---
name: p:drawio
description: Always use when user asks to create, generate, draw, or design a diagram, flowchart, architecture diagram, ER diagram, sequence diagram, class diagram, network diagram, mockup, wireframe, or UI sketch, or mentions draw.io, drawio, drawoi, .drawio files, or diagram export to PNG/SVG/PDF.
---

# Draw.io Diagram Skill

Generate draw.io diagrams as native `.drawio` files. Optionally export to PNG, SVG, or PDF with the diagram XML embedded (so the exported file remains editable in draw.io).

## How to create a diagram

1. **Generate draw.io XML** in mxGraphModel format for the requested diagram
2. **Write the XML** to a `.drawio` file in the current working directory using the Write tool
3. **If the user requested an export format** (png, svg, pdf), follow this fallback chain:
   - **draw.io CLI** (see below) — preferred when available. Exports with `--embed-diagram` so the result remains editable. Supports png, svg, pdf, jpg. After a successful export, delete the source `.drawio` file
   - **`render.py`** (bundled with this skill, see "Pure-Python SVG fallback" below) — runs when the draw.io CLI is absent **and** the requested format is `svg`. Pure stdlib, no install. Keep the source `.drawio` file alongside the `.svg` (the SVG has no embedded XML — the `.drawio` is the editable master)
   - **Neither available** → keep the `.drawio` file and tell the user: install the draw.io desktop app for png/pdf, or accept svg via the built-in `render.py` fallback
4. **Open the result** — the exported file if exported, or the `.drawio` file otherwise. If the open command fails, print the file path so the user can open it manually

## Choosing the output format

Check the user's request for a format preference. Examples:

- `/drawio create a flowchart` → `flowchart.drawio`
- `/drawio png flowchart for login` → `login-flow.drawio.png`
- `/drawio svg: ER diagram` → `er-diagram.drawio.svg`
- `/drawio pdf architecture overview` → `architecture-overview.drawio.pdf`

If no format is mentioned, just write the `.drawio` file and open it in draw.io. The user can always ask to export later.

### Supported export formats

| Format | Embed XML | Notes |
|--------|-----------|-------|
| `png` | Yes (`-e`) | Viewable everywhere, editable in draw.io |
| `svg` | Yes (`-e`) | Scalable, editable in draw.io |
| `pdf` | Yes (`-e`) | Printable, editable in draw.io |
| `jpg` | No | Lossy, no embedded XML support |

PNG, SVG, and PDF all support `--embed-diagram` — the exported file contains the full diagram XML, so opening it in draw.io recovers the editable diagram.

## draw.io CLI

The draw.io desktop app includes a command-line interface for exporting.

### Locating the CLI

First, detect the environment, then locate the CLI accordingly:

#### WSL2 (Windows Subsystem for Linux)

WSL2 is detected when `/proc/version` contains `microsoft` or `WSL`:

```bash
grep -qi microsoft /proc/version 2>/dev/null && echo "WSL2"
```

On WSL2, use the Windows draw.io Desktop executable via `/mnt/c/...`:

```bash
DRAWIO_CMD=`/mnt/c/Program Files/draw.io/draw.io.exe`
```

The backtick quoting is required to handle the space in `Program Files` in bash.

If draw.io is installed in a non-default location, check common alternatives:

```bash
# Default install path
`/mnt/c/Program Files/draw.io/draw.io.exe`

# Per-user install (if the above does not exist)
`/mnt/c/Users/$WIN_USER/AppData/Local/Programs/draw.io/draw.io.exe`
```

#### macOS

```bash
/Applications/draw.io.app/Contents/MacOS/draw.io
```

#### Linux (native)

```bash
drawio   # typically on PATH via snap/apt/flatpak
```

#### Windows (native, non-WSL2)

```
"C:\Program Files\draw.io\draw.io.exe"
```

Use `which drawio` (or `where draw.io` on Windows) to check if it's on PATH before falling back to the platform-specific path.

### Export command

```bash
drawio -x -f <format> -e -b 10 -o <output> <input.drawio>
```

**WSL2 example:**

```bash
`/mnt/c/Program Files/draw.io/draw.io.exe` -x -f png -e -b 10 -o diagram.drawio.png diagram.drawio
```

Key flags:
- `-x` / `--export`: export mode
- `-f` / `--format`: output format (png, svg, pdf, jpg)
- `-e` / `--embed-diagram`: embed diagram XML in the output (PNG, SVG, PDF only)
- `-o` / `--output`: output file path
- `-b` / `--border`: border width around diagram (default: 0)
- `-t` / `--transparent`: transparent background (PNG only)
- `-s` / `--scale`: scale the diagram size
- `--width` / `--height`: fit into specified dimensions (preserves aspect ratio)
- `-a` / `--all-pages`: export all pages (PDF only)
- `-p` / `--page-index`: select a specific page (1-based)

### Opening the result

| Environment | Command |
|-------------|---------|
| macOS | `open <file>` |
| Linux (native) | `xdg-open <file>` |
| WSL2 | `cmd.exe /c start "" "$(wslpath -w <file>)"` |
| Windows | `start <file>` |

**WSL2 notes:**
- `wslpath -w <file>` converts a WSL2 path (e.g. `/home/user/diagram.drawio`) to a Windows path (e.g. `C:\Users\...`). This is required because `cmd.exe` cannot resolve `/mnt/c/...` style paths.
- The empty string `""` after `start` is required to prevent `start` from interpreting the filename as a window title.

