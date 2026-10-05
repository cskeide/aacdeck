"""Shared GUI plumbing: data-folder paths, legacy migration, QImage conversion."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Optional

from PIL import Image
from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QImage


def pillow_to_qimage(img: Image.Image) -> "QImage":
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


def migrate_legacy_data() -> None:
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

