#!/usr/bin/env python3
"""AACdeck — desktop GUI

Dependencies: PySide6, Pillow, reportlab  (see requirements.txt)

Each tab lives in its own gui_*.py module; this file only assembles the window.
"""

from __future__ import annotations

import io
import sys

from PIL import Image, ImageDraw
from pdf_utils import open_file
from i18n import LANGUAGES, get_language, language_from_locale, set_language, t
from PySide6.QtCore import QLocale, QSettings, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from gui_common import BASE_DIR, migrate_legacy_data
from gui_cards import CardsTab
from gui_lotto import LottoTab
from gui_tegnprotokoll import TegnprotokollTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AACdeck")
        self.resize(1100, 720)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        tabs = QTabWidget()
        outer.addWidget(tabs, stretch=1)

        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 0, 0, 0)
        self.lang_combo = QComboBox()
        for code, name in LANGUAGES.items():
            self.lang_combo.addItem(name, code)
        self.lang_combo.setCurrentIndex(self.lang_combo.findData(get_language()))
        self.lang_combo.currentIndexChanged.connect(self._on_language_chosen)
        corner_layout.addWidget(self.lang_combo)
        data_btn = QPushButton(t("Open data folder"))
        data_btn.setToolTip(str(BASE_DIR))
        data_btn.clicked.connect(lambda: open_file(str(BASE_DIR)))
        corner_layout.addWidget(data_btn)
        tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        tabs.addTab(CardsTab(), t("Cards"))
        tabs.addTab(LottoTab(), t("Lotto"))
        tabs.addTab(TegnprotokollTab(), t("Sign Protocol"))

    def _on_language_chosen(self, _index: int) -> None:
        # Text is built once at startup, so a new language applies on restart.
        code = self.lang_combo.currentData()
        _settings().setValue("language", code)
        if code != get_language():
            QMessageBox.information(
                self,
                t("Restart needed", lang=code),
                t("Restart AACdeck to switch language.", lang=code),
            )


def _settings() -> QSettings:
    return QSettings("AACdeck", "AACdeck")


def _startup_language() -> str:
    """Saved choice if valid, else Norwegian on a Norwegian system, else English."""
    saved = _settings().value("language")
    if saved in LANGUAGES:
        return saved
    return language_from_locale(QLocale.system().name())


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


def main() -> None:
    migrate_legacy_data()
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setApplicationName("AACdeck")
    icon = _make_app_icon()
    app.setWindowIcon(icon)
    set_language(_startup_language())
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
