"""Pillow-rendered page previews for the three tabs.

Column counts and fractions are imported from the generator modules so a
preview can't drift from the real PDF layout."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

import make_cards
import make_lotto
import make_tegnprotokoll
from PIL import Image, ImageDraw, ImageFont
from pdf_utils import arasaac_credit, stem_to_label, to_rgb, uses_arasaac
from i18n import t
from PySide6.QtGui import QImage
from gui_common import pillow_to_qimage


# ── Preview rendering ──────────────────────────────────────────────────────────
# Column counts and column fractions are imported from the generator modules so
# the preview can never silently drift from the actual PDF layout.  The pixel
# sizes/gaps/margins below are preview-only (the PDFs work in mm, not px).
_PREV_CARD = 150  # px per card in preview
_PREV_COLS = make_cards.COLS
_PREV_GAP = 6
_PREV_MARGIN = 12

# Lotto preview constants (label-at-bottom)
_LOTTO_PREV_CARD = 120
_LOTTO_PREV_COLS = make_lotto.LOTTO_COLS
_LOTTO_PREV_GAP = 5
_LOTTO_PREV_MARGIN = 12

# Tegnprotokoll preview constants (A4 table, 3 columns)
_TEGN_PREV_W = 500
_TEGN_PREV_H = int(500 * 297 / 210)  # ≈707 px, A4 aspect
_TEGN_PREV_MARGIN = 20
_TEGN_PREV_ROW_H = 65  # px per data row
_TEGN_PREV_HDR_H = 20  # column-header row
_TEGN_PREV_TITLE_H = 28  # page-title area
_TEGN_PREV_FOOTER_H = 16
_TEGN_PREV_COL_FRACS = make_tegnprotokoll.COL_FRACS  # word | image | description


def _preview_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _draw_preview_credit(
    draw: ImageDraw.ImageDraw, page_w: int, page_h: int, margin: int
) -> None:
    """Mirror pdf_utils.draw_credit(): grey credit line in the bottom margin."""
    font = _preview_font(7)
    credit = arasaac_credit()
    try:
        bbox = font.getbbox(credit)
        tw = bbox[2] - bbox[0]
    except AttributeError:
        tw, _ = font.getsize(credit)  # type: ignore[attr-defined]
    draw.text(
        (page_w // 2 - tw // 2, page_h - margin + 2),
        credit,
        fill=(160, 160, 160),
        font=font,
    )


def _preview_cards_per_page() -> int:
    card, gap, margin, cols = _PREV_CARD, _PREV_GAP, _PREV_MARGIN, _PREV_COLS
    page_w = cols * card + (cols - 1) * gap + 2 * margin
    page_h = int(page_w * (297 / 210))
    rows_per_page = max(1, (page_h - 2 * margin + gap) // (card + gap))
    return rows_per_page * cols


def render_page_preview(images: List[Path], page_index: int = 0) -> QImage:
    """Render a scaled A4-proportioned preview of the given page using Pillow."""
    if not images:
        return QImage()

    card, gap, margin, cols = _PREV_CARD, _PREV_GAP, _PREV_MARGIN, _PREV_COLS
    page_w = cols * card + (cols - 1) * gap + 2 * margin
    page_h = int(page_w * (297 / 210))  # A4 aspect ratio

    rows_per_page = max(1, (page_h - 2 * margin + gap) // (card + gap))
    cards_per_page = rows_per_page * cols
    start = page_index * cards_per_page
    first_page = images[start : start + cards_per_page]

    label_h = max(18, card // 8)
    font_size = max(9, label_h - 6)
    font = _preview_font(font_size)

    page = Image.new("RGB", (page_w, page_h), (255, 255, 255))
    draw = ImageDraw.Draw(page)

    for idx, img_path in enumerate(first_page):
        row, col = divmod(idx, cols)
        cx = margin + col * (card + gap)
        cy = margin + row * (card + gap)

        # Card border
        draw.rectangle(
            [cx, cy, cx + card - 1, cy + card - 1], outline=(0, 0, 0), width=1
        )

        # Label background + text
        draw.rectangle(
            [cx, cy, cx + card - 1, cy + label_h],
            fill=(240, 240, 240),
            outline=(180, 180, 180),
            width=1,
        )
        label = stem_to_label(img_path.stem)
        try:
            bbox = font.getbbox(label)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            tw, th = font.getsize(label)  # type: ignore[attr-defined]
        draw.text(
            (cx + max(2, (card - tw) // 2), cy + max(1, (label_h - th) // 2)),
            label,
            fill=(0, 0, 0),
            font=font,
        )

        # Thumbnail image
        pad = 3
        iw, ih = card - 2 * pad, card - label_h - pad
        try:
            thumb = to_rgb(Image.open(img_path))
            thumb.thumbnail((iw, ih), Image.LANCZOS)
            page.paste(
                thumb,
                (
                    cx + pad + (iw - thumb.width) // 2,
                    cy + label_h + (ih - thumb.height) // 2,
                ),
            )
        except Exception:
            pass

    if uses_arasaac(images):
        _draw_preview_credit(draw, page_w, page_h, margin)
    return pillow_to_qimage(page)


def _lotto_cards_per_page() -> int:
    card, gap, margin, cols = (
        _LOTTO_PREV_CARD,
        _LOTTO_PREV_GAP,
        _LOTTO_PREV_MARGIN,
        _LOTTO_PREV_COLS,
    )
    page_w = cols * card + (cols - 1) * gap + 2 * margin
    page_h = int(page_w * (297 / 210))
    rows_per_page = max(1, (page_h - 2 * margin + gap) // (card + gap))
    return rows_per_page * cols


def _tegn_items_per_page() -> int:
    usable = (
        _TEGN_PREV_H
        - 2 * _TEGN_PREV_MARGIN
        - _TEGN_PREV_TITLE_H
        - _TEGN_PREV_HDR_H
        - _TEGN_PREV_FOOTER_H
    )
    return max(1, usable // _TEGN_PREV_ROW_H)


def render_tegnprotokoll_preview(
    items: List[Path],
    descriptions: dict,
    page_index: int = 0,
) -> QImage:
    """Render a scaled A4 preview of a Tegnprotokoll page (3-column table)."""
    if not items:
        return QImage()

    w, h = _TEGN_PREV_W, _TEGN_PREV_H
    margin = _TEGN_PREV_MARGIN
    row_h = _TEGN_PREV_ROW_H
    hdr_h = _TEGN_PREV_HDR_H
    title_h = _TEGN_PREV_TITLE_H
    footer_h = _TEGN_PREV_FOOTER_H

    ipp = _tegn_items_per_page()
    start = page_index * ipp
    page_items = items[start : start + ipp]

    usable_w = w - 2 * margin
    raw_ws = [int(f * usable_w) for f in _TEGN_PREV_COL_FRACS]
    raw_ws[-1] = usable_w - sum(raw_ws[:-1])  # fix rounding
    w_word, w_img, w_desc = raw_ws

    fsize_title = max(10, title_h // 2)
    fsize_hdr = max(8, hdr_h // 2 - 1)
    fsize_word = max(10, row_h // 4)
    fsize_desc = max(8, row_h // 6)
    fsize_foot = max(7, footer_h // 2 - 1)

    font_title = _preview_font(fsize_title)
    font_hdr = _preview_font(fsize_hdr)
    font_word = _preview_font(fsize_word)
    font_desc = _preview_font(fsize_desc)
    font_foot = _preview_font(fsize_foot)

    page = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(page)

    # ── Page title ────────────────────────────────────────────────────────────
    session_name = items[0].parent.name.replace("-", " ") if items else ""
    draw.text(
        (margin, margin + 4),
        t("Sign protocol — {name}", name=session_name),
        fill=(0, 0, 0),
        font=font_title,
    )

    # ── Column header ─────────────────────────────────────────────────────────
    table_x = margin
    table_top = margin + title_h
    draw.rectangle(
        [table_x, table_top, table_x + usable_w - 1, table_top + hdr_h - 1],
        fill=(215, 215, 215),
        outline=(160, 160, 160),
        width=1,
    )
    headers = [t("Sign"), t("Image"), t("How the child uses the sign")]
    hx = table_x
    for hdr, cw in zip(headers, raw_ws):
        try:
            bbox = font_hdr.getbbox(hdr)
            tw = bbox[2] - bbox[0]
        except AttributeError:
            tw, _ = font_hdr.getsize(hdr)  # type: ignore[attr-defined]
        draw.text(
            (hx + max(2, (cw - tw) // 2), table_top + 3),
            hdr,
            fill=(0, 0, 0),
            font=font_hdr,
        )
        hx += cw
    x1 = table_x + w_word
    x2 = table_x + w_word + w_img
    draw.line([x1, table_top, x1, table_top + hdr_h], fill=(160, 160, 160), width=1)
    draw.line([x2, table_top, x2, table_top + hdr_h], fill=(160, 160, 160), width=1)

    # ── Data rows ─────────────────────────────────────────────────────────────
    row_y = table_top + hdr_h
    for img_path in page_items:
        rb = row_y + row_h
        draw.rectangle(
            [table_x, row_y, table_x + usable_w - 1, rb - 1],
            outline=(190, 190, 190),
            width=1,
        )
        x1 = table_x + w_word
        x2 = table_x + w_word + w_img
        draw.line([x1, row_y, x1, rb], fill=(190, 190, 190), width=1)
        draw.line([x2, row_y, x2, rb], fill=(190, 190, 190), width=1)

        # Word
        label = stem_to_label(img_path.stem)
        try:
            bbox = font_word.getbbox(label)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            tw, th = font_word.getsize(label)  # type: ignore[attr-defined]
        draw.text(
            (table_x + max(2, (w_word - tw) // 2), row_y + max(2, (row_h - th) // 2)),
            label,
            fill=(0, 0, 0),
            font=font_word,
        )

        # Sign image
        pad = 4
        avail_w, avail_h = w_img - 2 * pad, row_h - 2 * pad
        try:
            thumb = to_rgb(Image.open(img_path))
            thumb.thumbnail((avail_w, avail_h), Image.LANCZOS)
            page.paste(
                thumb,
                (x1 + (w_img - thumb.width) // 2, row_y + (row_h - thumb.height) // 2),
            )
        except Exception:
            pass

        # Description
        stem = img_path.stem
        desc = descriptions.get(stem, "")
        if desc:
            # Truncate to fit single line
            display = desc
            try:
                bbox = font_desc.getbbox(display)
                tw = bbox[2] - bbox[0]
            except AttributeError:
                tw, _ = font_desc.getsize(display)  # type: ignore[attr-defined]
            max_desc_w = w_desc - 8
            while tw > max_desc_w and len(display) > 3:
                display = display[:-4] + "\u2026"
                try:
                    bbox = font_desc.getbbox(display)
                    tw = bbox[2] - bbox[0]
                except AttributeError:
                    tw, _ = font_desc.getsize(display)  # type: ignore[attr-defined]
            draw.text(
                (x2 + 4, row_y + max(2, (row_h - fsize_desc) // 2)),
                display,
                fill=(40, 40, 40),
                font=font_desc,
            )
        else:
            # Ruled lines for handwriting
            spacing = fsize_desc + 5
            ly = row_y + spacing
            while ly < rb - 4:
                draw.line(
                    [x2 + 6, ly, table_x + usable_w - 6, ly],
                    fill=(210, 210, 210),
                    width=1,
                )
                ly += spacing

        row_y = rb

    # ── Footer ────────────────────────────────────────────────────────────────
    footer_text = t("Illustrations: Statped / tegnbanken.no — CC BY-NC-ND 4.0")
    try:
        bbox = font_foot.getbbox(footer_text)
        fw = bbox[2] - bbox[0]
    except AttributeError:
        fw, _ = font_foot.getsize(footer_text)  # type: ignore[attr-defined]
    draw.text(
        (w // 2 - fw // 2, h - footer_h + 2),
        footer_text,
        fill=(160, 160, 160),
        font=font_foot,
    )

    return pillow_to_qimage(page)


def render_lotto_preview(images: List[Path], page_index: int = 0) -> QImage:
    """Render a scaled A4-proportioned preview of a lotto page (label at bottom, 4 cols)."""
    if not images:
        return QImage()

    card, gap, margin, cols = (
        _LOTTO_PREV_CARD,
        _LOTTO_PREV_GAP,
        _LOTTO_PREV_MARGIN,
        _LOTTO_PREV_COLS,
    )
    page_w = cols * card + (cols - 1) * gap + 2 * margin
    page_h = int(page_w * (297 / 210))

    rows_per_page = max(1, (page_h - 2 * margin + gap) // (card + gap))
    cards_per_page = rows_per_page * cols
    start = page_index * cards_per_page
    first_page = images[start : start + cards_per_page]

    label_h = max(16, card // 8)
    font_size = max(8, label_h - 6)
    font = _preview_font(font_size)

    page = Image.new("RGB", (page_w, page_h), (255, 255, 255))
    draw = ImageDraw.Draw(page)

    for idx, img_path in enumerate(first_page):
        row, col = divmod(idx, cols)
        cx = margin + col * (card + gap)
        cy = margin + row * (card + gap)

        # Card border
        draw.rectangle(
            [cx, cy, cx + card - 1, cy + card - 1], outline=(0, 0, 0), width=1
        )

        # Thumbnail in the image area (above label)
        pad = 3
        img_area_h = card - label_h - pad  # height from cy to start of label
        iw, ih = card - 2 * pad, img_area_h
        try:
            thumb = to_rgb(Image.open(img_path))
            thumb.thumbnail((iw, ih), Image.LANCZOS)
            page.paste(
                thumb,
                (
                    cx + pad + (iw - thumb.width) // 2,
                    cy + pad + (img_area_h - thumb.height) // 2,
                ),
            )
        except Exception:
            pass

        # Label background + text at bottom of card
        draw.rectangle(
            [cx, cy + card - label_h, cx + card - 1, cy + card - 1],
            fill=(240, 240, 240),
            outline=(180, 180, 180),
            width=1,
        )
        label = stem_to_label(img_path.stem)
        try:
            bbox = font.getbbox(label)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            tw, th = font.getsize(label)  # type: ignore[attr-defined]
        draw.text(
            (
                cx + max(2, (card - tw) // 2),
                cy + card - label_h + max(1, (label_h - th) // 2),
            ),
            label,
            fill=(0, 0, 0),
            font=font,
        )

    _draw_preview_credit(draw, page_w, page_h, margin)
    return pillow_to_qimage(page)

