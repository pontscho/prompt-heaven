#!/usr/bin/env python3
"""drawio → SVG renderer (pure stdlib, no third-party deps).

Renders the shape vocabulary used by the p:drawio skill:
  - Vertices: rounded rect, ellipse, rhombus, swimlane (horizontal=0/1),
    group, text (transparent/borderless label), shape=mxgraph.flowchart.document,
    shape=cylinder3
  - Edges: orthogonal Manhattan routing, arrow heads, optional label background
  - Labels: <b>, <i>, <font color="">, <br>, &#xa; — rendered as native SVG <text>
    (markdown-safe; no <foreignObject>)

Out of scope (intentionally): mxgraph.* stencil libraries (AWS/Azure/Cisco/...),
ELK auto-layout, bezier edge curves. The .drawio source remains the editable
master; this script only produces a static SVG snapshot.

CLI:
  python3 render.py INPUT.drawio OUTPUT.svg
"""

from __future__ import annotations

import html
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants tuned to match drawio Desktop default rendering
# ---------------------------------------------------------------------------
DEFAULT_FONT = "Helvetica, Arial, sans-serif"
DEFAULT_FONT_SIZE = 12
EDGE_FONT_SIZE = 11
LINE_HEIGHT = 16          # body line height at font-size 12
EDGE_LINE_HEIGHT = 14     # edge label line height at font-size 11
FONT_ASCENT = 12          # baseline offset from top of line box
EDGE_FONT_ASCENT = 11
CHAR_WIDTH = 7.0          # rough average proportional width in px (Helvetica 12)
MARGIN = 24               # viewBox margin
NODE_HPAD = 6             # horizontal text inset per side inside a vertex box
NODE_VPAD = 6             # vertical text inset (top+bottom) inside a vertex box


def estimate_line_width(segs: List[Dict[str, object]]) -> float:
    """Estimated rendered width (px) of one parsed line's text.

    Single source of truth shared by node-text wrapping and the edge-label
    background box (so wrap decisions and bg sizing stay consistent).
    """
    return sum(len(str(seg['text'])) for seg in segs) * CHAR_WIDTH


def estimate_text_width(text: str) -> float:
    return len(text) * CHAR_WIDTH

# ---------------------------------------------------------------------------
# Style parsing
# ---------------------------------------------------------------------------

def parse_style(s: str) -> Dict[str, str]:
    """drawio style string 'k=v;k2=v2;keyword;' → dict.

    Bare keywords like 'ellipse' / 'rhombus' / 'swimlane' / 'group' become
    {keyword: 'true'} so we can probe with `'ellipse' in style`.
    """
    out: Dict[str, str] = {}
    for part in s.split(';'):
        part = part.strip()
        if not part:
            continue
        if '=' in part:
            k, v = part.split('=', 1)
            out[k.strip()] = v.strip()
        else:
            out[part] = 'true'
    return out

# ---------------------------------------------------------------------------
# Cell model
# ---------------------------------------------------------------------------

@dataclass
class Cell:
    id: str
    value: str = ""
    style: Dict[str, str] = field(default_factory=dict)
    is_vertex: bool = False
    is_edge: bool = False
    parent: str = "1"
    source: str = ""
    target: str = ""
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    abs_x: float = 0.0
    abs_y: float = 0.0
    # Edge geometry: author-supplied waypoints (absolute coords) the route
    # must pass through, between source and target.
    waypoints: List[Tuple[float, float]] = field(default_factory=list)
    _resolved: bool = False

    @property
    def cx(self) -> float:
        return self.abs_x + self.width / 2

    @property
    def cy(self) -> float:
        return self.abs_y + self.height / 2

    @property
    def left(self) -> float:
        return self.abs_x

    @property
    def right(self) -> float:
        return self.abs_x + self.width

    @property
    def top(self) -> float:
        return self.abs_y

    @property
    def bottom(self) -> float:
        return self.abs_y + self.height

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_drawio(path: str) -> Tuple[List[Cell], Dict[str, Cell]]:
    tree = ET.parse(path)
    root = tree.getroot()

    graph_root = root.find('.//mxGraphModel/root')
    if graph_root is None:
        graph_root = root.find('.//root')
    if graph_root is None:
        raise ValueError(f"No mxGraphModel/root found in {path}")

    cells: List[Cell] = []
    for el in graph_root.findall('mxCell'):
        c = Cell(
            id=el.get('id', ''),
            value=el.get('value') or '',
            style=parse_style(el.get('style') or ''),
            is_vertex=el.get('vertex') == '1',
            is_edge=el.get('edge') == '1',
            parent=el.get('parent') or '1',
            source=el.get('source') or '',
            target=el.get('target') or '',
        )
        geom = el.find('mxGeometry')
        if geom is not None:
            c.x = float(geom.get('x', 0))
            c.y = float(geom.get('y', 0))
            c.width = float(geom.get('width', 0))
            c.height = float(geom.get('height', 0))
            # Edge waypoints: <Array as="points"><mxPoint x=".." y=".."/>...</Array>
            arr = geom.find("Array[@as='points']")
            if arr is not None:
                for mp in arr.findall('mxPoint'):
                    c.waypoints.append((float(mp.get('x', 0)), float(mp.get('y', 0))))
        cells.append(c)

    by_id = {c.id: c for c in cells}

    def resolve(c: Cell) -> None:
        if c._resolved or c.is_edge:
            return
        if c.parent in ('0', '1') or c.parent not in by_id:
            c.abs_x, c.abs_y = c.x, c.y
        else:
            p = by_id[c.parent]
            resolve(p)
            c.abs_x, c.abs_y = p.abs_x + c.x, p.abs_y + c.y
        c._resolved = True

    for c in cells:
        resolve(c)

    return cells, by_id

# ---------------------------------------------------------------------------
# Label parsing — turn HTML-flavored value into [[segment, ...], ...] lines
# ---------------------------------------------------------------------------

# Matches an opening or closing tag: <b>, </b>, <i>, </i>, <font color="...">, </font>
_TAG_RE = re.compile(
    r'<(/?)\s*(b|i|font)(?:\s+color\s*=\s*"([^"]*)")?\s*>',
    re.IGNORECASE,
)


