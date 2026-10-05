"""Shared utilities for AACdeck PDF modules.

Centralises helpers that were previously duplicated across make_cards.py,
make_lotto.py, make_tegnprotokoll.py, and app.py.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

from PIL import Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# ── Constants ──────────────────────────────────────────────────────────────────

IMAGE_EXTS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".webp", ".avif"})

_FONT_CANDIDATES: list[str] = [
    # Linux — Liberation Sans
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    # macOS / Windows Arial
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    # DejaVu fallback
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]

_REGULAR_FONT_CANDIDATES: list[str] = [
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]

# ── Image helpers ──────────────────────────────────────────────────────────────


def to_rgb(img: Image.Image) -> Image.Image:
    """Convert any Pillow image to RGB, handling palette+transparency correctly."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        return bg
    return img.convert("RGB")




# ── Font helpers ───────────────────────────────────────────────────────────────

# Track aliases already registered in this process to avoid redundant TTFont
# construction and pdfmetrics calls when the same alias is registered repeatedly.
_registered_font_aliases: set[str] = set()


def register_nordic_bold_font(alias: str) -> str:
    """Register a TTF bold font that supports Nordic characters (æ ø å).

    Tries Liberation Sans Bold → Arial Bold → DejaVu Sans Bold.  Falls back to
    the built-in Helvetica-Bold (no Nordic support) if nothing is found.

    Parameters
    ----------
    alias:
        The ReportLab internal font name to register under (e.g. ``"_LabelFont"``).

    Returns the registered alias, or ``"Helvetica-Bold"`` on fallback.
    """
    if alias in _registered_font_aliases:
        return alias
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont(alias, path))
            _registered_font_aliases.add(alias)
            return alias
    return "Helvetica-Bold"


def register_nordic_regular_font(alias: str) -> str:
    """Register a TTF regular font with Nordic character support.

    Falls back to ``"Helvetica"`` if nothing is found.
    """
    if alias in _registered_font_aliases:
        return alias
    for path in _REGULAR_FONT_CANDIDATES:
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont(alias, path))
            _registered_font_aliases.add(alias)
            return alias
    return "Helvetica"


# ── Text helpers ───────────────────────────────────────────────────────────────


def fit_text(
    c: canvas.Canvas,
    font: str,
    text: str,
    max_width: float,
    start_pt: float,
) -> float:
    """Return the largest font size ≤ *start_pt* at which *text* fits *max_width*."""
    size = start_pt
    while size > 4:
        if c.stringWidth(text, font, size) <= max_width:
            return size
        size -= 0.5
    return size


def fit_label(
    c: canvas.Canvas,
    font: str,
    text: str,
    max_width: float,
    start_pt: float,
) -> tuple[str, float]:
    """Fit *text* into *max_width*, returning ``(display_text, font_size)``.

    Shrinks the font down to the :func:`fit_text` floor (~4 pt).  If the text
    still overflows at that floor — i.e. it cannot be made to fit by shrinking
    alone — it is truncated with a trailing ellipsis so labels never bleed past
    the card edge.  The returned text is what should actually be drawn.
    """
    size = fit_text(c, font, text, max_width, start_pt)
    if c.stringWidth(text, font, size) <= max_width:
        return text, size
    ellipsis = "…"
    truncated = text
    while truncated and c.stringWidth(truncated + ellipsis, font, size) > max_width:
        truncated = truncated[:-1]
    return (truncated + ellipsis if truncated else ellipsis), size


# ── Page grid geometry ───────────────────────────────────────────────────────────


class Grid(NamedTuple):
    """Result of :func:`compute_grid` — a square-card page layout."""

    card_size: float
    image_area_h: float
    rows_per_page: int
    cards_per_page: int


