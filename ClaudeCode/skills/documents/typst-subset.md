# The Typst subset `replica.py build` accepts

A short orientation, not the reference. The full, maintained description (every accepted
form, the flow model, the hyphenation stage, what is measured and what is unverified) is
`docs/components/litepdf-typst.md` in the liteparser checkout. The subset is compiled by
liteparser's own pure-Python compiler (`litepdf_typst.py`); no `typst` binary is involved, and
it is **unverified against the real Typst**.

## Accepted

- `#set page(width, height, margin)`, `#set text(font, size, lang, hyphenate, ...)`,
  `#set par(leading, spacing, justify, first-line-indent, hanging-indent)`.
- Flow text: blank-line separated paragraphs and `=` headings, with
  `#show heading.where(level: N): set text(..)` / `set block(above:, below:)` / `set align(..)`
  (exactly these three show-rule forms; a heading level needs its text and block rules before use).
- Absolute elements: `#place(top + left, dx: .., dy: ..)[..]` (both alignment axes required),
  `#text(..)[..]` inline, `#rect`, `#line`, `#image("name.png", width:, height:, fit: "stretch")`
  (a relative png/jpg path inside the source's directory, no `..`).
- `#pagebreak()`, `#linebreak()`, `#v(Xpt, weak: true)` between flow blocks, and the label
  column `[#h(Xpt)]#box(width: Wpt)[label]` at the very start of a paragraph.
- Lengths in `pt`, `mm`, `cm`, `in`, `em`; `//` comments; escaped markup specials.

## Refused (a subset error with line:col and a `use:` hint)

- Scripting (`#let`, `#for`, `#if`, `#import`, `#include`, `#context`), `#align`, `#block`,
  any other show rule, unknown functions, parameters or units.
- `*strong*` and `_emphasis_` markup (use `#text(weight: "bold")[..]`), list markup.
- `hyphenate: true` with a language other than `hu`; both `first-line-indent` and
  `hanging-indent` non-zero at once.
- Absolute or `..` image paths, an image without `fit: "stretch"`, an unknown font, rotated
  pages (at emit), input larger than 1 MiB, nesting deeper than 32.

## Flow model, in one breath

Greedy line breaking at spaces and after `-`; justification stretches only the spaces; line
pitch is leading + cap-height x size; the gap between paragraphs is the larger of the two
spacings; a line that does not fit moves to the next page. Deliberately not implemented:
optimal breaking, shrinking, widow / orphan control, keeping a heading with the next block.