def _split_lines(label: str) -> List[str]:
    s = re.sub(r'<br\s*/?>', '\n', label, flags=re.IGNORECASE)
    s = s.replace('&#xa;', '\n').replace('&#10;', '\n')
    return s.split('\n')


def _parse_line(raw: str) -> List[Dict[str, object]]:
    """One line → list of {text, bold, italic, color}. Decodes XML entities."""
    out: List[Dict[str, object]] = []
    pos = 0
    bold = False
    italic = False
    color: Optional[str] = None

    def emit(text: str) -> None:
        if not text:
            return
        out.append({'text': html.unescape(text), 'bold': bold, 'italic': italic, 'color': color})

    for m in _TAG_RE.finditer(raw):
        emit(raw[pos:m.start()])
        is_close = bool(m.group(1))
        tag = m.group(2).lower()
        if tag == 'b':
            bold = not is_close
        elif tag == 'i':
            italic = not is_close
        elif tag == 'font':
            color = None if is_close else (m.group(3) or None)
        pos = m.end()
    emit(raw[pos:])
    return out


def parse_label(label: str) -> List[List[Dict[str, object]]]:
    if not label:
        return []
    return [_parse_line(line) for line in _split_lines(label)]

# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

def _line_text(segs: List[Dict[str, object]]) -> str:
    return ''.join(str(seg['text']) for seg in segs)


def _wrap_lines(lines: List[List[Dict[str, object]]], inner_w: float,
                node_label: str = '', allow_wrap: bool = True,
                quiet: bool = False) -> List[List[Dict[str, object]]]:
    """Width-based greedy word-wrap, preserving each author line independently.

    Each author line (already split on <br>/&#xa;) is wrapped onto as many
    sub-lines as needed so each sub-line's estimated rendered width fits within
    `inner_w`. Breaks happen on spaces only (greedy). A single word wider than
    `inner_w` is left intact (cannot be broken) but still centered by the caller.

    Returns the (possibly longer) list of styled lines. Overflow / wrap events
    are reported to stderr so a blind author knows which boxes were adjusted.
    When `allow_wrap` is False (whiteSpace=nowrap) the line is NOT wrapped, but
    an overflow WARNING is still emitted.
    """
    if inner_w <= 0:
        return lines

    wrapped: List[List[Dict[str, object]]] = []
    for line_segs in lines:
        if not line_segs:
            wrapped.append([])
            continue

        line_w = estimate_line_width(line_segs)
        if line_w <= inner_w:
            wrapped.append(line_segs)
            continue

        raw = _line_text(line_segs)
        if not allow_wrap:
            if not quiet:
                print(f'WARN: node {node_label!r} line "{raw}" exceeds inner width '
                      f'({line_w:.0f}px > {inner_w:.0f}px) -> nowrap, left overflowing',
                      file=sys.stderr)
            wrapped.append(line_segs)
            continue

        # Greedy word-wrap on a (char, style) stream so styling survives breaks.
        char_stream: List[Tuple[str, Dict[str, object]]] = []
        for seg in line_segs:
            style_attrs = {'bold': seg['bold'], 'italic': seg['italic'], 'color': seg['color']}
            for ch in str(seg['text']):
                char_stream.append((ch, style_attrs))

        sub_lines: List[List[Tuple[str, Dict[str, object]]]] = []
        current: List[Tuple[str, Dict[str, object]]] = []
        last_break = -1  # index in `current` of the last space
        for ch, st in char_stream:
            current.append((ch, st))
            if ch == ' ':
                last_break = len(current) - 1
            if len(current) * CHAR_WIDTH > inner_w:
                if last_break > 0:
                    head = current[:last_break]
                    tail = current[last_break + 1:]  # drop the breaking space
                    sub_lines.append(head)
                    current = tail
                    last_break = -1
                    # Recompute last_break for the carried-over tail.
                    for j, (c2, _s2) in enumerate(current):
                        if c2 == ' ':
                            last_break = j
                # else: a single word longer than inner_w — keep accumulating,
                # it cannot be broken; it will be left intact (centered).
        if current:
            sub_lines.append(current)

        k = len([s for s in sub_lines if s]) or 1
        if not quiet:
            print(f'WARN: node {node_label!r} line "{raw}" exceeds inner width '
                  f'({line_w:.0f}px > {inner_w:.0f}px) -> wrapped to {k} lines',
                  file=sys.stderr)
        for s in sub_lines:
            wrapped.append(_collapse_stream(s))
    return wrapped


def _collapse_stream(stream: List[Tuple[str, Dict[str, object]]]) -> List[Dict[str, object]]:
    """[(char, style), ...] → [{text, bold, italic, color}, ...] merged by style."""
    out: List[Dict[str, object]] = []
    if not stream:
        return out
    cur_text = stream[0][0]
    cur_style = stream[0][1]
    for ch, st in stream[1:]:
        if st == cur_style:
            cur_text += ch
        else:
            out.append({'text': cur_text, **cur_style})
            cur_text = ch
            cur_style = st
    out.append({'text': cur_text, **cur_style})
    return out

# ---------------------------------------------------------------------------
# SVG primitives
# ---------------------------------------------------------------------------

def esc(text: str) -> str:
    return (text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))


def _fill_stroke(style: Dict[str, str]) -> str:
    fill = style.get('fillColor', '#ffffff')
    stroke = style.get('strokeColor', '#000000')
    sw = style.get('strokeWidth', '1')
    fill_attr = 'fill="none"' if fill.lower() == 'none' else f'fill="{fill}"'
    return f'{fill_attr} stroke="{stroke}" stroke-width="{sw}"'


def _dash_attr(style: Dict[str, str]) -> str:
    return ' stroke-dasharray="6,4"' if style.get('dashed') == '1' else ''

# ---------------------------------------------------------------------------
# Vertex shape renderers
# ---------------------------------------------------------------------------