**WSL2 example:**

```bash
cmd.exe /c start "" "$(wslpath -w diagram.drawio)"
```

## Pure-Python SVG fallback (`render.py`)

When the draw.io CLI is not installed but the user wants an `.svg` export, fall back to the bundled `render.py` — a pure-stdlib script that parses the `.drawio` XML and emits SVG directly. No third-party packages, no Electron, no Chromium.

### When to use it

Use `render.py` when **all** of these hold:
- User asked for `svg` export (not png or pdf)
- `which drawio` / platform-specific path checks fail
- The diagram uses only the shapes this script supports (see below)

For `png` and `pdf`, still prefer the draw.io CLI — `render.py` only emits SVG. The user can convert SVG → PNG/PDF afterwards via `rsvg-convert`, `qlmanage`, browser print-to-PDF, or by installing `cairosvg` (one extra `pip install`, brings the Cairo native library along).

### Invocation

```bash
python3 ~/.claude/skills/p:drawio/render.py INPUT.drawio OUTPUT.svg
```

Writes the SVG and prints the path to stderr. Exit 0 on success, 2 on usage error, non-zero on parse failure.

The script has no third-party dependencies — it uses only `xml.etree.ElementTree`, `html`, `re`, and `dataclasses` from the standard library. Python 3.7+ is enough.

### Supported shapes

| Shape | Style trigger |
|-------|---------------|
| Rounded / sharp rectangle | (default), `rounded=1` |
| Ellipse | `ellipse;` |
| Diamond / rhombus | `rhombus;` |
| Swimlane | `swimlane;` with `horizontal=0` or `horizontal=1` |
| Group (invisible container) | `group;` |
| Document (wavy bottom) | `shape=mxgraph.flowchart.document` |
| Cylinder | `shape=cylinder3` or `shape=cylinder` |

Labels: `<b>`, `<i>`, `<font color="#...">`, `<br>`, `&#xa;`, `align`, `verticalAlign`, `spacingLeft/Top/Right/Bottom`, `fontStyle` (bitwise 1=bold, 2=italic). Edges: orthogonal Manhattan routing (vertical/horizontal jogs), arrow heads, `labelBackgroundColor` for edge-label cards, `dashed=1`.

### Limitations

- **No mxgraph stencils** (AWS/Azure/GCP/Cisco icon libraries) — they render as a fallback rectangle. If the user explicitly asks for cloud-architecture icons, fall back to the draw.io CLI instead
- **No ELK auto-routing** — edges follow a simple Manhattan path. For diagrams that rely on dense parallel edges or carefully spaced bends, the result will look messier than the draw.io CLI output
- **No bezier / curved edges** — `curved=1` is ignored
- **Multi-line swimlane titles** at `horizontal=0` are joined into a single rotated line (rotation makes multi-line awkward)
- **No PNG / PDF** — SVG only in v1

### Markdown-safe by design

The script emits labels as native SVG `<text>` elements (not `<foreignObject>`), so the output is safe to embed in GitHub markdown, Confluence, Notion, and docs sites. SVG → PNG converters (`cairosvg`, `rsvg-convert`) also handle native `<text>` cleanly.

## File naming

- Use a descriptive filename based on the diagram content (e.g., `login-flow`, `database-schema`)
- Use lowercase with hyphens for multi-word names
- For export, use double extensions: `name.drawio.png`, `name.drawio.svg`, `name.drawio.pdf` — this signals the file contains embedded diagram XML
- After a successful export, delete the intermediate `.drawio` file — the exported file contains the full diagram

## XML format

A `.drawio` file is native mxGraphModel XML. Always generate XML directly — Mermaid and CSV formats require server-side conversion and cannot be saved as native files.

### Basic structure

Every diagram must have this structure:

```xml
<mxGraphModel adaptiveColors="auto">
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <!-- Diagram cells go here with parent="1" -->
  </root>
</mxGraphModel>
```

- Cell `id="0"` is the root layer
- Cell `id="1"` is the default parent layer
- All diagram elements use `parent="1"` unless using multiple layers

## XML reference

For the complete draw.io XML reference including common styles, edge routing, containers, layers, tags, metadata, dark mode colors, and XML well-formedness rules, read the bundled file:
[./xml-reference.md](./xml-reference.md)

(absolute path: `~/.claude/skills/p:drawio/xml-reference.md`)

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| draw.io CLI not found | Desktop app not installed or not on PATH | Keep the `.drawio` file and tell the user to install the draw.io desktop app, or open the file manually |
| Export produces empty/corrupt file | Invalid XML (e.g. double hyphens in comments, unescaped special characters) | Validate XML well-formedness before writing; see the XML well-formedness section below |
| Diagram opens but looks blank | Missing root cells `id="0"` and `id="1"` | Ensure the basic mxGraphModel structure is complete |
| Edges not rendering | Edge mxCell is self-closing (no child mxGeometry element) | Every edge must have `<mxGeometry relative="1" as="geometry" />` as a child element |
| File won't open after export | Incorrect file path or missing file association | Print the absolute file path so the user can open it manually |

## CRITICAL: XML well-formedness

- **NEVER include ANY XML comments (`<!-- -->`) in the output.** XML comments are strictly forbidden — they waste tokens, can cause parse errors, and serve no purpose in diagram XML.
- Escape special characters in attribute values: `&amp;`, `&lt;`, `&gt;`, `&quot;`
- Always use unique `id` values for each `mxCell`
