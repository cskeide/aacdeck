#!/usr/bin/env python3
"""AACdeck — desktop GUI

Dependencies: PySide6, Pillow, reportlab  (see requirements.txt)
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Callable, List, Optional, Set

import make_cards
import make_lotto
import make_tegnprotokoll
from PIL import Image, ImageDraw, ImageFont
from pdf_utils import (
    ARASAAC_CREDIT,
    IMAGE_EXTS,
    carry_arasaac_record,
    open_file,
    record_arasaac_image,
    safe_stem,
    stem_to_label,
    to_rgb,
    uses_arasaac,
)


def _pillow_to_qimage(img: Image.Image) -> "QImage":
    """Convert a Pillow RGB image to QImage without a PNG encode/decode round-trip.

    Returns a QImage rather than a QPixmap so the conversion is safe to run on a
    worker thread — QPixmap may only be touched from the main (GUI) thread.
    Callers convert with QPixmap.fromImage() in a main-thread slot.
    """
    raw = img.tobytes("raw", "RGB")
    qimg = QImage(
        raw, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888
    )
    # QImage does not take ownership of `raw`; copy so the pixel data survives
    # after this function returns and `raw` is garbage-collected.
    return qimg.copy()


from PySide6.QtCore import QSize, QStandardPaths, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# ── Paths ──────────────────────────────────────────────────────────────────────
# The packaged app keeps user data in ~/Documents/AACdeck: the executable's own
# folder may be read-only (macOS app translocation), protected (Program Files),
# or left behind when a new version is downloaded. From source, data stays in
# the project folder so "python app.py" and the CLIs share sessions.
def _documents_dir() -> Path:
    loc = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )
    return Path(loc) if loc else Path.home() / "Documents"


if getattr(sys, "frozen", False):
    BASE_DIR = _documents_dir() / "AACdeck"
    # Releases before 2026-10 stored data next to the executable.
    _LEGACY_DIR: Optional[Path] = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent
    _LEGACY_DIR = None

SESSIONS_DIR = BASE_DIR / "sessions"
LOTTO_SESSIONS_DIR = BASE_DIR / "lotto-sessions"
TEGNPROTOKOLL_SESSIONS_DIR = BASE_DIR / "tegnprotokoll-sessions"
OUTPUT_DIR = BASE_DIR / "output"

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
    try:
        bbox = font.getbbox(ARASAAC_CREDIT)
        tw = bbox[2] - bbox[0]
    except AttributeError:
        tw, _ = font.getsize(ARASAAC_CREDIT)  # type: ignore[attr-defined]
    draw.text(
        (page_w // 2 - tw // 2, page_h - margin + 2),
        ARASAAC_CREDIT,
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
    return _pillow_to_qimage(page)


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
        f"Sign Protocol \u2014 {session_name}",
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
    headers = ["Sign", "Image", "How the child uses the sign"]
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
    footer_text = "Statped / tegnbanken.no \u2014 CC BY-NC-ND 4.0"
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

    return _pillow_to_qimage(page)


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
    return _pillow_to_qimage(page)


# ── Worker threads ─────────────────────────────────────────────────────────────
class GenerateWorker(QThread):
    done = Signal(str)  # path to generated PDF
    error = Signal(str)  # error message

    def __init__(self, session_path: Path, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session_path = session_path

    def run(self) -> None:
        try:
            # Pass OUTPUT_DIR directly so make_cards always writes to the right
            # place — including inside a PyInstaller onefile bundle where
            # make_cards.__file__ resolves to sys._MEIPASS (a temp dir).
            out_path = make_cards.make_cards(str(self.session_path), OUTPUT_DIR)
            self.done.emit(str(out_path))
        except Exception as exc:
            self.error.emit(str(exc))


class PreviewWorker(QThread):
    # QImage, not QPixmap — run() executes off the main thread, and QPixmap may
    # only be constructed there.  The receiving slot does QPixmap.fromImage().
    ready = Signal(QImage)

    def __init__(
        self, images: List[Path], page_index: int = 0, parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self.images = images
        self.page_index = page_index

    def run(self) -> None:
        self.ready.emit(render_page_preview(self.images, self.page_index))


class LottoPreviewWorker(QThread):
    ready = Signal(QImage)  # see PreviewWorker.ready

    def __init__(
        self, images: List[Path], page_index: int = 0, parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self.images = images
        self.page_index = page_index

    def run(self) -> None:
        self.ready.emit(render_lotto_preview(self.images, self.page_index))


class ArasaacSearchWorker(QThread):
    """Fetch ARASAAC search results + 300 px thumbnails for a query."""

    results = Signal(list)  # list[dict] with thumb_bytes filled in
    error = Signal(str)

    def __init__(self, query: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.query = query

    def run(self) -> None:
        try:
            import arasaac
            from concurrent.futures import ThreadPoolExecutor

            items = arasaac.search(self.query)

            def _fetch_thumb(r: dict) -> dict:
                try:
                    r["thumb_bytes"] = arasaac.fetch_image(r["id"], resolution=300)
                except Exception:
                    r["thumb_bytes"] = None
                return r

            with ThreadPoolExecutor(max_workers=8) as pool:
                items = list(pool.map(_fetch_thumb, items))
            self.results.emit(items)
        except Exception as exc:
            self.error.emit(str(exc))


class ArasaacDownloadWorker(QThread):
    """Download a single pictogram PNG and save it to the session directory."""

    done = Signal(str)  # path to saved file
    error = Signal(str)

    def __init__(
        self,
        pic_id: int,
        label: str,
        session_path: Path,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.pic_id = pic_id
        self.label = label
        self.session_path = session_path

    def run(self) -> None:
        try:
            import arasaac

            data = arasaac.fetch_image(self.pic_id, resolution=500)
            stem = safe_stem(self.label)
            dest = self.session_path / f"{stem}.png"
            counter = 2
            while dest.exists():
                dest = self.session_path / f"{stem}__{counter}.png"
                counter += 1
            dest.write_bytes(data)
            record_arasaac_image(dest, self.pic_id)
            self.done.emit(str(dest))
        except Exception as exc:
            self.error.emit(str(exc))


class LottoBoardWorker(QThread):
    """Generate both board and cut-out PDFs for a lotto session."""

    done = Signal(str, str)  # (board_path, cutout_path)
    error = Signal(str)

    def __init__(self, session_path: Path, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session_path = session_path

    def run(self) -> None:
        try:
            board, cutout = make_lotto.make_board_and_cutout_pdf(
                str(self.session_path), OUTPUT_DIR
            )
            self.done.emit(str(board), str(cutout))
        except Exception as exc:
            self.error.emit(str(exc))


class TegnprotokollSearchWorker(QThread):
    """Search Tegnbanken records (client-side, cached XML)."""

    results = Signal(list)
    error = Signal(str)

    def __init__(self, query: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.query = query

    def run(self) -> None:
        try:
            import tegnbanken

            self.results.emit(tegnbanken.search(self.query))
        except Exception as exc:
            self.error.emit(str(exc))


class TegnprotokollDownloadWorker(QThread):
    """Download a sign image from Tegnbanken and save it to the session folder."""

    done = Signal(str)  # path to saved file
    error = Signal(str)

    def __init__(
        self,
        record: dict,
        session_path: Path,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.record = record
        self.session_path = session_path

    def run(self) -> None:
        try:
            import tegnbanken

            word = self.record["word"]
            foto = self.record.get("foto", "")
            la_hend = self.record.get("la_hend", "")
            stem = safe_stem(word)

            data: Optional[bytes] = None
            ext = ".jpg"

            # Prefer la_hend (strektegning / line drawing) over foto (colour photo)
            if la_hend:
                try:
                    data = tegnbanken.fetch_image(la_hend, "la_hend")
                    ext = Path(la_hend).suffix or ".jpg"
                except Exception:
                    pass

            if data is None and foto:
                try:
                    data = tegnbanken.fetch_image(foto, "foto")
                    ext = Path(foto).suffix or ".jpg"
                except Exception:
                    pass

            if data is None:
                # No image available — generate a simple placeholder
                ph = Image.new("RGB", (300, 300), (230, 230, 230))
                ph_draw = ImageDraw.Draw(ph)
                ph_draw.text((150, 150), word, fill=(100, 100, 100), anchor="mm")
                buf = io.BytesIO()
                ph.save(buf, format="PNG")
                data = buf.getvalue()
                ext = ".png"

            dest = self.session_path / f"{stem}{ext}"
            if dest.exists():
                idx = 2
                while dest.exists():
                    dest = self.session_path / f"{stem}_{idx}{ext}"
                    idx += 1
            dest.write_bytes(data)
            self.done.emit(str(dest))
        except Exception as exc:
            self.error.emit(str(exc))


class TegnprotokollPdfWorker(QThread):
    """Generate a Tegnprotokoll PDF in a background thread."""

    done = Signal(str)
    error = Signal(str)

    def __init__(self, session_path: Path, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session_path = session_path

    def run(self) -> None:
        try:
            import make_tegnprotokoll

            out = make_tegnprotokoll.make_tegnprotokoll(
                str(self.session_path), OUTPUT_DIR
            )
            self.done.emit(str(out))
        except Exception as exc:
            self.error.emit(str(exc))


class TegnprotokollPreviewWorker(QThread):
    """Render a Tegnprotokoll table preview page using Pillow."""

    ready = Signal(QImage)  # see PreviewWorker.ready

    def __init__(
        self,
        items: List[Path],
        descriptions: dict,
        page_index: int = 0,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.items = items
        self.descriptions = descriptions
        self.page_index = page_index

    def run(self) -> None:
        self.ready.emit(
            render_tegnprotokoll_preview(self.items, self.descriptions, self.page_index)
        )


# ── Drag-and-drop image list ───────────────────────────────────────────────────
class ImageDropList(QListWidget):
    """Icon-mode list that accepts dropped image files."""

    files_dropped = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.DropOnly)
        self.setIconSize(QSize(90, 90))
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setSpacing(6)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls() and any(
            Path(u.toLocalFile()).suffix.lower() in IMAGE_EXTS
            for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths = [
            Path(u.toLocalFile())
            for u in event.mimeData().urls()
            if Path(u.toLocalFile()).suffix.lower() in IMAGE_EXTS
        ]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()


# ── Lotto tab ──────────────────────────────────────────────────────────────────
class PictogramSearchPanel(QWidget):
    """ARASAAC search box and results that download picks into a session.

    Shared by the Cards and Lotto tabs. *session_getter* returns the session to
    download into, or None when no session is selected. Emits ``downloaded``
    with the saved path once each image is written (and recorded in the
    session's ARASAAC manifest by the worker).
    """

    downloaded = Signal(str)

    def __init__(
        self,
        session_getter: Callable[[], Optional[Path]],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._session_getter = session_getter
        self._search_worker: Optional[ArasaacSearchWorker] = None
        self._download_workers: Set[ArasaacDownloadWorker] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        sep_lbl = QLabel("Search Pictograms")
        sep_lbl.setStyleSheet("font-weight: bold; font-size: 13px; padding: 8px 0 2px;")
        layout.addWidget(sep_lbl)

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search in English or Norwegian…")
        self.search_input.returnPressed.connect(self._do_search)
        search_row.addWidget(self.search_input, stretch=1)
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._do_search)
        search_row.addWidget(self.search_btn)
        layout.addLayout(search_row)

        self.search_status = QLabel("")
        self.search_status.setStyleSheet("color: gray; font-size: 11px;")
        self.search_status.setWordWrap(True)
        layout.addWidget(self.search_status)

        self.result_list = QListWidget()
        self.result_list.setIconSize(QSize(70, 70))
        self.result_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.result_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.result_list.setSpacing(4)
        self.result_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.result_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.result_list.customContextMenuRequested.connect(self._result_context_menu)
        layout.addWidget(self.result_list, stretch=1)

        self.add_btn = QPushButton("Add selected to session")
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self._add_selected)
        layout.addWidget(self.add_btn)

    # ── Search ─────────────────────────────────────────────────────────────────

    def _do_search(self) -> None:
        query = self.search_input.text().strip()
        if not query or self._search_worker is not None:
            return
        self.search_btn.setEnabled(False)
        self.result_list.clear()
        self.add_btn.setEnabled(False)
        self.search_status.setText("Searching…")

        worker = ArasaacSearchWorker(query)
        worker.results.connect(self._on_search_results)
        worker.error.connect(self._on_search_error)
        worker.finished.connect(self._on_search_finished)
        worker.finished.connect(worker.deleteLater)
        self._search_worker = worker
        worker.start()

    def _on_search_finished(self) -> None:
        self._search_worker = None
        self.search_btn.setEnabled(True)

    def _on_search_results(self, results: list) -> None:
        self.result_list.clear()
        if not results:
            self.search_status.setText("No results.")
            return
        self.search_status.setText(f"{len(results)} result(s)")
        for r in results:
            item = QListWidgetItem(r["label"])
            item.setData(Qt.ItemDataRole.UserRole, r)
            if r.get("thumb_bytes"):
                pix = QPixmap()
                pix.loadFromData(r["thumb_bytes"])
                if not pix.isNull():
                    item.setIcon(QIcon(pix))
            item.setSizeHint(QSize(110, 120))
            self.result_list.addItem(item)
        self.add_btn.setEnabled(True)

    def _on_search_error(self, msg: str) -> None:
        self.search_status.setText(f"Error: {msg}")

    # ── Download ───────────────────────────────────────────────────────────────

    def _result_context_menu(self, pos) -> None:
        item = self.result_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        add_action = menu.addAction("Add to session")
        if menu.exec(self.result_list.mapToGlobal(pos)) == add_action:
            session = self._session_or_warn()
            if session is not None:
                r = item.data(Qt.ItemDataRole.UserRole)
                self._start_download(session, r["id"], r["label"])

    def _add_selected(self) -> None:
        session = self._session_or_warn()
        if session is None:
            return
        for item in self.result_list.selectedItems():
            r = item.data(Qt.ItemDataRole.UserRole)
            self._start_download(session, r["id"], r["label"])

    def _session_or_warn(self) -> Optional[Path]:
        session = self._session_getter()
        if session is None:
            QMessageBox.warning(
                self, "No session", "Please select or create a session first."
            )
        return session

    def _start_download(self, session: Path, pic_id: int, label: str) -> None:
        worker = ArasaacDownloadWorker(pic_id, label, session)
        worker.done.connect(self._on_download_done)
        worker.error.connect(self._on_download_error)
        worker.finished.connect(lambda w=worker: self._download_workers.discard(w))
        worker.finished.connect(worker.deleteLater)
        self._download_workers.add(worker)
        worker.start()

    def _on_download_done(self, path: str) -> None:
        self.downloaded.emit(path)

    def _on_download_error(self, msg: str) -> None:
        self.search_status.setText(f"Download error: {msg}")


class LottoTab(QWidget):
    """Tab for searching ARASAAC pictograms, building lotto sessions, and
    generating board + cut-out PDFs."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self.current_lotto_session: Optional[Path] = None
        self._board_worker: Optional[LottoBoardWorker] = None
        self._preview_worker: Optional[LottoPreviewWorker] = None
        self._stale_preview_workers: Set[LottoPreviewWorker] = set()
        self._preview_images: List[Path] = []
        self._preview_page: int = 0
        self._preview_total_pages: int = 1
        self._last_board_pdf: Optional[str] = None
        self._last_cutout_pdf: Optional[str] = None

        LOTTO_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        self._build_ui()
        self._refresh_sessions()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, stretch=1)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_cards_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setSizes([300, 490, 300])

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(200)
        panel.setMaximumWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 6, 0)

        lbl = QLabel("Sessions")
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px 0;")
        layout.addWidget(lbl)

        self.lotto_session_list = QListWidget()
        self.lotto_session_list.currentItemChanged.connect(self._on_session_changed)
        layout.addWidget(self.lotto_session_list, stretch=1)

        new_btn = QPushButton("+ New session")
        new_btn.clicked.connect(self._new_session)
        layout.addWidget(new_btn)

        self.picto_search = PictogramSearchPanel(lambda: self.current_lotto_session)
        self.picto_search.downloaded.connect(self._on_download_done)
        layout.addWidget(self.picto_search, stretch=2)
        return panel

    def _build_cards_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 4, 0)

        self.lotto_session_title = QLabel("Select a session")
        self.lotto_session_title.setStyleSheet(
            "font-weight: bold; font-size: 13px; padding: 2px 0;"
        )
        layout.addWidget(self.lotto_session_title)

        self.lotto_image_list = QListWidget()
        self.lotto_image_list.setIconSize(QSize(90, 90))
        self.lotto_image_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.lotto_image_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.lotto_image_list.setSpacing(6)
        self.lotto_image_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.lotto_image_list.customContextMenuRequested.connect(
            self._card_context_menu
        )
        layout.addWidget(self.lotto_image_list, stretch=1)
        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(200)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 0, 0)

        lbl = QLabel("Page preview")
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px 0;")
        layout.addWidget(lbl)

        self.lotto_preview_scroll = QScrollArea()
        self.lotto_preview_scroll.setWidgetResizable(True)
        self.lotto_preview_scroll.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self.lotto_preview_label = QLabel("No session selected")
        self.lotto_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lotto_preview_label.setWordWrap(True)
        self.lotto_preview_scroll.setWidget(self.lotto_preview_label)
        layout.addWidget(self.lotto_preview_scroll, stretch=1)

        nav = QHBoxLayout()
        nav.setContentsMargins(0, 2, 0, 0)
        self.lotto_prev_btn = QPushButton("← Prev")
        self.lotto_prev_btn.setEnabled(False)
        self.lotto_prev_btn.clicked.connect(self._prev_preview_page)
        nav.addWidget(self.lotto_prev_btn)
        self.lotto_page_label = QLabel("")
        self.lotto_page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.lotto_page_label, stretch=1)
        self.lotto_next_btn = QPushButton("Next →")
        self.lotto_next_btn.setEnabled(False)
        self.lotto_next_btn.clicked.connect(self._next_preview_page)
        nav.addWidget(self.lotto_next_btn)
        layout.addLayout(nav)

        self.lotto_status = QLabel("")
        self.lotto_status.setWordWrap(True)
        layout.addWidget(self.lotto_status)

        self.lotto_progress = QProgressBar()
        self.lotto_progress.setRange(0, 0)
        self.lotto_progress.setVisible(False)
        layout.addWidget(self.lotto_progress)

        self.generate_pdfs_btn = QPushButton("Generate PDFs")
        self.generate_pdfs_btn.setEnabled(False)
        self.generate_pdfs_btn.setMinimumWidth(140)
        self.generate_pdfs_btn.clicked.connect(self._generate_pdfs)
        layout.addWidget(self.generate_pdfs_btn)

        open_row = QHBoxLayout()
        self.open_board_btn = QPushButton("Open board PDF")
        self.open_board_btn.setVisible(False)
        self.open_board_btn.clicked.connect(
            lambda: self._open_pdf(self._last_board_pdf)
        )
        open_row.addWidget(self.open_board_btn)
        self.open_cutout_btn = QPushButton("Open cut-out PDF")
        self.open_cutout_btn.setVisible(False)
        self.open_cutout_btn.clicked.connect(
            lambda: self._open_pdf(self._last_cutout_pdf)
        )
        open_row.addWidget(self.open_cutout_btn)
        layout.addLayout(open_row)
        return panel

    # ── Session management ─────────────────────────────────────────────────────

    def _refresh_sessions(self) -> None:
        current_name: Optional[str] = None
        if self.lotto_session_list.currentItem():
            p: Path = self.lotto_session_list.currentItem().data(
                Qt.ItemDataRole.UserRole
            )
            current_name = p.name

        self.lotto_session_list.clear()
        if not LOTTO_SESSIONS_DIR.exists():
            return

        restore_item: Optional[QListWidgetItem] = None
        for s in sorted(p for p in LOTTO_SESSIONS_DIR.iterdir() if p.is_dir()):
            count = sum(1 for f in s.iterdir() if f.suffix.lower() in IMAGE_EXTS)
            item = QListWidgetItem(f"{s.name}  ({count})")
            item.setData(Qt.ItemDataRole.UserRole, s)
            self.lotto_session_list.addItem(item)
            if s.name == current_name:
                restore_item = item

        if restore_item:
            self.lotto_session_list.setCurrentItem(restore_item)

    def _on_session_changed(self, current: QListWidgetItem, _previous) -> None:
        if current is None:
            self.current_lotto_session = None
            self.lotto_session_title.setText("Select a session")
            self.lotto_image_list.clear()
            self.generate_pdfs_btn.setEnabled(False)
            self.lotto_preview_label.setText("No session selected")
            self._preview_images = []
            self._preview_page = 0
            self._preview_total_pages = 1
            self._update_nav_buttons()
            return

        self.current_lotto_session = current.data(Qt.ItemDataRole.UserRole)
        self.lotto_session_title.setText(self.current_lotto_session.name)
        self._preview_page = 0
        self._load_session_images()

    def _new_session(self) -> None:
        name, ok = QInputDialog.getText(
            self, "New lotto session", "Session name (e.g. 2026-04-lotto-animals):"
        )
        if not ok or not name.strip():
            return
        name = name.strip().replace(" ", "-")
        new_path = LOTTO_SESSIONS_DIR / name
        if new_path.exists():
            QMessageBox.warning(
                self, "Already exists", f"Session '{name}' already exists."
            )
            return
        new_path.mkdir(parents=True)
        self._refresh_sessions()
        for i in range(self.lotto_session_list.count()):
            item = self.lotto_session_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == new_path:
                self.lotto_session_list.setCurrentItem(item)
                break

    # ── Image / card management ────────────────────────────────────────────────

    def _load_session_images(self) -> None:
        self.lotto_image_list.clear()
        if self.current_lotto_session is None:
            return

        images = sorted(
            p
            for p in self.current_lotto_session.iterdir()
            if p.suffix.lower() in IMAGE_EXTS
        )
        for img_path in images:
            item = QListWidgetItem(
                QIcon(self._make_thumb(img_path)),
                stem_to_label(img_path.stem),
            )
            item.setData(Qt.ItemDataRole.UserRole, img_path)
            item.setSizeHint(QSize(110, 120))
            self.lotto_image_list.addItem(item)

        self.generate_pdfs_btn.setEnabled(bool(images))
        self._schedule_preview(images)

    def _make_thumb(self, img_path: Path) -> QPixmap:
        try:
            img = to_rgb(Image.open(img_path))
            img.thumbnail((96, 96), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return QPixmap.fromImage(QImage.fromData(buf.read()))
        except Exception:
            return QPixmap()

    def _card_context_menu(self, pos) -> None:
        item = self.lotto_image_list.itemAt(pos)
        if item is None:
            return
        img_path: Path = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        rename_action = menu.addAction("Rename…")
        remove_action = menu.addAction("Remove from session")
        action = menu.exec(self.lotto_image_list.mapToGlobal(pos))
        if action == rename_action:
            current_label = stem_to_label(img_path.stem)
            new_label, ok = QInputDialog.getText(
                self, "Rename card", "Card label:", text=current_label
            )
            if ok and new_label.strip() and new_label.strip() != current_label:
                new_stem = new_label.strip().replace(" ", "_")
                new_path = img_path.with_stem(new_stem)
                if new_path.exists():
                    QMessageBox.warning(
                        self, "Name taken", f"'{new_path.name}' already exists."
                    )
                else:
                    img_path.rename(new_path)
                    carry_arasaac_record(img_path, new_path)
                    self._load_session_images()
        elif action == remove_action:
            if (
                QMessageBox.question(
                    self,
                    "Remove card",
                    f"Delete '{img_path.name}' from this session?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                == QMessageBox.StandardButton.Yes
            ):
                img_path.unlink(missing_ok=True)
                self._refresh_sessions()
                self._load_session_images()

    # ── Preview ────────────────────────────────────────────────────────────────

    def _schedule_preview(self, images: List[Path]) -> None:
        self._preview_images = images
        cpp = _lotto_cards_per_page()
        self._preview_total_pages = max(1, -(-len(images) // cpp) if images else 1)
        self._update_nav_buttons()
        self._update_preview()

    def _update_preview(self) -> None:
        if self._preview_worker is not None:
            try:
                self._preview_worker.ready.disconnect()
            except RuntimeError:
                pass
            # Keep the old thread alive in the stale list until it finishes
            old = self._preview_worker
            self._stale_preview_workers.add(old)
            old.finished.connect(lambda w=old: self._stale_preview_workers.discard(w))
        self.lotto_preview_label.setText("Rendering…")
        worker = LottoPreviewWorker(self._preview_images, self._preview_page)
        worker.ready.connect(self._on_preview_ready)
        self._preview_worker = worker
        worker.start()

    def _update_nav_buttons(self) -> None:
        total = self._preview_total_pages
        page = self._preview_page
        self.lotto_prev_btn.setEnabled(page > 0)
        self.lotto_next_btn.setEnabled(page < total - 1)
        self.lotto_page_label.setText(
            f"Page {page + 1} / {total}" if self._preview_images else ""
        )

    def _prev_preview_page(self) -> None:
        if self._preview_page > 0:
            self._preview_page -= 1
            self._update_nav_buttons()
            self._update_preview()

    def _next_preview_page(self) -> None:
        if self._preview_page < self._preview_total_pages - 1:
            self._preview_page += 1
            self._update_nav_buttons()
            self._update_preview()

    def _on_preview_ready(self, image: QImage) -> None:
        if image.isNull():
            self.lotto_preview_label.setText("No cards in session")
            return
        pixmap = QPixmap.fromImage(image)  # main thread — safe here
        max_w = max(100, self.lotto_preview_scroll.width() - 20)
        if pixmap.width() > max_w:
            pixmap = pixmap.scaledToWidth(
                max_w, Qt.TransformationMode.SmoothTransformation
            )
        self.lotto_preview_label.setPixmap(pixmap)

    # ── Downloads ──────────────────────────────────────────────────────────────

    def _on_download_done(self, _img_path: str) -> None:
        self._refresh_sessions()
        self._load_session_images()

    # ── PDF generation ─────────────────────────────────────────────────────────

    def _generate_pdfs(self) -> None:
        if self.current_lotto_session is None or self._board_worker is not None:
            return
        self.generate_pdfs_btn.setEnabled(False)
        self.open_board_btn.setVisible(False)
        self.open_cutout_btn.setVisible(False)
        self.lotto_progress.setVisible(True)
        self.lotto_status.setText("Generating PDFs…")

        worker = LottoBoardWorker(self.current_lotto_session)
        worker.done.connect(self._on_generate_done)
        worker.error.connect(self._on_generate_error)
        worker.finished.connect(worker.deleteLater)
        self._board_worker = worker
        worker.start()

    def _on_generate_done(self, board_path: str, cutout_path: str) -> None:
        self._board_worker = None
        self.lotto_progress.setVisible(False)
        self.generate_pdfs_btn.setEnabled(bool(self._preview_images))
        self._last_board_pdf = board_path
        self._last_cutout_pdf = cutout_path
        self.open_board_btn.setVisible(True)
        self.open_cutout_btn.setVisible(True)
        self.lotto_status.setText(
            f"Saved: {Path(board_path).name} and {Path(cutout_path).name}"
        )

    def _on_generate_error(self, msg: str) -> None:
        self._board_worker = None
        self.lotto_progress.setVisible(False)
        self.generate_pdfs_btn.setEnabled(bool(self._preview_images))
        self.lotto_status.setText(f"Error: {msg}")
        QMessageBox.critical(self, "Generation failed", msg)

    def _open_pdf(self, path: Optional[str]) -> None:
        if path:
            open_file(path)

    # ── Session management ─────────────────────────────────────────────────────

    def _refresh_sessions(self) -> None:
        current_name: Optional[str] = None
        if self.lotto_session_list.currentItem():
            p: Path = self.lotto_session_list.currentItem().data(
                Qt.ItemDataRole.UserRole
            )
            current_name = p.name

        self.lotto_session_list.clear()
        if not LOTTO_SESSIONS_DIR.exists():
            return

        restore_item: Optional[QListWidgetItem] = None
        for s in sorted(p for p in LOTTO_SESSIONS_DIR.iterdir() if p.is_dir()):
            count = sum(1 for f in s.iterdir() if f.suffix.lower() in IMAGE_EXTS)
            item = QListWidgetItem(f"{s.name}  ({count})")
            item.setData(Qt.ItemDataRole.UserRole, s)
            self.lotto_session_list.addItem(item)
            if s.name == current_name:
                restore_item = item

        if restore_item:
            self.lotto_session_list.setCurrentItem(restore_item)


class TegnprotokollTab(QWidget):
    """Tab for building a sign protocol (Tegnprotokoll): search Tegnbanken,
    add sign images, annotate with per-sign descriptions, generate a
    3-column A4 PDF (Word | Sign image | Child's usage description)."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self.current_session: Optional[Path] = None
        self._descriptions: dict = {}
        self._session_items: List[Path] = []
        self._search_worker: Optional[TegnprotokollSearchWorker] = None
        self._download_workers: Set[TegnprotokollDownloadWorker] = set()
        self._pdf_worker: Optional[TegnprotokollPdfWorker] = None
        self._preview_worker: Optional[TegnprotokollPreviewWorker] = None
        self._stale_preview_workers: Set[TegnprotokollPreviewWorker] = set()
        self._preview_page: int = 0
        self._preview_total_pages: int = 1
        self._last_pdf: Optional[str] = None

        TEGNPROTOKOLL_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        self._build_ui()
        self._refresh_sessions()

    # ── UI construction ───────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, stretch=1)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_signs_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setSizes([300, 490, 300])

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(200)
        panel.setMaximumWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 6, 0)

        lbl = QLabel("Sessions")
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px 0;")
        layout.addWidget(lbl)

        self.tegn_session_list = QListWidget()
        self.tegn_session_list.currentItemChanged.connect(self._on_session_changed)
        layout.addWidget(self.tegn_session_list, stretch=1)

        new_btn = QPushButton("+ New session")
        new_btn.clicked.connect(self._new_session)
        layout.addWidget(new_btn)

        sep_row = QHBoxLayout()
        sep = QLabel("Search Signs")
        sep.setStyleSheet("font-weight: bold; font-size: 13px; padding: 8px 0 2px;")
        sep_row.addWidget(sep, stretch=1)
        refresh_btn = QPushButton("Refresh database")
        refresh_btn.setToolTip("Clear cache and reload sign data")
        refresh_btn.clicked.connect(self._refresh_tegnbank)
        sep_row.addWidget(refresh_btn)
        layout.addLayout(sep_row)

        search_row = QHBoxLayout()
        self.tegn_search_input = QLineEdit()
        self.tegn_search_input.setPlaceholderText("Search for signs…")
        self.tegn_search_input.returnPressed.connect(self._do_search)
        search_row.addWidget(self.tegn_search_input, stretch=1)
        self.tegn_search_btn = QPushButton("Search")
        self.tegn_search_btn.clicked.connect(self._do_search)
        search_row.addWidget(self.tegn_search_btn)
        layout.addLayout(search_row)

        self.tegn_search_status = QLabel("")
        self.tegn_search_status.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.tegn_search_status)

        self.tegn_result_list = QListWidget()
        self.tegn_result_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
        )
        self.tegn_result_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.tegn_result_list.customContextMenuRequested.connect(
            self._result_context_menu
        )
        layout.addWidget(self.tegn_result_list, stretch=2)

        self.tegn_add_btn = QPushButton("Add selected to session")
        self.tegn_add_btn.setEnabled(False)
        self.tegn_add_btn.clicked.connect(self._add_selected)
        layout.addWidget(self.tegn_add_btn)
        return panel

    def _build_signs_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 4, 0)

        self.tegn_session_title = QLabel("Select a session")
        self.tegn_session_title.setStyleSheet(
            "font-weight: bold; font-size: 13px; padding: 2px 0;"
        )
        layout.addWidget(self.tegn_session_title)

        self.tegn_signs_list = QListWidget()
        self.tegn_signs_list.setIconSize(QSize(90, 90))
        self.tegn_signs_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.tegn_signs_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.tegn_signs_list.setSpacing(6)
        self.tegn_signs_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.tegn_signs_list.customContextMenuRequested.connect(self._sign_context_menu)
        layout.addWidget(self.tegn_signs_list, stretch=1)
        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(200)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 0, 0)

        lbl = QLabel("Page preview")
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px 0;")
        layout.addWidget(lbl)

        self.tegn_preview_scroll = QScrollArea()
        self.tegn_preview_scroll.setWidgetResizable(True)
        self.tegn_preview_scroll.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self.tegn_preview_label = QLabel("No session selected")
        self.tegn_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tegn_preview_label.setWordWrap(True)
        self.tegn_preview_scroll.setWidget(self.tegn_preview_label)
        layout.addWidget(self.tegn_preview_scroll, stretch=1)

        nav = QHBoxLayout()
        nav.setContentsMargins(0, 2, 0, 0)
        self.tegn_prev_btn = QPushButton("← Prev")
        self.tegn_prev_btn.setEnabled(False)
        self.tegn_prev_btn.clicked.connect(self._prev_page)
        nav.addWidget(self.tegn_prev_btn)
        self.tegn_page_label = QLabel("")
        self.tegn_page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.tegn_page_label, stretch=1)
        self.tegn_next_btn = QPushButton("Next →")
        self.tegn_next_btn.setEnabled(False)
        self.tegn_next_btn.clicked.connect(self._next_page)
        nav.addWidget(self.tegn_next_btn)
        layout.addLayout(nav)

        self.tegn_status = QLabel("")
        self.tegn_status.setWordWrap(True)
        layout.addWidget(self.tegn_status)

        self.tegn_progress = QProgressBar()
        self.tegn_progress.setRange(0, 0)
        self.tegn_progress.setVisible(False)
        layout.addWidget(self.tegn_progress)

        self.tegn_generate_btn = QPushButton("Generate Sign Protocol PDF")
        self.tegn_generate_btn.setEnabled(False)
        self.tegn_generate_btn.setMinimumWidth(160)
        self.tegn_generate_btn.clicked.connect(self._generate_pdf)
        layout.addWidget(self.tegn_generate_btn)

        self.tegn_open_btn = QPushButton("Open PDF")
        self.tegn_open_btn.setVisible(False)
        self.tegn_open_btn.clicked.connect(lambda: self._open_pdf(self._last_pdf))
        layout.addWidget(self.tegn_open_btn)
        return panel

    # ── Session management ─────────────────────────────────────────────────────

    def _refresh_sessions(self) -> None:
        current_name: Optional[str] = None
        if self.tegn_session_list.currentItem():
            p: Path = self.tegn_session_list.currentItem().data(
                Qt.ItemDataRole.UserRole
            )
            current_name = p.name

        self.tegn_session_list.clear()
        if not TEGNPROTOKOLL_SESSIONS_DIR.exists():
            return

        restore_item: Optional[QListWidgetItem] = None
        for s in sorted(p for p in TEGNPROTOKOLL_SESSIONS_DIR.iterdir() if p.is_dir()):
            count = sum(1 for f in s.iterdir() if f.suffix.lower() in IMAGE_EXTS)
            item = QListWidgetItem(f"{s.name}  ({count})")
            item.setData(Qt.ItemDataRole.UserRole, s)
            self.tegn_session_list.addItem(item)
            if s.name == current_name:
                restore_item = item

        if restore_item:
            self.tegn_session_list.setCurrentItem(restore_item)

    def _on_session_changed(self, current: QListWidgetItem, _previous) -> None:
        if current is None:
            self.current_session = None
            self._descriptions = {}
            self._session_items = []
            self.tegn_session_title.setText("Select a session")
            self.tegn_signs_list.clear()
            self.tegn_generate_btn.setEnabled(False)
            self.tegn_preview_label.setText("No session selected")
            self._preview_page = 0
            self._preview_total_pages = 1
            self._update_nav_buttons()
            return

        self.current_session = current.data(Qt.ItemDataRole.UserRole)
        self.tegn_session_title.setText(self.current_session.name)
        self._preview_page = 0
        self._load_descriptions()
        self._load_session_items()

    def _new_session(self) -> None:
        name, ok = QInputDialog.getText(
            self,
            "New sign protocol session",
            "Session name (e.g. 2026-04-signs-home):",
        )
        if not ok or not name.strip():
            return
        name = name.strip().replace(" ", "-")
        new_path = TEGNPROTOKOLL_SESSIONS_DIR / name
        if new_path.exists():
            QMessageBox.warning(
                self,
                "Already exists",
                f"Session '{name}' already exists.",
            )
            return
        new_path.mkdir(parents=True)
        self._refresh_sessions()
        for i in range(self.tegn_session_list.count()):
            item = self.tegn_session_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == new_path:
                self.tegn_session_list.setCurrentItem(item)
                break

    # ── Descriptions sidecar ────────────────────────────────────────────────────

    def _load_descriptions(self) -> None:
        self._descriptions = {}
        if self.current_session is None:
            return
        desc_file = self.current_session / "descriptions.json"
        if desc_file.exists():
            try:
                self._descriptions = json.loads(desc_file.read_text("utf-8"))
            except Exception:
                self._descriptions = {}

    def _save_descriptions(self) -> None:
        if self.current_session is None:
            return
        desc_file = self.current_session / "descriptions.json"
        try:
            desc_file.write_text(
                json.dumps(self._descriptions, ensure_ascii=False, indent=2),
                "utf-8",
            )
        except Exception as exc:
            self.tegn_status.setText(f"Warning: could not save descriptions — {exc}")

    # ── Sign / image management ─────────────────────────────────────────────────

    def _load_session_items(self) -> None:
        self.tegn_signs_list.clear()
        if self.current_session is None:
            return
        images = sorted(
            p for p in self.current_session.iterdir() if p.suffix.lower() in IMAGE_EXTS
        )
        for img_path in images:
            item = QListWidgetItem(
                QIcon(self._make_thumb(img_path)),
                stem_to_label(img_path.stem),
            )
            item.setData(Qt.ItemDataRole.UserRole, img_path)
            item.setSizeHint(QSize(110, 120))
            self.tegn_signs_list.addItem(item)
        self.tegn_generate_btn.setEnabled(bool(images))
        self._session_items = images
        self._schedule_preview()

    def _make_thumb(self, img_path: Path) -> QPixmap:
        try:
            img = to_rgb(Image.open(img_path))
            img.thumbnail((96, 96), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return QPixmap.fromImage(QImage.fromData(buf.read()))
        except Exception:
            return QPixmap()

    def _sign_context_menu(self, pos) -> None:
        item = self.tegn_signs_list.itemAt(pos)
        if item is None:
            return
        img_path: Path = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        rename_action = menu.addAction("Rename…")
        desc_action = menu.addAction("Set description…")
        remove_action = menu.addAction("Remove from session")
        action = menu.exec(self.tegn_signs_list.mapToGlobal(pos))

        if action == rename_action:
            current_label = stem_to_label(img_path.stem)
            new_label, ok = QInputDialog.getText(
                self, "Rename sign", "Sign label:", text=current_label
            )
            if ok and new_label.strip() and new_label.strip() != current_label:
                old_stem = img_path.stem
                new_stem = new_label.strip().replace(" ", "_")
                new_path = img_path.with_stem(new_stem)
                if new_path.exists():
                    QMessageBox.warning(
                        self,
                        "Name taken",
                        f"'{new_path.name}' already exists.",
                    )
                else:
                    img_path.rename(new_path)
                    if old_stem in self._descriptions:
                        self._descriptions[new_stem] = self._descriptions.pop(old_stem)
                        self._save_descriptions()
                    self._load_session_items()

        elif action == desc_action:
            stem = img_path.stem
            current_desc = self._descriptions.get(stem, "")
            new_desc, ok = QInputDialog.getText(
                self,
                "Set description",
                "Describe how the child uses the sign:",
                text=current_desc,
            )
            if ok:
                if new_desc.strip():
                    self._descriptions[stem] = new_desc.strip()
                else:
                    self._descriptions.pop(stem, None)
                self._save_descriptions()
                self._update_preview()

        elif action == remove_action:
            if (
                QMessageBox.question(
                    self,
                    "Remove sign",
                    f"Delete '{img_path.name}' from this session?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                == QMessageBox.StandardButton.Yes
            ):
                self._descriptions.pop(img_path.stem, None)
                self._save_descriptions()
                img_path.unlink(missing_ok=True)
                self._refresh_sessions()
                self._load_session_items()

    # ── Preview ───────────────────────────────────────────────────────────────

    def _schedule_preview(self) -> None:
        ipp = _tegn_items_per_page()
        total = (
            max(1, -(-len(self._session_items) // ipp)) if self._session_items else 1
        )
        self._preview_total_pages = total
        self._update_nav_buttons()
        self._update_preview()

    def _update_preview(self) -> None:
        if self._preview_worker is not None:
            try:
                self._preview_worker.ready.disconnect()
            except RuntimeError:
                pass
            old = self._preview_worker
            self._stale_preview_workers.add(old)
            old.finished.connect(lambda w=old: self._stale_preview_workers.discard(w))
        self.tegn_preview_label.setText("Rendering…")
        worker = TegnprotokollPreviewWorker(
            self._session_items, self._descriptions.copy(), self._preview_page
        )
        worker.ready.connect(self._on_preview_ready)
        self._preview_worker = worker
        worker.start()

    def _update_nav_buttons(self) -> None:
        total = self._preview_total_pages
        page = self._preview_page
        self.tegn_prev_btn.setEnabled(page > 0)
        self.tegn_next_btn.setEnabled(page < total - 1)
        self.tegn_page_label.setText(
            f"Page {page + 1} / {total}" if self._session_items else ""
        )

    def _prev_page(self) -> None:
        if self._preview_page > 0:
            self._preview_page -= 1
            self._update_nav_buttons()
            self._update_preview()

    def _next_page(self) -> None:
        if self._preview_page < self._preview_total_pages - 1:
            self._preview_page += 1
            self._update_nav_buttons()
            self._update_preview()

    def _on_preview_ready(self, image: QImage) -> None:
        if image.isNull():
            self.tegn_preview_label.setText("No signs in session")
            return
        pixmap = QPixmap.fromImage(image)  # main thread — safe here
        max_w = max(100, self.tegn_preview_scroll.width() - 20)
        if pixmap.width() > max_w:
            pixmap = pixmap.scaledToWidth(
                max_w, Qt.TransformationMode.SmoothTransformation
            )
        self.tegn_preview_label.setPixmap(pixmap)

    # ── Search ────────────────────────────────────────────────────────────────

    def _refresh_tegnbank(self) -> None:
        import tegnbanken

        tegnbanken.invalidate_cache()
        self.tegn_search_status.setText(
            "Cache cleared — next search will fetch fresh data."
        )

    def _do_search(self) -> None:
        query = self.tegn_search_input.text().strip()
        if not query or self._search_worker is not None:
            return
        self.tegn_search_btn.setEnabled(False)
        self.tegn_result_list.clear()
        self.tegn_add_btn.setEnabled(False)
        self.tegn_search_status.setText("Searching…")

        worker = TegnprotokollSearchWorker(query)
        worker.results.connect(self._on_search_results)
        worker.error.connect(self._on_search_error)
        worker.finished.connect(self._on_search_finished)
        worker.finished.connect(worker.deleteLater)
        self._search_worker = worker
        worker.start()

    def _on_search_finished(self) -> None:
        self._search_worker = None
        self.tegn_search_btn.setEnabled(True)

    def _on_search_results(self, results: list) -> None:
        self.tegn_result_list.clear()
        if not results:
            self.tegn_search_status.setText("No results.")
            return
        self.tegn_search_status.setText(f"{len(results)} result(s)")
        for r in results:
            has_img = bool(r.get("foto") or r.get("la_hend"))
            label = ("\u2713 " if has_img else "\u25a1 ") + r["word"]
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, r)
            if not has_img:
                item.setForeground(
                    self.palette().color(self.palette().ColorRole.PlaceholderText)
                )
            self.tegn_result_list.addItem(item)
        self.tegn_add_btn.setEnabled(True)

    def _on_search_error(self, msg: str) -> None:
        self.tegn_search_status.setText(f"Error: {msg}")

    # ── Add signs ──────────────────────────────────────────────────────────────

    def _result_context_menu(self, pos) -> None:
        item = self.tegn_result_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        add_action = menu.addAction("Add to session")
        action = menu.exec(self.tegn_result_list.mapToGlobal(pos))
        if action == add_action:
            if self.current_session is None:
                QMessageBox.warning(
                    self, "No session", "Please select or create a session first."
                )
                return
            self._start_download(item.data(Qt.ItemDataRole.UserRole))

    def _add_selected(self) -> None:
        if self.current_session is None:
            QMessageBox.warning(
                self,
                "No session",
                "Please select or create a session first.",
            )
            return
        selected = self.tegn_result_list.selectedItems()
        if not selected:
            return
        for item in selected:
            self._start_download(item.data(Qt.ItemDataRole.UserRole))

    def _start_download(self, record: dict) -> None:
        worker = TegnprotokollDownloadWorker(record, self.current_session)
        worker.done.connect(self._on_download_done)
        worker.error.connect(self._on_download_error)
        worker.finished.connect(lambda w=worker: self._download_workers.discard(w))
        worker.finished.connect(worker.deleteLater)
        self._download_workers.add(worker)
        worker.start()

    def _on_download_done(self, _img_path: str) -> None:
        self._refresh_sessions()
        self._load_session_items()

    def _on_download_error(self, msg: str) -> None:
        self.tegn_status.setText(f"Download error: {msg}")

    # ── PDF generation ─────────────────────────────────────────────────────────

    def _generate_pdf(self) -> None:
        if self.current_session is None or self._pdf_worker is not None:
            return
        self.tegn_generate_btn.setEnabled(False)
        self.tegn_open_btn.setVisible(False)
        self.tegn_progress.setVisible(True)
        self.tegn_status.setText("Generating PDF…")

        worker = TegnprotokollPdfWorker(self.current_session)
        worker.done.connect(self._on_generate_done)
        worker.error.connect(self._on_generate_error)
        worker.finished.connect(worker.deleteLater)
        self._pdf_worker = worker
        worker.start()

    def _on_generate_done(self, pdf_path: str) -> None:
        self._pdf_worker = None
        self.tegn_progress.setVisible(False)
        self.tegn_generate_btn.setEnabled(bool(self._session_items))
        self._last_pdf = pdf_path
        self.tegn_open_btn.setVisible(True)
        self.tegn_status.setText(f"Saved: {Path(pdf_path).name}")

    def _on_generate_error(self, msg: str) -> None:
        self._pdf_worker = None
        self.tegn_progress.setVisible(False)
        self.tegn_generate_btn.setEnabled(bool(self._session_items))
        self.tegn_status.setText(f"Error: {msg}")
        QMessageBox.critical(self, "Generation failed", msg)

    def _open_pdf(self, path: Optional[str]) -> None:
        if path:
            open_file(path)


# ── Main window ────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AACdeck")
        self.resize(1100, 720)

        self.current_session: Optional[Path] = None
        self._preview_worker: Optional[PreviewWorker] = None
        self._stale_preview_workers: Set[PreviewWorker] = set()
        self._generate_worker: Optional[GenerateWorker] = None
        self._last_pdf: Optional[str] = None
        self._preview_images: List[Path] = []
        self._preview_page: int = 0
        self._preview_total_pages: int = 1

        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        self._build_ui()
        self._refresh_sessions()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        tabs = QTabWidget()
        outer.addWidget(tabs, stretch=1)

        data_btn = QPushButton("Open data folder")
        data_btn.setToolTip(str(BASE_DIR))
        data_btn.clicked.connect(lambda: open_file(str(BASE_DIR)))
        tabs.setCornerWidget(data_btn, Qt.Corner.TopRightCorner)

        # ── Cards tab (existing layout) ────────────────────────────────────────
        cards_widget = QWidget()
        cards_layout = QVBoxLayout(cards_widget)
        cards_layout.setContentsMargins(0, 4, 0, 0)
        cards_layout.setSpacing(6)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        cards_layout.addWidget(splitter, stretch=1)
        splitter.addWidget(self._build_session_panel())
        splitter.addWidget(self._build_image_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setSizes([280, 440, 320])
        cards_layout.addWidget(self._build_bottom_bar())
        tabs.addTab(cards_widget, "Cards")

        # ── Lotto tab ──────────────────────────────────────────────────────────
        tabs.addTab(LottoTab(), "Lotto")

        # ── Tegnprotokoll tab ──────────────────────────────────────────────────
        tabs.addTab(TegnprotokollTab(), "Sign Protocol")

    def _build_session_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(200)
        panel.setMaximumWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 6, 0)

        lbl = QLabel("Sessions")
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px 0;")
        layout.addWidget(lbl)

        self.session_list = QListWidget()
        self.session_list.currentItemChanged.connect(self._on_session_changed)
        layout.addWidget(self.session_list, stretch=1)

        btn = QPushButton("+ New session")
        btn.clicked.connect(self._new_session)
        layout.addWidget(btn)

        self.picto_search = PictogramSearchPanel(lambda: self.current_session)
        self.picto_search.downloaded.connect(self._on_pictogram_downloaded)
        layout.addWidget(self.picto_search, stretch=2)
        return panel

    def _on_pictogram_downloaded(self, _img_path: str) -> None:
        self._refresh_sessions()
        self._load_images()

    def _build_image_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 4, 0)

        self.session_title = QLabel("Select a session")
        self.session_title.setStyleSheet(
            "font-weight: bold; font-size: 13px; padding: 2px 0;"
        )
        layout.addWidget(self.session_title)

        self.image_list = ImageDropList()
        self.image_list.files_dropped.connect(self._on_images_dropped)
        self.image_list.customContextMenuRequested.connect(self._image_context_menu)
        layout.addWidget(self.image_list, stretch=1)

        add_btn = QPushButton("Add images…")
        add_btn.clicked.connect(self._add_images_dialog)
        layout.addWidget(add_btn)
        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(180)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 0, 0)

        lbl = QLabel("Page preview")
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px 0;")
        layout.addWidget(lbl)

        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self.preview_label = QLabel("No session selected")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setWordWrap(True)
        self.preview_scroll.setWidget(self.preview_label)
        layout.addWidget(self.preview_scroll, stretch=1)

        # Page navigation bar
        nav = QHBoxLayout()
        nav.setContentsMargins(0, 2, 0, 0)
        self.prev_page_btn = QPushButton("← Prev")
        self.prev_page_btn.setEnabled(False)
        self.prev_page_btn.clicked.connect(self._prev_preview_page)
        nav.addWidget(self.prev_page_btn)
        self.page_counter_label = QLabel("")
        self.page_counter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.page_counter_label, stretch=1)
        self.next_page_btn = QPushButton("Next →")
        self.next_page_btn.setEnabled(False)
        self.next_page_btn.clicked.connect(self._next_preview_page)
        nav.addWidget(self.next_page_btn)
        layout.addLayout(nav)
        return panel

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 4, 0, 0)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label, stretch=1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate spinner
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximumWidth(160)
        layout.addWidget(self.progress_bar)

        self.open_btn = QPushButton("Open PDF")
        self.open_btn.setVisible(False)
        self.open_btn.clicked.connect(self._open_pdf)
        layout.addWidget(self.open_btn)

        self.generate_btn = QPushButton("Generate PDF")
        self.generate_btn.setEnabled(False)
        self.generate_btn.setMinimumWidth(120)
        self.generate_btn.clicked.connect(self._generate_pdf)
        layout.addWidget(self.generate_btn)
        return bar

    # ── Session management ─────────────────────────────────────────────────────

    def _refresh_sessions(self) -> None:
        # Remember which session was selected so we can restore it.
        current_name: Optional[str] = None
        if self.session_list.currentItem():
            p: Path = self.session_list.currentItem().data(Qt.ItemDataRole.UserRole)
            current_name = p.name

        self.session_list.clear()
        if not SESSIONS_DIR.exists():
            return

        restore_item: Optional[QListWidgetItem] = None
        for s in sorted(p for p in SESSIONS_DIR.iterdir() if p.is_dir()):
            count = sum(1 for f in s.iterdir() if f.suffix.lower() in IMAGE_EXTS)
            item = QListWidgetItem(f"{s.name}  ({count})")
            item.setData(Qt.ItemDataRole.UserRole, s)
            self.session_list.addItem(item)
            if s.name == current_name:
                restore_item = item

        if restore_item:
            self.session_list.setCurrentItem(restore_item)

    def _on_session_changed(self, current: QListWidgetItem, _previous) -> None:
        if current is None:
            self.current_session = None
            self.session_title.setText("Select a session")
            self.image_list.clear()
            self.generate_btn.setEnabled(False)
            self.preview_label.setText("No session selected")
            self._preview_images = []
            self._preview_page = 0
            self._preview_total_pages = 1
            self._update_nav_buttons()
            return

        self.current_session = current.data(Qt.ItemDataRole.UserRole)
        self.session_title.setText(self.current_session.name)
        self._preview_page = 0
        self._load_images()

    def _new_session(self) -> None:
        name, ok = QInputDialog.getText(
            self, "New session", "Session name (e.g. 2026-04-skole):"
        )
        if not ok or not name.strip():
            return
        name = name.strip().replace(" ", "-")
        new_path = SESSIONS_DIR / name
        if new_path.exists():
            QMessageBox.warning(
                self, "Already exists", f"Session '{name}' already exists."
            )
            return
        new_path.mkdir(parents=True)
        self._refresh_sessions()
        # Auto-select the new session
        for i in range(self.session_list.count()):
            item = self.session_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == new_path:
                self.session_list.setCurrentItem(item)
                break

    # ── Image management ───────────────────────────────────────────────────────

    def _load_images(self) -> None:
        self.image_list.clear()
        if self.current_session is None:
            return

        images = sorted(
            p for p in self.current_session.iterdir() if p.suffix.lower() in IMAGE_EXTS
        )
        for img_path in images:
            item = QListWidgetItem(
                QIcon(self._make_thumb(img_path)),
                stem_to_label(img_path.stem),
            )
            item.setData(Qt.ItemDataRole.UserRole, img_path)
            item.setSizeHint(QSize(110, 120))
            self.image_list.addItem(item)

        self.generate_btn.setEnabled(bool(images))
        self._schedule_preview(images)

    def _make_thumb(self, img_path: Path) -> QPixmap:
        try:
            img = to_rgb(Image.open(img_path))
            img.thumbnail((96, 96), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return QPixmap.fromImage(QImage.fromData(buf.read()))
        except Exception:
            return QPixmap()

    def _on_images_dropped(self, paths: List[Path]) -> None:
        if self.current_session is None:
            QMessageBox.warning(
                self, "No session", "Please select or create a session first."
            )
            return
        for src in paths:
            dst = self.current_session / src.name
            if dst.exists():
                counter = 2
                while True:
                    dst = self.current_session / f"{src.stem}__{counter}{src.suffix}"
                    if not dst.exists():
                        break
                    counter += 1
            shutil.copy2(src, dst)
        self._refresh_sessions()
        self._load_images()

    def _add_images_dialog(self) -> None:
        if self.current_session is None:
            QMessageBox.warning(
                self, "No session", "Please select or create a session first."
            )
            return
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Add images",
            str(Path.home()),
            "Images (*.jpg *.jpeg *.png *.webp *.avif)",
        )
        if files:
            self._on_images_dropped([Path(f) for f in files])

    def _image_context_menu(self, pos) -> None:
        item = self.image_list.itemAt(pos)
        if item is None:
            return
        img_path: Path = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        rename_action = menu.addAction("Rename…")
        duplicate_action = menu.addAction("Duplicate")
        remove_action = menu.addAction("Remove from session")
        action = menu.exec(self.image_list.mapToGlobal(pos))
        if action == rename_action:
            current_label = stem_to_label(img_path.stem)
            new_label, ok = QInputDialog.getText(
                self, "Rename card", "Card label:", text=current_label
            )
            if ok and new_label.strip() and new_label.strip() != current_label:
                new_stem = new_label.strip().replace(" ", "_")
                new_path = img_path.with_stem(new_stem)
                if new_path.exists():
                    QMessageBox.warning(
                        self, "Name taken", f"'{new_path.name}' already exists."
                    )
                else:
                    img_path.rename(new_path)
                    carry_arasaac_record(img_path, new_path)
                    self._load_images()
        elif action == duplicate_action:
            counter = 2
            while True:
                dst = img_path.with_stem(f"{img_path.stem}__{counter}")
                if not dst.exists():
                    break
                counter += 1
            shutil.copy2(img_path, dst)
            carry_arasaac_record(img_path, dst)
            self._refresh_sessions()
            self._load_images()
        elif action == remove_action:
            if (
                QMessageBox.question(
                    self,
                    "Remove image",
                    f"Delete '{img_path.name}' from this session?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                == QMessageBox.StandardButton.Yes
            ):
                img_path.unlink(missing_ok=True)
                self._refresh_sessions()
                self._load_images()

    # ── PDF generation ─────────────────────────────────────────────────────────

    def _generate_pdf(self) -> None:
        if self.current_session is None or self._generate_worker is not None:
            return
        self.generate_btn.setEnabled(False)
        self.open_btn.setVisible(False)
        self.progress_bar.setVisible(True)
        self.status_label.setText("Generating PDF…")

        worker = GenerateWorker(self.current_session)
        worker.done.connect(self._on_generate_done)
        worker.error.connect(self._on_generate_error)
        worker.finished.connect(worker.deleteLater)
        self._generate_worker = worker
        worker.start()

    def _on_generate_done(self, pdf_path: str) -> None:
        self._generate_worker = None
        self.progress_bar.setVisible(False)
        self.generate_btn.setEnabled(True)
        self.status_label.setText(f"Saved: {pdf_path}")
        self._last_pdf = pdf_path
        self.open_btn.setVisible(True)

    def _on_generate_error(self, message: str) -> None:
        self._generate_worker = None
        self.progress_bar.setVisible(False)
        self.generate_btn.setEnabled(True)
        self.status_label.setText(f"Error: {message}")
        QMessageBox.critical(self, "Generation failed", message)

    def _open_pdf(self) -> None:
        if self._last_pdf:
            open_file(self._last_pdf)

    # ── Preview ────────────────────────────────────────────────────────────────

    def _schedule_preview(self, images: List[Path]) -> None:
        self._preview_images = images
        cpp = _preview_cards_per_page()
        self._preview_total_pages = max(1, -(-len(images) // cpp) if images else 1)
        self._update_nav_buttons()
        self._update_preview()

    def _update_preview(self) -> None:
        if self._preview_worker is not None:
            try:
                self._preview_worker.ready.disconnect()
            except RuntimeError:
                pass
            old = self._preview_worker
            self._stale_preview_workers.add(old)
            old.finished.connect(lambda w=old: self._stale_preview_workers.discard(w))
        self.preview_label.setText("Rendering…")
        worker = PreviewWorker(self._preview_images, self._preview_page)
        worker.ready.connect(self._on_preview_ready)
        self._preview_worker = worker
        worker.start()

    def _update_nav_buttons(self) -> None:
        total = self._preview_total_pages
        page = self._preview_page
        self.prev_page_btn.setEnabled(page > 0)
        self.next_page_btn.setEnabled(page < total - 1)
        self.page_counter_label.setText(
            f"Page {page + 1} / {total}" if self._preview_images else ""
        )

    def _prev_preview_page(self) -> None:
        if self._preview_page > 0:
            self._preview_page -= 1
            self._update_nav_buttons()
            self._update_preview()

    def _next_preview_page(self) -> None:
        if self._preview_page < self._preview_total_pages - 1:
            self._preview_page += 1
            self._update_nav_buttons()
            self._update_preview()

    def _on_preview_ready(self, image: QImage) -> None:
        if image.isNull():
            self.preview_label.setText("No cards in session")
            return
        pixmap = QPixmap.fromImage(image)  # main thread — safe here
        max_w = max(100, self.preview_scroll.width() - 20)
        if pixmap.width() > max_w:
            pixmap = pixmap.scaledToWidth(
                max_w, Qt.TransformationMode.SmoothTransformation
            )
        self.preview_label.setPixmap(pixmap)


# ── Entry point ────────────────────────────────────────────────────────────────
def _make_app_icon() -> QIcon:
    """Generate a simple app icon — 2×2 grid of coloured picture cards."""
    icon = QIcon()
    # Card colours: warm yellow, sky blue, soft green, coral
    colours = [(255, 190, 70), (90, 175, 255), (120, 205, 110), (255, 110, 110)]
    for size in (16, 24, 32, 48, 64, 128, 256):
        img = Image.new("RGBA", (size, size), (245, 245, 245, 255))
        draw = ImageDraw.Draw(img, "RGBA")

        margin = max(1, size // 10)
        gap = max(1, size // 14)
        card_sz = (size - 2 * margin - gap) // 2
        border_w = max(1, size // 48)

        for idx, fill in enumerate(colours):
            row, col = divmod(idx, 2)
            x = margin + col * (card_sz + gap)
            y = margin + row * (card_sz + gap)
            draw.rectangle(
                [x, y, x + card_sz - 1, y + card_sz - 1],
                fill=fill,
                outline=(60, 60, 60),
                width=border_w,
            )

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        pix = QPixmap()
        pix.loadFromData(buf.read())
        icon.addPixmap(pix)
    return icon


def _migrate_legacy_data() -> None:
    """Copy sessions/output from beside the executable into BASE_DIR, once.

    Copies rather than moves, so a failed or interrupted migration never loses
    anything. Skipped entirely once BASE_DIR exists.
    """
    if _LEGACY_DIR is None or BASE_DIR.exists():
        return
    for name in ("sessions", "lotto-sessions", "tegnprotokoll-sessions", "output"):
        src = _LEGACY_DIR / name
        if src.is_dir() and any(src.iterdir()):
            try:
                shutil.copytree(src, BASE_DIR / name, dirs_exist_ok=True)
            except OSError as exc:
                print(f"Warning: could not copy {src} to {BASE_DIR}: {exc}")


def main() -> None:
    _migrate_legacy_data()
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setApplicationName("AACdeck")
    icon = _make_app_icon()
    app.setWindowIcon(icon)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