def render_vertex(c: Cell) -> str:
    """Shape only — labels rendered separately so they can sit on top."""
    s = c.style
    if 'group' in s:
        return ''  # invisible container

    if 'text' in s:
        # drawio 'text' shape is transparent + borderless by default. Without
        # this branch it falls through to the generic rect renderer below,
        # which paints a white box with a black border (the _fill_stroke
        # defaults). Only emit a rect when the author set an explicit fill or
        # stroke; otherwise the label is rendered on its own (render_vertex_label).
        fill = s.get('fillColor', 'none')
        stroke = s.get('strokeColor', 'none')
        if fill.lower() == 'none' and stroke.lower() == 'none':
            return ''
        rounded = s.get('rounded') == '1'
        rx = ' rx="6" ry="6"' if rounded else ''
        fill_attr = 'fill="none"' if fill.lower() == 'none' else f'fill="{fill}"'
        stroke_attr = 'stroke="none"' if stroke.lower() == 'none' else f'stroke="{stroke}"'
        sw = s.get('strokeWidth', '1')
        return (f'<rect x="{c.left}" y="{c.top}" width="{c.width}" '
                f'height="{c.height}"{rx} {fill_attr} {stroke_attr} '
                f'stroke-width="{sw}"{_dash_attr(s)}/>')

    if 'ellipse' in s:
        return f'<ellipse cx="{c.cx}" cy="{c.cy}" rx="{c.width/2}" ry="{c.height/2}" {_fill_stroke(s)}{_dash_attr(s)}/>'

    if 'rhombus' in s:
        pts = f'{c.cx},{c.top} {c.right},{c.cy} {c.cx},{c.bottom} {c.left},{c.cy}'
        return f'<polygon points="{pts}" {_fill_stroke(s)}{_dash_attr(s)}/>'

    if 'swimlane' in s:
        return _render_swimlane(c)

    shape = s.get('shape', '')
    if shape == 'mxgraph.flowchart.document':
        return _render_document(c)
    if shape in ('cylinder3', 'cylinder'):
        return _render_cylinder(c)

    rounded = s.get('rounded') == '1'
    rx = ' rx="6" ry="6"' if rounded else ''
    return f'<rect x="{c.left}" y="{c.top}" width="{c.width}" height="{c.height}"{rx} {_fill_stroke(s)}{_dash_attr(s)}/>'


def _render_swimlane(c: Cell) -> str:
    s = c.style
    start_size = float(s.get('startSize', 30))
    horizontal = s.get('horizontal', '1') != '0'
    stroke = s.get('strokeColor', '#000000')
    parts = [
        f'<rect x="{c.left}" y="{c.top}" width="{c.width}" height="{c.height}" {_fill_stroke(s)}{_dash_attr(s)}/>',
    ]
    # Title divider
    if horizontal:
        parts.append(
            f'<line x1="{c.left}" y1="{c.top+start_size}" x2="{c.right}" y2="{c.top+start_size}" '
            f'stroke="{stroke}" stroke-width="1"{_dash_attr(s)}/>'
        )
    else:
        parts.append(
            f'<line x1="{c.left+start_size}" y1="{c.top}" x2="{c.left+start_size}" y2="{c.bottom}" '
            f'stroke="{stroke}" stroke-width="1"{_dash_attr(s)}/>'
        )
    return '\n'.join(parts)


def _render_document(c: Cell) -> str:
    s = c.style
    wave = min(c.height * 0.10, 12)
    x, y, w, h = c.left, c.top, c.width, c.height
    # Top edge straight; bottom edge sinusoidal
    path = (
        f'M {x} {y} '
        f'L {x+w} {y} '
        f'L {x+w} {y+h-wave} '
        f'Q {x+w*0.75} {y+h+wave} {x+w*0.5} {y+h-wave/2} '
        f'T {x} {y+h-wave} '
        f'Z'
    )
    return f'<path d="{path}" {_fill_stroke(s)}/>'


def _render_cylinder(c: Cell) -> str:
    s = c.style
    stroke = s.get('strokeColor', '#000000')
    lip = min(c.height * 0.18, 16)
    x, y, w, h = c.left, c.top, c.width, c.height
    cx = c.cx
    # Body silhouette with closed bottom curve; top will be drawn as overlay ellipse
    body = (
        f'M {x} {y+lip/2} '
        f'L {x} {y+h-lip/2} '
        f'A {w/2} {lip/2} 0 0 0 {x+w} {y+h-lip/2} '
        f'L {x+w} {y+lip/2} '
        f'A {w/2} {lip/2} 0 0 0 {x} {y+lip/2} '
        f'Z'
    )
    top_lid = (
        f'<ellipse cx="{cx}" cy="{y+lip/2}" rx="{w/2}" ry="{lip/2}" '
        f'fill="none" stroke="{stroke}" stroke-width="1"/>'
    )
    return f'<path d="{body}" {_fill_stroke(s)}/>\n{top_lid}'

# ---------------------------------------------------------------------------
# Label renderers
# ---------------------------------------------------------------------------

