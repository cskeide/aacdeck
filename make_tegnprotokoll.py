"""Tegnprotokoll PDF generator.

Produces a 3-column A4 table ready to print:

    Column 1 – Word/Tegn       (large bold text, centered)
    Column 2 – Sign image      (scaled to fit, from Tegnbanken)
    Column 3 – Child's usage   (pre-filled from descriptions.json, or ruled
                                 blank lines for hand-writing)

Attribution footer on every page:
    "Illustrasjoner: Statped / tegnbanken.no — CC BY-NC-ND 4.0"

Title, column headings and footer follow the i18n language (Norwegian by
default).

Usage (CLI)::

    python make_tegnprotokoll.py [--lang nb|en] <session-folder>

Usage (library)::

    from make_tegnprotokoll import make_tegnprotokoll
    out_path = make_tegnprotokoll("tegnprotokoll-sessions/my-session")
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Optional

from i18n import parse_cli_lang, t
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdf_utils import (
    IMAGE_EXTS,
    to_rgb,
    register_nordic_bold_font,
    register_nordic_regular_font,
    fit_label,
    stem_to_label,
)



# ── Layout constants ───────────────────────────────────────────────────────────

PAGE_MARGIN = 10 * mm  # page edge → table edge
ROW_HEIGHT = 35 * mm  # height of each data row
IMAGE_PAD = 2 * mm  # padding around sign image inside its cell
CELL_PAD_H = 3 * mm  # horizontal text padding inside word/desc cells
CELL_PAD_V = 2 * mm  # vertical text padding inside word/desc cells
BORDER_WIDTH = 0.5  # pt – cell border thickness
HEADER_FONT_PT = 9  # column header font size
WORD_FONT_PT = 14  # maximum word font size (shrinks to fit)
DESC_FONT_PT = 9  # description body font size
FOOTER_FONT_PT = 7  # attribution footer font size
TITLE_HEIGHT = 12 * mm  # space reserved for the page title above the table
HEADER_ROW_H = 8 * mm  # height of the column-header row
FOOTER_H = 8 * mm  # height of the attribution footer at page bottom

# Column widths as fractions of the usable page width
COL_FRACS = (0.27, 0.33, 0.40)  # word | image | description


# ── Font registration ──────────────────────────────────────────────────────────


def _register_bold_font() -> str:
    return register_nordic_bold_font("_TegnBold")


def _register_regular_font() -> str:
    return register_nordic_regular_font("_TegnReg")


# ── Text helpers ───────────────────────────────────────────────────────────────


def _wrap(
    c: canvas.Canvas, font: str, size: float, text: str, max_width: float
) -> list[str]:
    """Word-wrap *text* into lines that each fit *max_width*."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip() if current else word
        if c.stringWidth(candidate, font, size) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


# ── Row drawing ────────────────────────────────────────────────────────────────


def _draw_row(
    c: canvas.Canvas,
    row_x: float,
    row_y_top: float,
    row_h: float,
    col_widths: tuple,
    word: str,
    img_path: Optional[Path],
    description: str,
    bold_font: str,
    reg_font: str,
) -> None:
    """Draw one table row.

    *row_y_top* is the **top** edge of the row in ReportLab coordinates
    (y increases upward from bottom-left of page).
    """
    w_word, w_img, w_desc = col_widths
    row_bottom = row_y_top - row_h
    total_w = w_word + w_img + w_desc

    x0 = row_x  # left edge of word cell
    x1 = row_x + w_word  # left edge of image cell
    x2 = row_x + w_word + w_img  # left edge of desc cell

    # ── Cell borders ──────────────────────────────────────────────────────────
    c.setLineWidth(BORDER_WIDTH)
    c.setStrokeColorRGB(0.4, 0.4, 0.4)
    c.rect(x0, row_bottom, total_w, row_h)
    c.line(x1, row_bottom, x1, row_y_top)
    c.line(x2, row_bottom, x2, row_y_top)

    # ── Word cell (Col 1) ──────────────────────────────────────────────────────
    inner_w = w_word - 2 * CELL_PAD_H
    display, fs = fit_label(c, bold_font, word, inner_w, WORD_FONT_PT)
    c.setFont(bold_font, fs)
    c.setFillColorRGB(0, 0, 0)
    tw = c.stringWidth(display, bold_font, fs)
    c.drawString(
        x0 + (w_word - tw) / 2,
        row_bottom + (row_h - fs) / 2,
        display,
    )

    # ── Image cell (Col 2) ────────────────────────────────────────────────────
    if img_path is not None and img_path.exists():
        avail_w = w_img - 2 * IMAGE_PAD
        avail_h = row_h - 2 * IMAGE_PAD
        try:
            pil = to_rgb(Image.open(img_path))
            scale = min(avail_w / pil.width, avail_h / pil.height)
            new_w = int(pil.width * scale)
            new_h = int(pil.height * scale)
            pil = pil.resize((max(1, new_w), max(1, new_h)), Image.LANCZOS)
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            buf.seek(0)
            c.drawImage(
                ImageReader(buf),
                x1 + (w_img - new_w) / 2,
                row_bottom + (row_h - new_h) / 2,
                new_w,
                new_h,
                mask="auto",
            )
        except Exception as exc:
            print(f"  Warning: could not load {img_path.name}: {exc}")

    # ── Description cell (Col 3) ──────────────────────────────────────────────
    desc_w = w_desc - 2 * CELL_PAD_H
    if description:
        lines = _wrap(c, reg_font, DESC_FONT_PT, description, desc_w)
        line_h = DESC_FONT_PT + 1.5
        total_text_h = len(lines) * line_h
        # Vertically centre the text block
        line_y = row_bottom + (row_h + total_text_h) / 2 - DESC_FONT_PT
        c.setFont(reg_font, DESC_FONT_PT)
        c.setFillColorRGB(0, 0, 0)
        for line in lines:
            c.drawString(x2 + CELL_PAD_H, line_y, line)
            line_y -= line_h
    else:
        # Light ruled lines for hand-writing
        c.setLineWidth(0.3)
        c.setStrokeColorRGB(0.75, 0.75, 0.75)
        spacing = DESC_FONT_PT + 5
        y = row_bottom + CELL_PAD_V + spacing
        while y < row_y_top - CELL_PAD_V:
            c.line(x2 + CELL_PAD_H, y, x2 + w_desc - CELL_PAD_H, y)
            y += spacing


