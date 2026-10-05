# pip install Pillow reportlab

import io
import sys
from pathlib import Path
from typing import NamedTuple, Optional

from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader

from pdf_utils import (
    IMAGE_EXTS,
    to_rgb,
    register_nordic_bold_font,
    fit_label,
    compute_grid,
    stem_to_label,
    ARASAAC_CREDIT,
    draw_credit,
)


# ── Layout constants ───────────────────────────────────────────────────────────
LOTTO_COLS = 4
PAGE_MARGIN = 8 * mm  # page edge → first card
CARD_GAP = 4 * mm  # gap between cards
BORDER_WIDTH = 2.5  # points
LABEL_FONT_PT = 10  # points
LABEL_PAD_V = 2 * mm  # vertical padding above label text baseline
IMAGE_PAD = 2 * mm  # padding around image on all four sides within image area


# ── Font setup ─────────────────────────────────────────────────────────────────
def _register_label_font() -> str:
    """Register a TTF with Nordic character support and return its name."""
    return register_nordic_bold_font("_LottoFont")


# ── Helpers ────────────────────────────────────────────────────────────────────
class _Decoded(NamedTuple):
    """One image decoded once, reusable across the board and cut-out passes."""

    label: str
    size: Optional[tuple[int, int]]  # (w, h), or None if decode failed
    png: Optional[bytes]  # RGB PNG bytes, or None if decode failed


def _decode_images(images: list[Path]) -> list[_Decoded]:
    """Decode each image to RGB PNG bytes exactly once.

    A card whose image fails to load still gets a border + label (with
    ``size``/``png`` left as ``None``), and a warning is printed — matching the
    behaviour of the other generators.
    """
    decoded: list[_Decoded] = []
    for img_path in images:
        label = stem_to_label(img_path.stem)
        try:
            pil_img = to_rgb(Image.open(img_path))
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            decoded.append(_Decoded(label, pil_img.size, buf.getvalue()))
        except Exception as exc:
            print(f"  Warning: could not load {img_path.name}: {exc}")
            decoded.append(_Decoded(label, None, None))
    return decoded


def _draw_lotto_card(
    c: canvas.Canvas,
    card_x: float,
    card_y: float,
    card_size: float,
    image_area_h: float,
    label_area_h: float,
    font: str,
    entry: _Decoded,
    cutout_mode: bool = False,
) -> None:
    """Draw one lotto card with the label at the bottom.

    *card_y* is the bottom-left corner in ReportLab coordinates (y increases
    upward).  *cutout_mode* replaces the solid border with a dashed cut-line.
    """
    # ── Border ─────────────────────────────────────────────────────────────────
    c.setLineWidth(BORDER_WIDTH)
    c.setStrokeColorRGB(0, 0, 0)
    if cutout_mode:
        c.setDash([4, 4])
    c.rect(card_x, card_y, card_size, card_size)
    if cutout_mode:
        c.setDash([])  # reset to solid for everything else

    # ── Label at bottom (card_y → card_y + label_area_h) ──────────────────────
    display, font_size = fit_label(c, font, entry.label, card_size - 4 * mm, LABEL_FONT_PT)
    baseline = card_y + LABEL_PAD_V
    c.setFont(font, font_size)
    c.setFillColorRGB(0, 0, 0)
    c.drawCentredString(card_x + card_size / 2, baseline, display)

    # ── Image (above label area, IMAGE_PAD on all four sides) ─────────────────
    # Image area in ReportLab: bottom = card_y + label_area_h, height = image_area_h
    if entry.png is None or entry.size is None:
        return
    orig_w, orig_h = entry.size
    avail_w = card_size - 2 * IMAGE_PAD
    avail_h = image_area_h - 2 * IMAGE_PAD
    scale = min(avail_w / orig_w, avail_h / orig_h)
    draw_w = orig_w * scale
    draw_h = orig_h * scale

    img_area_bottom = card_y + label_area_h
    draw_x = card_x + IMAGE_PAD + (avail_w - draw_w) / 2
    draw_y = img_area_bottom + IMAGE_PAD + (avail_h - draw_h) / 2

    c.saveState()
    clip = c.beginPath()
    clip.rect(card_x, img_area_bottom, card_size, image_area_h)
    c.clipPath(clip, stroke=0, fill=0)
    c.drawImage(ImageReader(io.BytesIO(entry.png)), draw_x, draw_y, draw_w, draw_h)
    c.restoreState()