def render_vertex_label(c: Cell) -> str:
    """Label rendered INSIDE the vertex bounds, honoring align/verticalAlign/spacing*."""
    if not c.value:
        return ''
    if 'group' in c.style:
        return ''

    s = c.style
    is_swimlane = 'swimlane' in s
    if is_swimlane:
        return _render_swimlane_title(c)

    align = s.get('align', 'center')
    valign = s.get('verticalAlign', 'middle')
    pad_l = float(s.get('spacingLeft', 0))
    pad_r = float(s.get('spacingRight', 0))
    pad_t = float(s.get('spacingTop', 0))
    pad_b = float(s.get('spacingBottom', 0))

    inner_w = max(c.width - pad_l - pad_r - 2 * NODE_HPAD, 1)
    allow_wrap = s.get('whiteSpace') != 'nowrap'

    lines = parse_label(c.value)
    # Wrap silently here — the fit pre-pass (fit_vertex_text) already emitted
    # the WARN diagnostics and grew the box height to fit these wrapped lines.
    lines = _wrap_lines(lines, inner_w, node_label='', allow_wrap=allow_wrap, quiet=True)
    if not lines:
        return ''

    if align == 'left':
        text_x = c.left + pad_l
        anchor = 'start'
    elif align == 'right':
        text_x = c.right - pad_r
        anchor = 'end'
    else:
        text_x = c.cx
        anchor = 'middle'

    n = len(lines)
    total_h = n * LINE_HEIGHT
    if valign == 'top':
        first_y = c.top + pad_t + FONT_ASCENT
    elif valign == 'bottom':
        first_y = c.bottom - pad_b - total_h + FONT_ASCENT
    else:
        first_y = c.cy - total_h / 2 + FONT_ASCENT

    global_color = s.get('fontColor', '#000000')
    fs_int = int(s.get('fontStyle', 0) or 0)
    g_bold = bool(fs_int & 1)
    g_italic = bool(fs_int & 2)

    out: List[str] = []
    for i, segs in enumerate(lines):
        if not segs:
            continue
        y = first_y + i * LINE_HEIGHT
        tspans: List[str] = []
        for seg in segs:
            attrs = [f'fill="{seg["color"] or global_color}"']
            if seg['bold'] or g_bold:
                attrs.append('font-weight="bold"')
            if seg['italic'] or g_italic:
                attrs.append('font-style="italic"')
            tspans.append(f'<tspan {" ".join(attrs)}>{esc(seg["text"])}</tspan>')  # type: ignore[arg-type]
        out.append(
            f'<text x="{text_x}" y="{y}" text-anchor="{anchor}" '
            f'font-family="{DEFAULT_FONT}" font-size="{DEFAULT_FONT_SIZE}">'
            f'{"".join(tspans)}</text>'
        )
    return '\n'.join(out)


def _label_snippet(c: Cell, maxlen: int = 40) -> str:
    """A short, single-line identifier for diagnostics: prefer the cell id, fall
    back to a flattened label snippet."""
    if c.id and not c.id.isdigit():
        return c.id
    flat = ' '.join(_line_text(line) for line in parse_label(c.value)).strip()
    if len(flat) > maxlen:
        flat = flat[:maxlen - 1] + '…'
    return c.id or flat


def fit_vertex_text(cells: List[Cell]) -> None:
    """Pre-pass: for every leaf vertex, wrap its label to the box inner width and
    grow the box height (downward, top-left anchor fixed) so all wrapped lines
    fit. Mutates cell.height in place. Emits WARN diagnostics to stderr.

    Containers (group/swimlane) and borderless/transparent text labels are left
    alone — they are not fixed-width boxes the author sized for the text.
    """
    for c in cells:
        if not c.is_vertex or c.id in ('0', '1'):
            continue
        s = c.style
        if 'group' in s or 'swimlane' in s or s.get('container') == '1':
            continue
        if not c.value:
            continue
        if c.width <= 0 or c.height <= 0:
            continue
        # Transparent borderless text labels: no box to overflow / grow.
        if 'text' in s:
            fill = s.get('fillColor', 'none').lower()
            stroke = s.get('strokeColor', 'none').lower()
            if fill == 'none' and stroke == 'none':
                continue

        pad_l = float(s.get('spacingLeft', 0))
        pad_r = float(s.get('spacingRight', 0))
        pad_t = float(s.get('spacingTop', 0))
        pad_b = float(s.get('spacingBottom', 0))
        inner_w = max(c.width - pad_l - pad_r - 2 * NODE_HPAD, 1)
        allow_wrap = s.get('whiteSpace') != 'nowrap'

        label = _label_snippet(c)
        lines = parse_label(c.value)
        wrapped = _wrap_lines(lines, inner_w, node_label=label, allow_wrap=allow_wrap)

        n = len([ln for ln in wrapped if ln]) or len(wrapped)
        needed_h = n * LINE_HEIGHT + pad_t + pad_b + 2 * NODE_VPAD
        if needed_h > c.height + 0.5:
            old_h = c.height
            c.height = needed_h
            print(f'WARN: node {label!r} grown height {old_h:.0f} -> {c.height:.0f} '
                  f'to fit {n} lines', file=sys.stderr)


def _render_swimlane_title(c: Cell) -> str:
    s = c.style
    start_size = float(s.get('startSize', 30))
    horizontal = s.get('horizontal', '1') != '0'
    align = s.get('align', 'center')
    fs_int = int(s.get('fontStyle', 0) or 0)
    g_bold = bool(fs_int & 1)
    g_italic = bool(fs_int & 2)
    color = s.get('fontColor', '#000000')

    lines = parse_label(c.value)
    if not lines:
        return ''

    if horizontal:
        title_cx = c.cx
        title_cy = c.top + start_size / 2
        n = len(lines)
        first_y = title_cy - (n - 1) * LINE_HEIGHT / 2 + 4  # +4 ≈ ascent offset
        out: List[str] = []
        for i, segs in enumerate(lines):
            y = first_y + i * LINE_HEIGHT
            tspans: List[str] = []
            for seg in segs:
                attrs = [f'fill="{seg["color"] or color}"']
                if seg['bold'] or g_bold:
                    attrs.append('font-weight="bold"')
                if seg['italic'] or g_italic:
                    attrs.append('font-style="italic"')
                tspans.append(f'<tspan {" ".join(attrs)}>{esc(seg["text"])}</tspan>')  # type: ignore[arg-type]
            anchor = {'left': 'start', 'right': 'end'}.get(align, 'middle')
            if anchor == 'start':
                x = c.left + 8
            elif anchor == 'end':
                x = c.right - 8
            else:
                x = title_cx
            out.append(
                f'<text x="{x}" y="{y}" text-anchor="{anchor}" '
                f'font-family="{DEFAULT_FONT}" font-size="{DEFAULT_FONT_SIZE}">'
                f'{"".join(tspans)}</text>'
            )
        return '\n'.join(out)

    # horizontal=0: title rotated -90° at the left strip
    title_cx = c.left + start_size / 2
    title_cy = c.cy
    # Flatten all lines into one string (rotation makes multi-line awkward)
    parts: List[str] = []
    for segs in lines:
        for seg in segs:
            parts.append(str(seg['text']))
    text = ' '.join(p.strip() for p in parts if p.strip())
    if not text:
        return ''
    attrs = [f'fill="{color}"']
    if g_bold:
        attrs.append('font-weight="bold"')
    if g_italic:
        attrs.append('font-style="italic"')
    return (
        f'<text x="{title_cx}" y="{title_cy}" text-anchor="middle" '
        f'dominant-baseline="middle" '
        f'transform="rotate(-90 {title_cx} {title_cy})" '
        f'font-family="{DEFAULT_FONT}" font-size="{DEFAULT_FONT_SIZE}" '
        f'{" ".join(attrs)}>{esc(text)}</text>'
    )

