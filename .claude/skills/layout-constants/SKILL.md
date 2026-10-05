---
name: layout-constants
description: Layout constant tables and rendering gotchas for the PDF generators (make_cards, make_lotto, make_tegnprotokoll) and the gui_previews.py previews. Load before editing any drawing logic, layout constant, grid geometry, or preview renderer.
user-invocable: false
---

Background knowledge for changing how PDFs are drawn. Read this before editing `make_cards.py`, `make_lotto.py`, `make_tegnprotokoll.py`, `pdf_utils.py`, or the `render_*_preview` functions in `gui_previews.py`.

## Three gotchas that cause most bugs here

**ReportLab's origin is bottom-left.** A label drawn visually at the *top* of a card has the *highest* `y`. Every vertical measurement counts up from the bottom of the page. Re-derive the direction of each `+`/`-` rather than assuming.

**Helvetica-Bold does not render æ/ø/å.** Font priority is Liberation Sans Bold → Arial Bold → DejaVu Sans Bold → Helvetica-Bold, and that last built-in fallback silently mangles Norwegian text. All text must go through `pdf_utils.register_nordic_bold_font()` or `register_nordic_regular_font()`. Never hardcode a font name.

**The preview must not drift from the PDF.** `gui_previews.py` renders previews with Pillow in pixels, on a completely separate code path from ReportLab. Shared geometry is therefore *imported*, not copied:

```python
_PREV_COLS = make_cards.COLS                      # gui_previews.py
_LOTTO_PREV_COLS = make_lotto.LOTTO_COLS          # gui_previews.py
_TEGN_PREV_COL_FRACS = make_tegnprotokoll.COL_FRACS  # gui_previews.py
```

Keep these as imports. Pixel sizes, gaps, and margins (`_PREV_CARD = 150`, `_PREV_GAP = 6`, `_PREV_MARGIN = 12`, and the `_TEGN_PREV_*` values) are genuinely preview-only and stay as literals.

## Layout constants

All values are in ReportLab points (1 pt = 1/72"). Millimetre constants use `from reportlab.lib.units import mm`, multiplied inline. Each generator keeps these in one block at the top of the file — **change spacing there, not in drawing logic**.

### `make_cards.py` — label at top

| Constant | Default | Effect |
|---|---|---|
| `COLS` | `3` | Cards per row |
| `PAGE_MARGIN` | `10 mm` | Page edge → first card |
| `CARD_GAP` | `5 mm` | Space between cards |
| `BORDER_WIDTH` | `5` pt | Card border thickness |
| `LABEL_FONT_PT` | `12` pt | Max label font size |
| `LABEL_PAD_V` | `2.5 mm` | Vertical padding above/below label |
| `IMAGE_PAD` | `2.5 mm` | Padding left, right, bottom of image |

### `make_lotto.py` — label at **bottom**

| Constant | Default | Effect |
|---|---|---|
| `LOTTO_COLS` | `4` | Cards per row |
| `PAGE_MARGIN` | `8 mm` | Page edge → first card |
| `CARD_GAP` | `4 mm` | Space between cards |
| `BORDER_WIDTH` | `2.5` pt | Card border thickness |
| `LABEL_FONT_PT` | `10` pt | Max label font size |
| `LABEL_PAD_V` | `2 mm` | Vertical padding above label baseline |
| `IMAGE_PAD` | `2 mm` | Padding on all four sides |

Lotto's label sits at the bottom and its `IMAGE_PAD` applies to all four sides — both differ from cards. Don't port assumptions across.

### `make_tegnprotokoll.py` — A4 table, 3 columns

| Constant | Default | Effect |
|---|---|---|
| `COL_FRACS` | `(0.27, 0.33, 0.40)` | word \| image \| description — **must sum to 1.0** |
| `PAGE_MARGIN` | `10 mm` | Page edge → table edge |
| `ROW_HEIGHT` | `35 mm` | Height of each data row |
| `TITLE_HEIGHT` | `12 mm` | Space above the table for the page title |
| `HEADER_ROW_H` | `8 mm` | Column-header row height |
| `FOOTER_H` | `8 mm` | Attribution footer at page bottom |
| `IMAGE_PAD` | `2 mm` | Padding around sign image in its cell |
| `CELL_PAD_H` / `CELL_PAD_V` | `3 mm` / `2 mm` | Text padding inside word/description cells |
| `BORDER_WIDTH` | `0.5` pt | Cell border thickness |
| Font sizes | `HEADER_FONT_PT=9`, `WORD_FONT_PT=14`, `DESC_FONT_PT=9`, `FOOTER_FONT_PT=7` | `WORD_FONT_PT` is a maximum; it shrinks to fit |

## Geometry rules

Cards are **square**. `card_size` is derived at runtime from page width, column count, `PAGE_MARGIN`, and `CARD_GAP` — never hardcode it, or the layout stops fitting A4. `pdf_utils.compute_grid()` owns this computation for both square-card generators and returns a `Grid` namedtuple; it raises `ValueError` when a constant combination makes the layout impossible.

Label sizing goes through `pdf_utils.fit_label()` (returns `(display_text, size)`, ellipsizing labels that can't fit even at minimum size) or `fit_text()` (size only). Don't hand-roll truncation.

Images are decoded by Pillow and converted to RGB PNG before reaching ReportLab, so ReportLab's own format support is irrelevant. Supported extensions live in `pdf_utils.IMAGE_EXTS`.

## `make_cards.py` call flow

1. `make_cards()` — entry point; resolves paths, discovers images, computes grid geometry via `pdf_utils.compute_grid()`, drives the ReportLab canvas
2. `_draw_card()` — draws one card in order **border → label → image** (Pillow → `BytesIO` → ReportLab). Label sizing and truncation go through `pdf_utils.fit_label()`
3. `_register_label_font()` — registers a Nordic-capable TTF via `pdf_utils.register_nordic_bold_font()`, falling back to Helvetica-Bold

`make_lotto.py` mirrors this flow with `LOTTO_COLS`, and additionally decodes each image once to render both the board and cut-out PDFs in `make_board_and_cutout_pdf()`.

## After changing anything here

```bash
.venv/bin/python -m pytest -q
```

The tests render real PDFs against temp sessions, so they catch exceptions and page-count changes — but they do **not** verify that anything landed in the right place. For visual changes, generate a PDF against a real session and look at it.
