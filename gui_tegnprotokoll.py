"""Sign Protocol tab: Tegnbanken sign sessions and the protocol PDF."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import List, Optional, Set

from PIL import Image, ImageDraw
from pdf_utils import IMAGE_EXTS, open_file, safe_stem, stem_to_label, to_rgb
from i18n import t
from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
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
from gui_common import OUTPUT_DIR, TEGNPROTOKOLL_SESSIONS_DIR
from gui_previews import _tegn_items_per_page, render_tegnprotokoll_preview


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

        lbl = QLabel(t("Sessions"))
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px 0;")
        layout.addWidget(lbl)

        self.tegn_session_list = QListWidget()
        self.tegn_session_list.currentItemChanged.connect(self._on_session_changed)
        layout.addWidget(self.tegn_session_list, stretch=1)

        new_btn = QPushButton(t("+ New session"))
        new_btn.clicked.connect(self._new_session)
        layout.addWidget(new_btn)

        sep_row = QHBoxLayout()
        sep = QLabel(t("Search Signs"))
        sep.setStyleSheet("font-weight: bold; font-size: 13px; padding: 8px 0 2px;")
        sep_row.addWidget(sep, stretch=1)
        refresh_btn = QPushButton(t("Refresh database"))
        refresh_btn.setToolTip(t("Clear cache and reload sign data"))
        refresh_btn.clicked.connect(self._refresh_tegnbank)
        sep_row.addWidget(refresh_btn)
        layout.addLayout(sep_row)

        search_row = QHBoxLayout()
        self.tegn_search_input = QLineEdit()
        self.tegn_search_input.setPlaceholderText(t("Search for signs…"))
        self.tegn_search_input.returnPressed.connect(self._do_search)
        search_row.addWidget(self.tegn_search_input, stretch=1)
        self.tegn_search_btn = QPushButton(t("Search"))
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

        self.tegn_add_btn = QPushButton(t("Add selected to session"))
        self.tegn_add_btn.setEnabled(False)
        self.tegn_add_btn.clicked.connect(self._add_selected)
        layout.addWidget(self.tegn_add_btn)
        return panel

    def _build_signs_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 4, 0)

        self.tegn_session_title = QLabel(t("Select a session"))
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

        lbl = QLabel(t("Page preview"))
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px 0;")
        layout.addWidget(lbl)

        self.tegn_preview_scroll = QScrollArea()
        self.tegn_preview_scroll.setWidgetResizable(True)
        self.tegn_preview_scroll.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self.tegn_preview_label = QLabel(t("No session selected"))
        self.tegn_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tegn_preview_label.setWordWrap(True)
        self.tegn_preview_scroll.setWidget(self.tegn_preview_label)
        layout.addWidget(self.tegn_preview_scroll, stretch=1)

        nav = QHBoxLayout()
        nav.setContentsMargins(0, 2, 0, 0)
        self.tegn_prev_btn = QPushButton(t("← Prev"))
        self.tegn_prev_btn.setEnabled(False)
        self.tegn_prev_btn.clicked.connect(self._prev_page)
        nav.addWidget(self.tegn_prev_btn)
        self.tegn_page_label = QLabel("")
        self.tegn_page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.tegn_page_label, stretch=1)
        self.tegn_next_btn = QPushButton(t("Next →"))
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

        self.tegn_generate_btn = QPushButton(t("Generate Sign Protocol PDF"))
        self.tegn_generate_btn.setEnabled(False)
        self.tegn_generate_btn.setMinimumWidth(160)
        self.tegn_generate_btn.clicked.connect(self._generate_pdf)
        layout.addWidget(self.tegn_generate_btn)

        self.tegn_open_btn = QPushButton(t("Open PDF"))
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
            self.tegn_session_title.setText(t("Select a session"))
            self.tegn_signs_list.clear()
            self.tegn_generate_btn.setEnabled(False)
            self.tegn_preview_label.setText(t("No session selected"))
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
            t("New sign protocol session"),
            t("Session name (e.g. 2026-04-signs-home):"),
        )
        if not ok or not name.strip():
            return
        name = name.strip().replace(" ", "-")
        new_path = TEGNPROTOKOLL_SESSIONS_DIR / name
        if new_path.exists():
            QMessageBox.warning(
                self,
                t("Already exists"),
                t("Session '{name}' already exists.", name=name),
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
            self.tegn_status.setText(t("Warning: could not save descriptions — {error}", error=exc))

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
        rename_action = menu.addAction(t("Rename…"))
        desc_action = menu.addAction(t("Set description…"))
        remove_action = menu.addAction(t("Remove from session"))
        action = menu.exec(self.tegn_signs_list.mapToGlobal(pos))

        if action == rename_action:
            current_label = stem_to_label(img_path.stem)
            new_label, ok = QInputDialog.getText(
                self, t("Rename sign"), t("Sign label:"), text=current_label
            )
            if ok and new_label.strip() and new_label.strip() != current_label:
                old_stem = img_path.stem
                new_stem = new_label.strip().replace(" ", "_")
                new_path = img_path.with_stem(new_stem)
                if new_path.exists():
                    QMessageBox.warning(
                        self,
                        t("Name taken"),
                        t("'{name}' already exists.", name=new_path.name),
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
                t("Set description"),
                t("Describe how the child uses the sign:"),
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
                    t("Remove sign"),
                    t("Delete '{name}' from this session?", name=img_path.name),
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
        self.tegn_preview_label.setText(t("Rendering…"))
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
            t("Page {page} / {total}", page=page + 1, total=total) if self._session_items else ""
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
            self.tegn_preview_label.setText(t("No signs in session"))
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
            t("Cache cleared — next search will fetch fresh data.")
        )

    def _do_search(self) -> None:
        query = self.tegn_search_input.text().strip()
        if not query or self._search_worker is not None:
            return
        self.tegn_search_btn.setEnabled(False)
        self.tegn_result_list.clear()
        self.tegn_add_btn.setEnabled(False)
        self.tegn_search_status.setText(t("Searching…"))

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
            self.tegn_search_status.setText(t("No results."))
            return
        self.tegn_search_status.setText(t("{n} result(s)", n=len(results)))
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
        self.tegn_search_status.setText(t("Error: {msg}", msg=msg))

    # ── Add signs ──────────────────────────────────────────────────────────────

    def _result_context_menu(self, pos) -> None:
        item = self.tegn_result_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        add_action = menu.addAction(t("Add to session"))
        action = menu.exec(self.tegn_result_list.mapToGlobal(pos))
        if action == add_action:
            if self.current_session is None:
                QMessageBox.warning(
                    self, t("No session"), t("Please select or create a session first.")
                )
                return
            self._start_download(item.data(Qt.ItemDataRole.UserRole))

    def _add_selected(self) -> None:
        if self.current_session is None:
            QMessageBox.warning(
                self,
                t("No session"),
                t("Please select or create a session first."),
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
        self.tegn_status.setText(t("Download error: {msg}", msg=msg))

    # ── PDF generation ─────────────────────────────────────────────────────────

    def _generate_pdf(self) -> None:
        if self.current_session is None or self._pdf_worker is not None:
            return
        self.tegn_generate_btn.setEnabled(False)
        self.tegn_open_btn.setVisible(False)
        self.tegn_progress.setVisible(True)
        self.tegn_status.setText(t("Generating PDF…"))

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
        self.tegn_status.setText(t("Saved: {path}", path=Path(pdf_path).name))

    def _on_generate_error(self, msg: str) -> None:
        self._pdf_worker = None
        self.tegn_progress.setVisible(False)
        self.tegn_generate_btn.setEnabled(bool(self._session_items))
        self.tegn_status.setText(t("Error: {msg}", msg=msg))
        QMessageBox.critical(self, t("Generation failed"), msg)

    def _open_pdf(self, path: Optional[str]) -> None:
        if path:
            open_file(path)