# ---------------------------------------------------------------------------
# Edge routing + rendering
# ---------------------------------------------------------------------------

Pt = Tuple[float, float]
Box = Tuple[float, float, float, float]  # (x, y, w, h)

# How far a stub leaves a node before the orthogonal routing turns. Also the
# clearance kept between routed lanes / labels and node boxes.
STUB = 20.0
LANE_GAP = 18.0           # perpendicular spacing between parallel edge lanes
OBSTACLE_PAD = 4.0        # min clearance an auto-route keeps from a node box


def _node_box(c: Cell) -> Box:
    return (c.left, c.top, c.width, c.height)


def _collidable(c: Cell) -> bool:
    """Vertices that form a visible box an edge must not cross."""
    if not c.is_vertex or c.id in ('0', '1'):
        return False
    s = c.style
    # Containers (groups, swimlanes, explicit container=1) are backdrops, not
    # solid obstacles — edges routinely run between their own children.
    if 'group' in s or 'swimlane' in s or s.get('container') == '1':
        return False
    if c.width <= 0 or c.height <= 0:
        return False
    # Transparent borderless text labels are not solid boxes.
    if 'text' in s:
        fill = s.get('fillColor', 'none').lower()
        stroke = s.get('strokeColor', 'none').lower()
        if fill == 'none' and stroke == 'none':
            return False
    return True


def _seg_hits_box(p1: Pt, p2: Pt, box: Box, pad: float = 0.0) -> bool:
    """True if the OPEN segment crosses INTO the (padded) box interior.

    Liang–Barsky clip. Segments merely touching/running along the boundary do
    not count (we shrink the test box by a hair).
    """
    bx, by, bw, bh = box
    x0, y0 = bx - pad + 0.01, by - pad + 0.01
    x1, y1 = bx + bw + pad - 0.01, by + bh + pad - 0.01
    if x1 <= x0 or y1 <= y0:
        return False
    ax, ay = p1
    cx, cy = p2
    dx, dy = cx - ax, cy - ay
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
        else:
            r = q / p
            if p < 0:
                if r > t1:
                    return False
                t0 = max(t0, r)
            else:
                if r < t0:
                    return False
                t1 = min(t1, r)
    return t1 - t0 > 1e-9


def _route_hits(pts: List[Pt], boxes: List[Box], pad: float = 0.0) -> bool:
    for i in range(len(pts) - 1):
        for b in boxes:
            if _seg_hits_box(pts[i], pts[i + 1], b, pad):
                return True
    return False


def _side_point(c: Cell, side: str, t: float = 0.5) -> Pt:
    """A point on one side of the box; t in [0,1] along that side."""
    if side == 'top':
        return (c.left + t * c.width, c.top)
    if side == 'bottom':
        return (c.left + t * c.width, c.bottom)
    if side == 'left':
        return (c.left, c.top + t * c.height)
    return (c.right, c.top + t * c.height)  # right


def _pick_side(c: Cell, toward: Pt) -> str:
    """Choose the box side whose outward normal best faces `toward`."""
    dx = toward[0] - c.cx
    dy = toward[1] - c.cy
    if abs(dx) >= abs(dy):
        return 'right' if dx >= 0 else 'left'
    return 'bottom' if dy >= 0 else 'top'


def _outward(side: str, p: Pt, dist: float) -> Pt:
    if side == 'top':
        return (p[0], p[1] - dist)
    if side == 'bottom':
        return (p[0], p[1] + dist)
    if side == 'left':
        return (p[0] - dist, p[1])
    return (p[0] + dist, p[1])


def _attach(c: Cell, fx: Optional[float], fy: Optional[float], toward: Pt) -> Tuple[Pt, str]:
    """Resolve an attach point + the side it lives on.

    If explicit exit/entry fractions are given, honor them (snap to the nearest
    side for the stub direction). Otherwise auto-pick the facing side.
    """
    if fx is not None and fy is not None:
        px = c.left + fx * c.width
        py = c.top + fy * c.height
        # Determine which side this fraction sits on (for the stub direction).
        d_left, d_right = fx, 1 - fx
        d_top, d_bottom = fy, 1 - fy
        m = min(d_left, d_right, d_top, d_bottom)
        if m == d_left:
            side = 'left'
        elif m == d_right:
            side = 'right'
        elif m == d_top:
            side = 'top'
        else:
            side = 'bottom'
        return (px, py), side
    side = _pick_side(c, toward)
    return _side_point(c, side), side


def _orthogonalize(anchors: List[Pt]) -> List[Pt]:
    """Insert L-bends so every consecutive pair is axis-aligned.

    Between two diagonally-offset anchors we insert one elbow. The elbow keeps
    the leg leaving the previous point along the dominant axis of travel.
    """
    if len(anchors) < 2:
        return anchors
    out: List[Pt] = [anchors[0]]
    for i in range(1, len(anchors)):
        ax, ay = out[-1]
        bx, by = anchors[i]
        if abs(ax - bx) < 1e-6 or abs(ay - by) < 1e-6:
            out.append((bx, by))
            continue
        # Insert an elbow. Prefer to travel along the longer axis first so the
        # bend lands closer to the target side.
        if abs(bx - ax) >= abs(by - ay):
            out.append((bx, ay))
        else:
            out.append((ax, by))
        out.append((bx, by))
    # Drop consecutive duplicates.
    dedup: List[Pt] = [out[0]]
    for p in out[1:]:
        if abs(p[0] - dedup[-1][0]) > 1e-6 or abs(p[1] - dedup[-1][1]) > 1e-6:
            dedup.append(p)
    return dedup


