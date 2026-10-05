"""ARASAAC pictogram search and download, shared by the Cards and Lotto tabs."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Set

from pdf_utils import record_arasaac_image, safe_stem
from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


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

