---
name: documents
description: >-
  Process PDFs and produce documents from the results: measure a sample PDF (page boxes,
  margins, centre axis, baseline pitch per style, paragraph gaps, page-number position, rules,
  images), then build a new document from that measurement. The first supported workflow is the
  PDF replica: reproduce the sample's page layout with NEW content, without a browser - emit the
  sample as editable Typst-subset source through liteparser, swap the content in that source,
  build it to a PDF with real TrueType metrics, and compare the replica's structure against the
  sample with a PASS/FAIL table. Use when asked to make a document "look exactly like" an
  existing PDF (a bill, a form letter, an official template), to rebuild a PDF from Markdown in
  a sample's layout, to measure or dump a PDF's layout, or when a headless-Chrome / HTML print
  attempt drifts from the sample by fractions of a point.
---

# documents

Processes PDFs (measure, dump, extract) and produces documents from the results. The first
supported workflow is the **PDF replica**, described below; other output formats are planned
and not yet supported.

```bash
S=~/.claude/skills/p/skills/documents/scripts
```

## PDF replica

Three stdlib-only scripts (Python 3.9+), run with `python3 -I`. They need the liteparser
package: `measure.py` and `compare.py` only `litepdf.py` (dumping PDFs); `replica.py` the whole
package (`litepdf.py`, `litepdf_write.py`, `litepdf_flow.py`, `litepdf_hyph.py`,
`litepdf_typst.py`), whose directory it puts on `sys.path` and whose `litepdf_typst` API it
calls in process (not the CLI). liteparser is a private project that is not published with this
plugin: point at a checkout with `--litepdf-dir DIR` or set `$LITEPDF_DIR`. There is no other
lookup and no fallback; a missing checkout or module exits 2 with one stderr line naming it:

```bash
export LITEPDF_DIR=/path/to/liteparser
```

| script | does |
|---|---|
| `measure.py` | measures a sample PDF (or a litepdf dump) per page and prints the report: page boxes, margins, centre axis, pitch per style, gaps, page number, rules, images. Writes no layout file |
| `replica.py emit` | dumps the sample (or reads a dump), builds `fonts.json` from `--font-dir`, and writes `report.typ`: the sample as editable Typst source (recovered paragraphs and headings as flow text, the rest as exact `#place` elements) |
| `replica.py build` | compiles `report.typ` to `report.pdf` with litepdf_typst and the TrueType metrics, then checks every word written against `pdftotext` |
| `compare.py` | compares the STRUCTURE of the replica with the sample (page boxes, margins, edges, axis, pitch, first baseline, page number, rules, images, fonts, forbidden / required characters) and exits 1 on any FAIL |

The accepted Typst subset is summarised in `typst-subset.md` next to this file (the full
reference is `docs/components/litepdf-typst.md` in the liteparser checkout). Each script's
`--help` carries its full usage, output and exit codes (0 ok, 1 a real finding, 2 bad
invocation / missing dependency / refusal / Typst subset error, always one stderr line).

**Status: unverified against the real Typst.** The `.typ` is a strict subset compiled by
liteparser's own pure-Python compiler; no `typst` binary is involved anywhere, and every
Typst-semantics claim in it (cap-height placement, spacing, escapes, line geometry) comes from
the Typst documentation, not from a run of Typst. Trust `compare.py` and the page images, not
the claim that real Typst would render the same.

### Workflow

1. **Measure the sample** (optional, but it explains the roles), and look at it:

   ```bash
   LITEPDF_DIR=/path/to/liteparser python3 -I $S/measure.py sample.pdf \
       --out-dir WORK/sample > WORK/measure.md
   pdftoppm -r 100 -png sample.pdf WORK/sample      # then Read the PNGs
   ```

   The dump lands in `WORK/sample/<stem>.dump.jsonl`, the images in `WORK/sample/<stem>_images/`.
   Read the per-page report (margins, centre axis, pitch per style, gaps, page number, rules)
   and the page images to understand the roles: titles, headings, body, quotes, the cover.

2. **Emit** the sample as Typst source:

   ```bash
   LITEPDF_DIR=/path/to/liteparser python3 -I $S/replica.py emit sample.pdf \
       --font-dir /path/to/truetype-fonts -o WORK/report.typ
   ```

   `--font-dir` is a directory holding the sample's TrueType fonts (searched recursively for
   `.ttf`; repeatable); for a LibreOffice sample the Liberation family bundled with LibreOffice
   works (on macOS typically `/Applications/LibreOffice.app/Contents/Resources/fonts/truetype`,
   a path that varies by install and is only an example). A dump font with no matching TTF
   variant is refused (exit 2). The sample's images are written next to `report.typ`, because
   the source names them relative to its own directory. The per-page table says which pages
   became flow text and which stayed absolute (and why).