def _connectors(sp: Pt, ss: str, tp: Pt, ts: str, obstacles: List[Box]) -> List[List[Pt]]:
    """Candidate anchor lists between two attach points + their stub directions."""
    s_stub = _outward(ss, sp, STUB)
    t_stub = _outward(ts, tp, STUB)
    sx, sy = s_stub
    tx, ty = t_stub
    cands: List[List[Pt]] = []

    # Simple one-bend connectors between the stub ends.
    cands.append([sp, s_stub, (sx, ty), t_stub, tp])
    cands.append([sp, s_stub, (tx, sy), t_stub, tp])

    # Z-routes through a sweeping mid line.
    lo, hi = min(sy, ty), max(sy, ty)
    for frac in (0.5, 0.35, 0.65, 0.2, 0.8):
        midy = lo + (hi - lo) * frac
        cands.append([sp, s_stub, (sx, midy), (tx, midy), t_stub, tp])
    lo, hi = min(sx, tx), max(sx, tx)
    for frac in (0.5, 0.35, 0.65, 0.2, 0.8):
        midx = lo + (hi - lo) * frac
        cands.append([sp, s_stub, (midx, sy), (midx, ty), t_stub, tp])

    # Detour around the whole obstacle field on each flank (for back-edges).
    if obstacles:
        ox0 = min(b[0] for b in obstacles)
        ox1 = max(b[0] + b[2] for b in obstacles)
        oy0 = min(b[1] for b in obstacles)
        oy1 = max(b[1] + b[3] for b in obstacles)
        for lane in (ox1 + STUB, ox0 - STUB):
            cands.append([sp, s_stub, (lane, sy), (lane, ty), t_stub, tp])
        for lane in (oy1 + STUB, oy0 - STUB):
            cands.append([sp, s_stub, (sx, lane), (tx, lane), t_stub, tp])
    return cands


# Order in which to try sides: the geometrically-facing side first, then the
# orthogonal sides, then the opposite side last.
_SIDE_ALTS = {
    'top': ['top', 'left', 'right', 'bottom'],
    'bottom': ['bottom', 'right', 'left', 'top'],
    'left': ['left', 'top', 'bottom', 'right'],
    'right': ['right', 'bottom', 'top', 'left'],
}


def _auto_route(src: Cell, tgt: Cell,
                sp: Pt, ss: str, s_fixed: bool,
                tp: Pt, ts: str, t_fixed: bool,
                obstacles: List[Box]) -> List[Pt]:
    """Obstacle-avoiding orthogonal route.

    When an endpoint's attach side is NOT pinned by exit/entry fractions, try
    several candidate sides so the route can both leave and arrive cleanly. The
    route must not cross any OTHER node box, and must not slice through its own
    source/target box (only touch at the attach point).
    """
    own = [_node_box(src), _node_box(tgt)]
    others = [b for b in obstacles if b not in own]

    s_sides = [ss] if s_fixed else _SIDE_ALTS[ss]
    t_sides = [ts] if t_fixed else _SIDE_ALTS[ts]

    best = None
    best_cost = None
    for sside in s_sides:
        sp2 = sp if s_fixed else _side_point(src, sside)
        for tside in t_sides:
            tp2 = tp if t_fixed else _side_point(tgt, tside)
            for cand in _connectors(sp2, sside, tp2, tside, obstacles):
                route = _orthogonalize(cand)
                # Hard constraint: never cross own boxes mid-route (touching the
                # attach endpoint is fine — _seg_hits_box ignores boundary grazing).
                own_hit = _route_hits(route, own, pad=0.0)
                hits = sum(1 for i in range(len(route) - 1)
                           for b in others if _seg_hits_box(route[i], route[i + 1], b, OBSTACLE_PAD))
                if not own_hit and hits == 0:
                    return route
                # Fallback cost: own-box crossing is heavily penalized; then
                # other-box hits; mild bias toward fewer bends.
                cost = (100 if own_hit else 0) + hits + 0.01 * len(route)
                if best_cost is None or cost < best_cost:
                    best_cost = cost
                    best = route
    return best if best is not None else _orthogonalize([sp, tp])


def _lane_offset(pts: List[Pt], idx: int, count: int) -> List[Pt]:
    """Shift interior waypoints of a route perpendicular to its main axis so
    parallel edges between the same node pair occupy distinct lanes.

    idx in [0, count). The endpoints (attach points) are left untouched so the
    arrows still land on the node boundary; only interior bends move.
    """
    if count <= 1 or len(pts) < 3:
        return pts
    # Centered offset: e.g. count=2 -> [-0.5, +0.5]*GAP
    off = (idx - (count - 1) / 2.0) * LANE_GAP
    # Main axis = orientation of the longest interior run.
    # Determine if route is predominantly horizontal or vertical in its middle.
    mid = pts[1:-1]
    horiz = 0.0
    vert = 0.0
    for i in range(len(pts) - 1):
        dx = abs(pts[i + 1][0] - pts[i][0])
        dy = abs(pts[i + 1][1] - pts[i][1])
        horiz += dx
        vert += dy
    out = [pts[0]]
    if horiz >= vert:
        # Mainly horizontal -> offset interior points in Y.
        for p in mid:
            out.append((p[0], p[1] + off))
    else:
        for p in mid:
            out.append((p[0] + off, p[1]))
    out.append(pts[-1])
    return _orthogonalize(out)


