"""Cards tab: picture-card sessions and the card PDF."""

from __future__ import annotations

import io
import shutil
from pathlib import Path
from typing import List, Optional, Set

import make_cards
from PIL import Image
from pdf_utils import IMAGE_EXTS, carry_arasaac_record, open_file, stem_to_label, to_rgb
from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
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
from gui_common import OUTPUT_DIR, SESSIONS_DIR
from gui_previews import _preview_cards_per_page, render_page_preview
from gui_pictograms import PictogramSearchPanel


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



# ── Main window ────────────────────────────────────────────────────────────────
class CardsTab(QWidget):
    """Tab for building picture-card sessions and generating the card PDF."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, stretch=1)
        splitter.addWidget(self._build_session_panel())
        splitter.addWidget(self._build_image_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setSizes([280, 440, 320])
        layout.addWidget(self._build_bottom_bar())

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