def compute_grid(
    page_w: float,
    page_h: float,
    cols: int,
    page_margin: float,
    card_gap: float,
    label_area_h: float,
) -> Grid:
    """Compute square-card grid geometry for a page.

    Shared by the ASK-card and lotto generators, which lay out identical
    square-card grids (the only difference is column count and spacing).

    Raises :exc:`ValueError` if the constants leave no room for cards.
    """
    card_size = (page_w - 2 * page_margin - (cols - 1) * card_gap) / cols
    image_area_h = card_size - label_area_h
    if card_size <= 0 or image_area_h <= 0:
        raise ValueError(
            "Layout constants produce non-positive card dimensions — "
            "reduce the page margin, card gap, or column count."
        )
    rows_per_page = int((page_h - 2 * page_margin + card_gap) // (card_size + card_gap))
    cards_per_page = cols * rows_per_page
    if cards_per_page <= 0:
        raise ValueError(
            "Layout constants produce 0 cards per page — "
            "reduce the page margin, card gap, or column count."
        )
    return Grid(card_size, image_area_h, rows_per_page, cards_per_page)


# ── Filename sanitisation ──────────────────────────────────────────────────────

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_stem(label: str) -> str:
    """Turn an arbitrary label string into a safe filename stem.

    Replaces spaces with underscores, strips characters illegal on Windows
    (``<>:"/\\|?*`` and control characters), and collapses runs of underscores.
    """
    stem = label.replace(" ", "_")
    stem = _UNSAFE_CHARS.sub("_", stem)
    # Collapse repeated underscores that may arise from multi-char replacements
    stem = re.sub(r"_+", "_", stem).strip("_")
    return stem or "image"


_DUPLICATE_SUFFIX = re.compile(r"__\d+$")


def stem_to_label(stem: str) -> str:
    """Convert a filename stem to a human-readable card label.

    Strips any trailing ``__N`` duplicate counter added by the GUI, then
    replaces remaining underscores with spaces.
    """
    stem = _DUPLICATE_SUFFIX.sub("", stem)
    return stem.replace("_", " ")


# ── ARASAAC attribution ────────────────────────────────────────────────────────
# ARASAAC pictograms are CC BY-NC-SA 4.0, which requires crediting the author
# and owner on anything that reproduces them.

ARASAAC_CREDIT = (
    "Piktogrammer: Sergio Palao / ARASAAC (arasaac.org), "
    "Aragón-regjeringen — CC BY-NC-SA 4.0"
)
CREDIT_FONT_PT = 7

# Per-session sidecar listing the filenames that were downloaded from ARASAAC,
# so a session mixing pictograms with the user's own photos can be credited
# only when it actually contains a pictogram.
ARASAAC_MANIFEST = ".arasaac.json"


def record_arasaac_image(image_path: Path, pic_id: int) -> None:
    """Note in the session's manifest that *image_path* came from ARASAAC."""
    manifest = image_path.parent / ARASAAC_MANIFEST
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[image_path.name] = pic_id
    manifest.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def carry_arasaac_record(src: Path, dst: Path) -> None:
    """Keep the manifest in step after *src* was renamed or copied to *dst*.

    Call after the file operation: if *src* is gone it was a rename and its
    entry is dropped; otherwise the copy gets an entry of its own.
    """
    manifest = src.parent / ARASAAC_MANIFEST
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict) or src.name not in data:
        return
    data[dst.name] = data[src.name]
    if not src.exists():
        del data[src.name]
    manifest.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def uses_arasaac(images: list[Path]) -> bool:
    """Return True if any of *images* is listed in its session's ARASAAC manifest."""
    if not images:
        return False
    manifest = images[0].parent / ARASAAC_MANIFEST
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and any(p.name in data for p in images)


def draw_credit(c: canvas.Canvas, text: str, page_w: float, page_margin: float) -> None:
    """Draw a small grey centred credit line inside the bottom page margin.

    Cards never extend below *page_margin*, so the line can't overlap them.
    """
    font = register_nordic_regular_font("_CreditFont")
    c.setFont(font, CREDIT_FONT_PT)
    c.setFillColorRGB(0.55, 0.55, 0.55)
    c.drawCentredString(page_w / 2, (page_margin - CREDIT_FONT_PT) / 2, text)
    c.setFillColorRGB(0, 0, 0)


# ── PDF opener ─────────────────────────────────────────────────────────────────


def open_file(path: str) -> None:
    """Open *path* with the platform default viewer (non-blocking)."""
    import subprocess

    if not Path(path).exists():
        return
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