3. **Edit `WORK/report.typ`**: swap the content, keep the structure. Replace paragraph text in
   place; change styles only through `#set` / `#show` rules at the top, never by inventing
   constructs outside the subset. Everything the compiler does not know is refused with
   line:col and a `use:` hint, so a bad edit costs one build, not a silent drift.

4. **Build**:

   ```bash
   LITEPDF_DIR=/path/to/liteparser python3 -I $S/replica.py build WORK/report.typ \
       --font-dir /path/to/truetype-fonts -o WORK/report.pdf
   ```

   The two outputs share a stem (`report.typ` -> `report.pdf`; another stem is refused). A
   subset error exits 2 with one line `file:line:col: construct: message -- use: hint`. After
   writing, every word written must appear in the `pdftotext` output (exit 1 if not); without
   `pdftotext` on PATH (or with `--no-text-check`) the check is skipped with an INFO line.

5. **Compare**, then fix the `.typ` and repeat until PASS:

   ```bash
   LITEPDF_DIR=/path/to/liteparser python3 -I $S/compare.py sample.pdf WORK/report.pdf \
       --forbid U+2013,U+2014 --require őűŐŰ [--pages-map 1:1,2:2,3:3,4:5]
   ```

   Map a sample page to the output page of the same kind (a section's first page to a section's
   first page) when the page counts differ.

6. **Visual check**: `pdftoppm -r 100 -png -f N -l N` some pages of both PDFs, then Read them.

7. **Text check**: `pdftotext -enc UTF-8 WORK/report.pdf -`: nothing missing, nothing leaked in
   from content that does not belong, no forbidden characters, hyphenated words whole.

### Why not headless Chrome

Measured on the bill sample's Chrome rebuild (`compare.py` reports it red): Chrome snaps text
baselines to 0.75 pt (a 13.8 pt pitch comes out 13.5 / 14.25, the first baseline 85.5 for
85.601) and the page box to whole 1/300-inch units (594.96 x 841.92 pt for a 595.304 x
841.89 pt sample, which no CSS `@page` size can express). It also substitutes Times New Roman
for the sample's Liberation Serif and writes its text under a 0.75 CTM. `replica.py build`
writes every coordinate as computed from the TrueType metrics.

### Known limits

- **Unverified against the real Typst** (see above): the layout model is doc-based.
- **Rotation 0 only (v1).** measure.py, compare.py and `replica.py emit` refuse a dumped page
  with `rotation` != 0 (one stderr line naming the page and rotation, exit 2). litepdf 1ff75e2
  reports rotated pages in the MediaBox frame with `rotation` (and `display_width` /
  `display_height` when rotation != 0), the basis for lifting this later; rotation 180 and
  rects / images on rotated pages are unverified there.
- **Hyphenation: Hungarian (`hu`) only.** `#set text(lang: "hu", hyphenate: true)`; any other
  language with `hyphenate: true` is a subset error, and `auto` with another language simply
  does not hyphenate.
- **Greedy line breaking**, at spaces and after a `-` that precedes a letter (a hyphenated
  compound may now break there); no optimal (Knuth-Plass) breaking, no shrinking, no
  widow / orphan control, no keeping a heading with the next block. Line breaks therefore
  differ slightly from a LibreOffice / Word sample even for identical text.
- **No kerning or shaping**: widths are plain advance widths. Original per-glyph positions that
  deviate from them (kerning, uneven gaps) are lost on emit.
- **Source cap 1 MiB** per `.typ`; nesting deeper than 32 and scripting (`#let`, `#for`, ...)
  are refused.
- **TrueType fonts only** (glyf outlines), no font fallback: a missing glyph is refused.
- **Tables, columns, footnotes** are not flow: they stay `#place`d absolute elements as the
  emitter wrote them; two-column pages are emitted absolute.
- **compare.py checks structure, not text.** The right edge uses its own tolerance (default
  0.6 pt, since justified lines of a LibreOffice sample end up to ~0.56 pt short); a pitch
  measured from fewer than 2 baseline diffs is INFO, not FAIL; the centre axis is INFO when one
  side has no centred line.
- **The emit is a starting point.** Paragraph and heading recovery are heuristics; the page
  images and `compare.py` on the unedited `report.pdf` are the test that they captured the
  geometry.
- litepdf's dump drops lone-space runs on some producers, keeps the `ABCDEF+` subset tag on
  font names (stripped here), and reports `size` without the CTM (Chrome's 12 pt text shows as
  16 pt); compare then reports a font / size mismatch.
