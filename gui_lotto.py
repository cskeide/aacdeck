"""Lotto tab: ARASAAC lotto sessions, board and cut-out PDFs."""

from __future__ import annotations

import io
from pathlib import Path
from typing import List, Optional, Set

import make_lotto
from PIL import Image
from pdf_utils import IMAGE_EXTS, carry_arasaac_record, open_file, stem_to_label, to_rgb
from i18n import t
from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from gui_common import LOTTO_SESSIONS_DIR, OUTPUT_DIR
from gui_previews import _lotto_cards_per_page, render_lotto_preview
from gui_pictograms import PictogramSearchPanel


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

        lbl = QLabel(t("Sessions"))
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px 0;")
        layout.addWidget(lbl)

        self.lotto_session_list = QListWidget()
        self.lotto_session_list.currentItemChanged.connect(self._on_session_changed)
        layout.addWidget(self.lotto_session_list, stretch=1)

        new_btn = QPushButton(t("+ New session"))
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

        self.lotto_session_title = QLabel(t("Select a session"))
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

        lbl = QLabel(t("Page preview"))
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px 0;")
        layout.addWidget(lbl)

        self.lotto_preview_scroll = QScrollArea()
        self.lotto_preview_scroll.setWidgetResizable(True)
        self.lotto_preview_scroll.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self.lotto_preview_label = QLabel(t("No session selected"))
        self.lotto_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lotto_preview_label.setWordWrap(True)
        self.lotto_preview_scroll.setWidget(self.lotto_preview_label)
        layout.addWidget(self.lotto_preview_scroll, stretch=1)

        nav = QHBoxLayout()
        nav.setContentsMargins(0, 2, 0, 0)
        self.lotto_prev_btn = QPushButton(t("← Prev"))
        self.lotto_prev_btn.setEnabled(False)
        self.lotto_prev_btn.clicked.connect(self._prev_preview_page)
        nav.addWidget(self.lotto_prev_btn)
        self.lotto_page_label = QLabel("")
        self.lotto_page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.lotto_page_label, stretch=1)
        self.lotto_next_btn = QPushButton(t("Next →"))
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

        self.generate_pdfs_btn = QPushButton(t("Generate PDFs"))
        self.generate_pdfs_btn.setEnabled(False)
        self.generate_pdfs_btn.setMinimumWidth(140)
        self.generate_pdfs_btn.clicked.connect(self._generate_pdfs)
        layout.addWidget(self.generate_pdfs_btn)

        open_row = QHBoxLayout()
        self.open_board_btn = QPushButton(t("Open board PDF"))
        self.open_board_btn.setVisible(False)
        self.open_board_btn.clicked.connect(
            lambda: self._open_pdf(self._last_board_pdf)
        )
        open_row.addWidget(self.open_board_btn)
        self.open_cutout_btn = QPushButton(t("Open cut-out PDF"))
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
            self.lotto_session_title.setText(t("Select a session"))
            self.lotto_image_list.clear()
            self.generate_pdfs_btn.setEnabled(False)
            self.lotto_preview_label.setText(t("No session selected"))
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
            self, t("New lotto session"), t("Session name (e.g. 2026-04-lotto-animals):")
        )
        if not ok or not name.strip():
            return
        name = name.strip().replace(" ", "-")
        new_path = LOTTO_SESSIONS_DIR / name
        if new_path.exists():
            QMessageBox.warning(
                self, t("Already exists"), t("Session '{name}' already exists.", name=name)
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
        rename_action = menu.addAction(t("Rename…"))
        remove_action = menu.addAction(t("Remove from session"))
        action = menu.exec(self.lotto_image_list.mapToGlobal(pos))
        if action == rename_action:
            current_label = stem_to_label(img_path.stem)
            new_label, ok = QInputDialog.getText(
                self, t("Rename card"), t("Card label:"), text=current_label
            )
            if ok and new_label.strip() and new_label.strip() != current_label:
                new_stem = new_label.strip().replace(" ", "_")
                new_path = img_path.with_stem(new_stem)
                if new_path.exists():
                    QMessageBox.warning(
                        self, t("Name taken"), t("'{name}' already exists.", name=new_path.name)
                    )
                else:
                    img_path.rename(new_path)
                    carry_arasaac_record(img_path, new_path)
                    self._load_session_images()
        elif action == remove_action:
            if (
                QMessageBox.question(
                    self,
                    t("Remove card"),
                    t("Delete '{name}' from this session?", name=img_path.name),
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
        self.lotto_preview_label.setText(t("Rendering…"))
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
            t("Page {page} / {total}", page=page + 1, total=total) if self._preview_images else ""
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
            self.lotto_preview_label.setText(t("No cards in session"))
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
        self.lotto_status.setText(t("Generating PDFs…"))

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
            t("Saved: {board} and {cutout}", board=Path(board_path).name, cutout=Path(cutout_path).name)
        )

    def _on_generate_error(self, msg: str) -> None:
        self._board_worker = None
        self.lotto_progress.setVisible(False)
        self.generate_pdfs_btn.setEnabled(bool(self._preview_images))
        self.lotto_status.setText(t("Error: {msg}", msg=msg))
        QMessageBox.critical(self, t("Generation failed"), msg)

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