def _edge_endpoints(c: Cell, src: Cell, tgt: Cell) -> Tuple[Pt, str, bool, Pt, str, bool]:
    """Resolve source/target attach points honoring exit/entry fractions and
    waypoints (attach toward the first/last waypoint when present).

    Returns (src_pt, src_side, src_fixed, tgt_pt, tgt_side, tgt_fixed) where
    *_fixed means the author pinned the side via exit/entry fractions.
    """
    s = c.style

    def frac(key: str) -> Optional[float]:
        v = s.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    ex, ey = frac('exitX'), frac('exitY')
    nx, ny = frac('entryX'), frac('entryY')

    src_toward = c.waypoints[0] if c.waypoints else (tgt.cx, tgt.cy)
    tgt_toward = c.waypoints[-1] if c.waypoints else (src.cx, src.cy)

    sp, ss = _attach(src, ex, ey, src_toward)
    tp, ts = _attach(tgt, nx, ny, tgt_toward)
    s_fixed = ex is not None and ey is not None
    t_fixed = nx is not None and ny is not None
    return sp, ss, s_fixed, tp, ts, t_fixed


def compute_route(c: Cell, src: Cell, tgt: Cell, obstacles: List[Box],
                  lane_idx: int, lane_count: int) -> List[Pt]:
    sp, ss, s_fixed, tp, ts, t_fixed = _edge_endpoints(c, src, tgt)

    if c.waypoints:
        # Author-supplied waypoints take precedence: route through them.
        anchors = [sp]
        # Stub out of the source so the first leg leaves the box cleanly.
        anchors.append(_outward(ss, sp, STUB))
        anchors.extend(c.waypoints)
        anchors.append(_outward(ts, tp, STUB))
        anchors.append(tp)
        return _orthogonalize(anchors)

    route = _auto_route(src, tgt, sp, ss, s_fixed, tp, ts, t_fixed, obstacles)
    # Parallel-edge lane separation, but only when the author has NOT already
    # pinned distinct attach points via exit/entry fractions (those already
    # separate the lanes; an extra offset would add an ugly jog).
    if lane_count > 1 and not (s_fixed or t_fixed):
        route = _lane_offset(route, lane_idx, lane_count)
    return route


# ---------------------------------------------------------------------------
# Edge label placement
# ---------------------------------------------------------------------------

def _label_box_for(lines, mx: float, my: float) -> Box:
    n = len(lines)
    label_w = max((estimate_line_width(line) for line in lines), default=0.0) + 12
    label_h = n * EDGE_LINE_HEIGHT + 6
    return (mx - label_w / 2, my - label_h / 2, label_w, label_h)


def _boxes_overlap(a: Box, b: Box, pad: float = 0.0) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx - pad or bx + bw <= ax - pad or
                ay + ah <= by - pad or by + bh <= ay - pad)


def _seg_points(pts: List[Pt]):
    """Yield (midpoint, length, horizontal?) for each segment, longest first."""
    segs = []
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        L = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        segs.append((((x0 + x1) / 2, (y0 + y1) / 2), L, abs(x1 - x0) >= abs(y1 - y0), pts[i], pts[i + 1]))
    segs.sort(key=lambda t: -t[1])
    return segs