# ── Public entry point ─────────────────────────────────────────────────────────


def make_tegnprotokoll(
    session_path_str: str,
    output_dir: Optional[Path] = None,
) -> Path:
    """Generate a Tegnprotokoll PDF for *session_path_str*.

    Session folder layout::

        tegnprotokoll-sessions/{name}/
            bade.jpg            ← sign image; stem becomes word label
            stoer_bror.jpg      ← underscores → spaces in label
            descriptions.json   ← optional, {"stem": "description text", …}

    Parameters
    ----------
    session_path_str:
        Path to the session folder.
    output_dir:
        Where to write the PDF.  Defaults to ``{project_root}/output``.

    Returns the output :class:`~pathlib.Path`.
    Raises :exc:`ValueError` on invalid input (empty session, bad path, etc.).
    """
    session_path = Path(session_path_str).resolve()
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent / "output"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Descriptions sidecar
    descriptions: dict[str, str] = {}
    desc_file = session_path / "descriptions.json"
    if desc_file.exists():
        try:
            descriptions = json.loads(desc_file.read_text("utf-8"))
        except Exception as exc:
            print(f"  Warning: could not parse {desc_file.name}: {exc}")

    # Collect images
    images = sorted(p for p in session_path.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not images:
        raise ValueError(f"No images found in {session_path}")

    bold_font = _register_bold_font()
    reg_font = _register_regular_font()

    session_name = session_path.name
    out_path = output_dir / f"{session_name}_tegnprotokoll.pdf"
    c = canvas.Canvas(str(out_path), pagesize=A4)
    page_w, page_h = A4

    usable_w = page_w - 2 * PAGE_MARGIN
    col_widths = tuple(f * usable_w for f in COL_FRACS)
    w_word, w_img, w_desc = col_widths
    table_x = PAGE_MARGIN

    # ── Helpers scoped to this canvas ─────────────────────────────────────────

    def draw_header(table_top: float) -> float:
        """Draw column-header row; return y just below the header."""
        y0 = table_top - HEADER_ROW_H
        c.setFillColorRGB(0.87, 0.87, 0.87)
        c.rect(table_x, y0, usable_w, HEADER_ROW_H, fill=1, stroke=0)
        c.setLineWidth(0.8)
        c.setStrokeColorRGB(0.4, 0.4, 0.4)
        c.rect(table_x, y0, usable_w, HEADER_ROW_H)
        c.line(table_x + w_word, y0, table_x + w_word, table_top)
        c.line(table_x + w_word + w_img, y0, table_x + w_word + w_img, table_top)

        headers = [t("Sign"), t("Image"), t("How the child uses the sign")]
        cws = col_widths
        hx = table_x
        c.setFont(bold_font, HEADER_FONT_PT)
        c.setFillColorRGB(0, 0, 0)
        for hdr, cw in zip(headers, cws):
            hw = c.stringWidth(hdr, bold_font, HEADER_FONT_PT)
            c.drawString(hx + (cw - hw) / 2, y0 + 2 * mm, hdr)
            hx += cw
        return y0  # next row starts here

    def draw_footer() -> None:
        text = t("Illustrations: Statped / tegnbanken.no — CC BY-NC-ND 4.0")
        c.setFont(reg_font, FOOTER_FONT_PT)
        c.setFillColorRGB(0.55, 0.55, 0.55)
        fw = c.stringWidth(text, reg_font, FOOTER_FONT_PT)
        c.drawString(page_w / 2 - fw / 2, FOOTER_H / 2 + 1 * mm, text)

    def start_page(is_first: bool) -> float:
        """Draw page furniture; return the y of the first data row's top."""
        if not is_first:
            draw_footer()
            c.showPage()

        page_top = page_h - PAGE_MARGIN

        c.setFont(bold_font, 13)
        c.setFillColorRGB(0, 0, 0)
        display = session_name.replace("-", " ")
        c.drawString(PAGE_MARGIN, page_top - 8 * mm, t("Sign protocol — {name}", name=display))

        table_top = page_top - TITLE_HEIGHT
        return draw_header(table_top)

    # ── Render ─────────────────────────────────────────────────────────────────
    current_y = start_page(is_first=True)
    min_y = PAGE_MARGIN + FOOTER_H

    for img_path in images:
        if current_y - ROW_HEIGHT < min_y:
            current_y = start_page(is_first=False)

        word = stem_to_label(img_path.stem)
        desc = descriptions.get(img_path.stem, "")
        _draw_row(
            c,
            table_x,
            current_y,
            ROW_HEIGHT,
            col_widths,
            word,
            img_path,
            desc,
            bold_font,
            reg_font,
        )
        current_y -= ROW_HEIGHT

    draw_footer()
    c.save()

    print(f"Tegnprotokoll: {len(images)} sign(s) → {out_path}")
    return out_path


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = parse_cli_lang(sys.argv[1:])
    if len(args) != 1:
        print(f"Usage: {sys.argv[0]} [--lang nb|en] <session-folder>")
        sys.exit(1)
    make_tegnprotokoll(args[0])
