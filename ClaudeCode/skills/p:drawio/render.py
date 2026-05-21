#!/usr/bin/env python3
"""drawio → SVG renderer (pure stdlib, no third-party deps).

Renders the shape vocabulary used by the p:drawio skill:
  - Vertices: rounded rect, ellipse, rhombus, swimlane (horizontal=0/1),
    group, shape=mxgraph.flowchart.document, shape=cylinder3
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
CHAR_WIDTH = 6.5          # rough average proportional width in px
MARGIN = 24               # viewBox margin

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

def _wrap_lines(lines: List[List[Dict[str, object]]], max_chars: int) -> List[List[Dict[str, object]]]:
    """Wrap each line to max_chars using greedy word-wrap, preserving segment styling.

    Segments may be split across wrapped lines (each sub-line keeps the original
    style attrs of the segment it came from).
    """
    if max_chars <= 0:
        return lines

    wrapped: List[List[Dict[str, object]]] = []
    for line_segs in lines:
        # Flatten to (char, style)
        char_stream: List[Tuple[str, Dict[str, object]]] = []
        for seg in line_segs:
            style_attrs = {'bold': seg['bold'], 'italic': seg['italic'], 'color': seg['color']}
            for ch in seg['text']:  # type: ignore[union-attr]
                char_stream.append((ch, style_attrs))

        if not char_stream:
            wrapped.append([])
            continue

        # Greedy line breaks at word boundaries
        current: List[Tuple[str, Dict[str, object]]] = []
        col = 0
        last_break = -1
        for ch, st in char_stream:
            current.append((ch, st))
            col += 1
            if ch == ' ':
                last_break = len(current) - 1
            if col >= max_chars:
                if last_break > 0 and last_break > len(current) - max_chars // 2:
                    head = current[:last_break]
                    tail = current[last_break + 1:]
                else:
                    head = current
                    tail = []
                wrapped.append(_collapse_stream(head))
                current = tail
                col = len(tail)
                last_break = -1
        if current:
            wrapped.append(_collapse_stream(current))
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

    inner_w = max(c.width - pad_l - pad_r, 1)
    max_chars = max(int(inner_w / CHAR_WIDTH), 1)

    lines = parse_label(c.value)
    lines = _wrap_lines(lines, max_chars)
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

def orthogonal_route(src: Cell, tgt: Cell) -> List[Tuple[float, float]]:
    sx, sy_t, sy_b = src.cx, src.top, src.bottom
    tx, ty_t, ty_b = tgt.cx, tgt.top, tgt.bottom
    sl, sr = src.left, src.right
    tl, tr = tgt.left, tgt.right
    sy_mid = src.cy
    ty_mid = tgt.cy

    if ty_t >= sy_b:
        if abs(tx - sx) < 2:
            return [(sx, sy_b), (sx, ty_t)]
        midy = (sy_b + ty_t) / 2
        return [(sx, sy_b), (sx, midy), (tx, midy), (tx, ty_t)]

    if ty_b <= sy_t:
        if abs(tx - sx) < 2:
            return [(sx, sy_t), (sx, ty_b)]
        midy = (sy_t + ty_b) / 2
        return [(sx, sy_t), (sx, midy), (tx, midy), (tx, ty_b)]

    if tl >= sr:
        midx = (sr + tl) / 2
        return [(sr, sy_mid), (midx, sy_mid), (midx, ty_mid), (tl, ty_mid)]
    if tr <= sl:
        midx = (sl + tr) / 2
        return [(sl, sy_mid), (midx, sy_mid), (midx, ty_mid), (tr, ty_mid)]

    # Overlapping boxes — fall back to straight center-to-center
    return [(sx, sy_mid), (tx, ty_mid)]


def render_edge(c: Cell, by_id: Dict[str, Cell]) -> str:
    if c.source not in by_id or c.target not in by_id:
        return ''
    src = by_id[c.source]
    tgt = by_id[c.target]
    s = c.style
    stroke = s.get('strokeColor', '#000000')
    sw = s.get('strokeWidth', '1')
    dash = _dash_attr(s)

    pts = orthogonal_route(src, tgt)
    if len(pts) < 2:
        return ''
    d = f'M {pts[0][0]} {pts[0][1]} ' + ' '.join(f'L {x} {y}' for x, y in pts[1:])

    marker_id = f'arr-{c.id}'
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

    edge_svg = (
        f'{marker}'
        f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{sw}"{dash}{marker_ref}/>'
    )

    if not c.value:
        return edge_svg

    label_svg = _render_edge_label(c, pts)
    return f'{edge_svg}\n{label_svg}'


def _render_edge_label(c: Cell, pts: List[Tuple[float, float]]) -> str:
    s = c.style
    lines = parse_label(c.value)
    if not lines:
        return ''

    # Midpoint of the longest segment (or geometric midpoint as fallback)
    longest_idx = 0
    longest_len = -1.0
    for i in range(len(pts) - 1):
        dx = pts[i + 1][0] - pts[i][0]
        dy = pts[i + 1][1] - pts[i][1]
        L = (dx * dx + dy * dy) ** 0.5
        if L > longest_len:
            longest_len = L
            longest_idx = i
    mx = (pts[longest_idx][0] + pts[longest_idx + 1][0]) / 2
    my = (pts[longest_idx][1] + pts[longest_idx + 1][1]) / 2

    n = len(lines)
    max_line_chars = max((sum(len(str(seg['text'])) for seg in line) for line in lines), default=0)
    label_w = max_line_chars * CHAR_WIDTH + 12
    label_h = n * EDGE_LINE_HEIGHT + 6

    parts: List[str] = []
    bg = s.get('labelBackgroundColor')
    if bg:
        parts.append(
            f'<rect x="{mx - label_w/2}" y="{my - label_h/2}" '
            f'width="{label_w}" height="{label_h}" fill="{bg}" stroke="none"/>'
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

    return '\n'.join(parts)

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
    min_x, min_y, max_x, max_y = compute_bounds(cells)
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
    for c in cells:
        if c.is_edge:
            parts.append(render_edge(c, by_id))

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