def _place_label(lines, pts: List[Pt], node_boxes: List[Box],
                 placed: List[Box]) -> Pt:
    """Find a label center on the edge whose bg box avoids node boxes and
    already-placed labels. Search along edge segments, then nudge perpendicular.
    """
    nudges = [0]
    for k in range(1, 9):
        step = k * (EDGE_LINE_HEIGHT * 0.75)
        nudges.extend([-step, step])

    candidates: List[Tuple[Pt, float]] = []  # (center, base_cost favoring centeredness)
    for (mid, L, horiz, a, b) in _seg_points(pts):
        for frac in (0.5, 0.4, 0.6, 0.3, 0.7):
            cx = a[0] + (b[0] - a[0]) * frac
            cy = a[1] + (b[1] - a[1]) * frac
            for d in nudges:
                # Prefer the longest segment, centered along it, minimal nudge.
                bias = abs(frac - 0.5) + abs(d) / 1000.0 - L / 100000.0
                if horiz:
                    candidates.append(((cx, cy + d), bias))
                else:
                    candidates.append(((cx + d, cy), bias))

    best = candidates[0][0] if candidates else (pts[len(pts) // 2])
    best_cost = None
    for ((mx, my), bias) in candidates:
        box = _label_box_for(lines, mx, my)
        cost = 0.0
        for nb in node_boxes:
            if _boxes_overlap(box, nb):
                cost += 10
        for pb in placed:
            if _boxes_overlap(box, pb, pad=2):
                cost += 10
        cost += bias  # tie-break toward centered, low-nudge, long-segment
        if cost < 1.0:  # no hard collisions
            return (mx, my)
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best = (mx, my)
    return best


def _render_edge_label(c: Cell, pts: List[Pt], node_boxes: List[Box],
                       placed: List[Box]) -> Tuple[str, Optional[Box]]:
    s = c.style
    lines = parse_label(c.value)
    if not lines:
        return '', None

    mx, my = _place_label(lines, pts, node_boxes, placed)
    box = _label_box_for(lines, mx, my)

    n = len(lines)
    parts: List[str] = []
    bg = s.get('labelBackgroundColor')
    if bg:
        parts.append(
            f'<rect class="edge-label-bg" x="{box[0]}" y="{box[1]}" '
            f'width="{box[2]}" height="{box[3]}" fill="{bg}" stroke="none"/>'
        )

    color = s.get('fontColor', '#000000')
    fs_int = int(s.get('fontStyle', 0) or 0)
    g_bold = bool(fs_int & 1)
    g_italic = bool(fs_int & 2)

    first_y = my - (n - 1) * EDGE_LINE_HEIGHT / 2 + 3
    for i, segs in enumerate(lines):
        if not segs:
            continue
        y = first_y + i * EDGE_LINE_HEIGHT
        tspans: List[str] = []
        for seg in segs:
            attrs = [f'fill="{seg["color"] or color}"']
            if seg['bold'] or g_bold:
                attrs.append('font-weight="bold"')
            if seg['italic'] or g_italic:
                attrs.append('font-style="italic"')
            tspans.append(f'<tspan {" ".join(attrs)}>{esc(str(seg["text"]))}</tspan>')
        parts.append(
            f'<text x="{mx}" y="{y}" text-anchor="middle" '
            f'font-family="{DEFAULT_FONT}" font-size="{EDGE_FONT_SIZE}">'
            f'{"".join(tspans)}</text>'
        )

    return '\n'.join(parts), box


def render_edges(cells: List[Cell], by_id: Dict[str, Cell]) -> Tuple[str, Optional[Box]]:
    """Render all edges together so parallel-edge lanes and label collisions can
    be resolved globally. Returns (svg, extent_box) where extent_box bounds all
    drawn edge geometry + label rects (for viewBox expansion)."""
    edges = [c for c in cells if c.is_edge and c.source in by_id and c.target in by_id]
    node_boxes = [_node_box(c) for c in cells if _collidable(c)]

    # Group parallel edges by unordered node pair for lane separation.
    groups: Dict[frozenset, List[Cell]] = {}
    for e in edges:
        groups.setdefault(frozenset((e.source, e.target)), []).append(e)
    lane_of: Dict[str, Tuple[int, int]] = {}
    for key, grp in groups.items():
        # Stable order by edge id so lane assignment is deterministic.
        grp_sorted = sorted(grp, key=lambda e: e.id)
        for i, e in enumerate(grp_sorted):
            lane_of[e.id] = (i, len(grp_sorted))

    out: List[str] = []
    placed_labels: List[Box] = []
    ext = [float('inf'), float('inf'), float('-inf'), float('-inf')]  # minx miny maxx maxy

    def grow(x: float, y: float) -> None:
        ext[0] = min(ext[0], x)
        ext[1] = min(ext[1], y)
        ext[2] = max(ext[2], x)
        ext[3] = max(ext[3], y)

    for e in edges:
        src = by_id[e.source]
        tgt = by_id[e.target]
        s = e.style
        stroke = s.get('strokeColor', '#000000')
        sw = s.get('strokeWidth', '1')
        dash = _dash_attr(s)

        lane_idx, lane_count = lane_of.get(e.id, (0, 1))
        pts = compute_route(e, src, tgt, node_boxes, lane_idx, lane_count)
        if len(pts) < 2:
            continue
        for (px, py) in pts:
            grow(px, py)
        d = f'M {pts[0][0]} {pts[0][1]} ' + ' '.join(f'L {x} {y}' for x, y in pts[1:])

        marker_id = f'arr-{e.id}'
        end_arrow = s.get('endArrow', 'classic')
        if end_arrow == 'none':
            marker = ''
            marker_ref = ''
        else:
            marker = (
                f'<defs><marker id="{marker_id}" viewBox="0 0 10 10" refX="9" refY="5" '
                f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
                f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{stroke}"/></marker></defs>'
            )
            marker_ref = f' marker-end="url(#{marker_id})"'

        out.append(
            f'{marker}'
            f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{sw}"{dash}{marker_ref}/>'
        )

        if e.value:
            # Exclude the edge's own endpoints from label-vs-node avoidance so a
            # short edge can still sit its label near its own boxes if needed,
            # but keep all other boxes as hard constraints.
            label_svg, box = _render_edge_label(e, pts, node_boxes, placed_labels)
            if label_svg:
                out.append(label_svg)
            if box is not None:
                placed_labels.append(box)
                grow(box[0], box[1])
                grow(box[0] + box[2], box[1] + box[3])

    if ext[0] == float('inf'):
        return '\n'.join(out), None
    return '\n'.join(out), (ext[0], ext[1], ext[2] - ext[0], ext[3] - ext[1])

# ---------------------------------------------------------------------------
# Bounds + main render
# ---------------------------------------------------------------------------

def compute_bounds(cells: List[Cell]) -> Tuple[float, float, float, float]:
    verts = [c for c in cells if c.is_vertex and c.id not in ('0', '1')]
    if not verts:
        return 0, 0, 200, 200
    return (
        min(c.left for c in verts),
        min(c.top for c in verts),
        max(c.right for c in verts),
        max(c.bottom for c in verts),
    )


def render(input_path: str, output_path: str) -> None:
    cells, by_id = parse_drawio(input_path)

    # Fit node text BEFORE anything else: wrapping long label lines and growing
    # box heights changes node geometry, which edge routing and the viewBox must
    # both observe. Mutates cell.height in place; emits WARN diagnostics.
    fit_vertex_text(cells)

    # Render edges first: routing may place geometry (stubs, detour lanes,
    # label boxes) beyond the vertex bounds, so we must factor it into the
    # viewBox. The SVG draw order still puts edges last (see below).
    edges_svg, edge_ext = render_edges(cells, by_id)

    min_x, min_y, max_x, max_y = compute_bounds(cells)
    if edge_ext is not None:
        ex, ey, ew, eh = edge_ext
        min_x = min(min_x, ex)
        min_y = min(min_y, ey)
        max_x = max(max_x, ex + ew)
        max_y = max(max_y, ey + eh)
    min_x -= MARGIN
    min_y -= MARGIN
    max_x += MARGIN
    max_y += MARGIN
    w = max_x - min_x
    h = max_y - min_y

    parts: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{w}" height="{h}" viewBox="{min_x} {min_y} {w} {h}">'
        ),
        '<rect x="0" y="0" width="100%" height="100%" fill="white"/>',
    ]

    # Render order: containers first, then leaf vertices, then edges (so edges
    # cross over shapes but under nothing), then nothing — labels are emitted
    # inline with their owning shape so they stay on top of the shape fill.
    containers = []
    leaves = []
    for c in cells:
        if not c.is_vertex or c.id in ('0', '1'):
            continue
        if 'swimlane' in c.style or c.style.get('container') == '1' or 'group' in c.style:
            containers.append(c)
        else:
            leaves.append(c)

    for c in containers:
        parts.append(render_vertex(c))
        parts.append(render_vertex_label(c))
    for c in leaves:
        parts.append(render_vertex(c))
        parts.append(render_vertex_label(c))
    parts.append(edges_svg)

    parts.append('</svg>')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(p for p in parts if p))


def main(argv: List[str]) -> int:
    if len(argv) < 3:
        print('Usage: render.py INPUT.drawio OUTPUT.svg', file=sys.stderr)
        return 2
    render(argv[1], argv[2])
    print(f'Wrote {argv[2]}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