# ── Internal PDF builder ───────────────────────────────────────────────────────
def _render(
    decoded: list[_Decoded],
    output_path: Path,
    font: str,
    cutout_mode: bool,
) -> Path:
    """Render *decoded* cards to a single PDF at *output_path*."""
    page_w, page_h = A4
    label_area_h = LABEL_FONT_PT + 2 * LABEL_PAD_V
    card_size, image_area_h, rows_per_page, cards_per_page = compute_grid(
        page_w, page_h, LOTTO_COLS, PAGE_MARGIN, CARD_GAP, label_area_h
    )

    c = canvas.Canvas(str(output_path), pagesize=A4)

    for i, entry in enumerate(decoded):
        page_pos = i % cards_per_page
        if page_pos == 0 and i > 0:
            draw_credit(c, ARASAAC_CREDIT, page_w, PAGE_MARGIN)
            c.showPage()

        row = page_pos // LOTTO_COLS
        col = page_pos % LOTTO_COLS

        x = PAGE_MARGIN + col * (card_size + CARD_GAP)
        y = page_h - PAGE_MARGIN - (row + 1) * card_size - row * CARD_GAP

        _draw_lotto_card(
            c, x, y, card_size, image_area_h, label_area_h, font, entry, cutout_mode
        )

    # Lotto sessions are filled from ARASAAC, so every page carries its credit.
    draw_credit(c, ARASAAC_CREDIT, page_w, PAGE_MARGIN)
    c.save()
    pages = (len(decoded) - 1) // cards_per_page + 1
    mode = "cut-out" if cutout_mode else "board"
    print(
        f"✓  {len(decoded)} lotto cards ({mode}) across {pages} page(s)  →  {output_path}\n"
        f"   Grid: {LOTTO_COLS} × {rows_per_page} = {cards_per_page} cards/page"
    )
    return output_path


def _prepare(
    session_path_str: str,
    output_dir: Optional[Path],
) -> tuple[Path, list[_Decoded], Path, str]:
    """Validate the session, decode its images once, and set up the output dir."""
    session_path = Path(session_path_str).resolve()
    if not session_path.is_dir():
        raise ValueError(f"'{session_path_str}' is not a directory.")

    images = sorted(p for p in session_path.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not images:
        raise ValueError(f"No images found in '{session_path_str}'.")

    if output_dir is None:
        output_dir = Path(__file__).resolve().parent / "output"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    decoded = _decode_images(images)
    font = _register_label_font()
    return session_path, decoded, output_dir, font


# ── Public API ─────────────────────────────────────────────────────────────────
def make_board_pdf(
    session_path_str: str,
    output_dir: Optional[Path] = None,
) -> Path:
    """Generate a lotto board PDF (solid borders) for *session_path_str*."""
    session_path, decoded, output_dir, font = _prepare(session_path_str, output_dir)
    return _render(decoded, output_dir / f"{session_path.name}_board.pdf", font, False)


def make_cutout_pdf(
    session_path_str: str,
    output_dir: Optional[Path] = None,
) -> Path:
    """Generate a lotto cut-out PDF (dashed borders) for *session_path_str*."""
    session_path, decoded, output_dir, font = _prepare(session_path_str, output_dir)
    return _render(decoded, output_dir / f"{session_path.name}_cutout.pdf", font, True)


def make_board_and_cutout_pdf(
    session_path_str: str,
    output_dir: Optional[Path] = None,
) -> tuple[Path, Path]:
    """Generate both the board and cut-out PDFs, decoding each image only once.

    Returns ``(board_path, cutout_path)``.
    """
    session_path, decoded, output_dir, font = _prepare(session_path_str, output_dir)
    board = _render(decoded, output_dir / f"{session_path.name}_board.pdf", font, False)
    cutout = _render(decoded, output_dir / f"{session_path.name}_cutout.pdf", font, True)
    return board, cutout


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(
            "Usage:   python make_lotto.py <session_folder>\n"
            "Example: python make_lotto.py lotto-sessions/2026-04-test"
        )
    board, cutout = make_board_and_cutout_pdf(sys.argv[1])
    print(f"Board:  {board}")
    print(f"Cutout: {cutout}")
