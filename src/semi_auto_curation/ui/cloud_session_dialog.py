from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from semi_auto_curation.services.cloud_api import (
    DEFAULT_CLOUD_ACCESS_TOKEN,
    DEFAULT_CLOUD_BASE_URL,
    CloudServiceConfig,
    CloudSessionSelection,
    CloudSyncResult,
    extract_session_tag,
    get_session_detail,
    list_sessions,
    load_local_session_tags,
    save_local_session_tags,
    sync_session_category,
)


class CloudSessionsWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, config: CloudServiceConfig, limit: int) -> None:
        super().__init__()
        self.config = config
        self.limit = limit

    def run(self) -> None:
        try:
            payload = list_sessions(self.config, limit=self.limit)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(payload)


class CloudSessionDetailWorker(QObject):
    finished = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, config: CloudServiceConfig, session_id: str) -> None:
        super().__init__()
        self.config = config
        self.session_id = session_id

    def run(self) -> None:
        try:
            payload = get_session_detail(self.config, self.session_id)
        except Exception as exc:
            self.failed.emit(self.session_id, str(exc))
            return
        self.finished.emit(self.session_id, payload)


class CloudSyncWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, selection: CloudSessionSelection, output_dir) -> None:
        super().__init__()
        self.selection = selection
        self.output_dir = output_dir

    def run(self) -> None:
        try:
            result = sync_session_category(self.selection, self.output_dir, progress_callback=self._emit_progress)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)

    def _emit_progress(self, index: int, total: int, message: str) -> None:
        progress = 10 + int(index / max(total, 1) * 90)
        self.progress.emit(progress, message)


@dataclass(slots=True)
class SessionTreeColumns:
    tag: int = 0
    session_id: int = 1
    created_at: int = 2
    status: int = 3
    iv: int = 4
    b1500_tran: int = 5
    b1500_output: int = 6
    rows: int = 7
    cols: int = 8
    images: int = 9
    files: int = 10
    size: int = 11


class CloudSessionsDialog(QDialog):
    def __init__(self, required_category: str, parent=None) -> None:
        super().__init__(parent)
        self.required_category = required_category
        self.columns = SessionTreeColumns()
        self.local_tag_store = Path.cwd() / ".cloud_session_tags.json"
        self.local_tags = load_local_session_tags(self.local_tag_store)
        self._selected_session: CloudSessionSelection | None = None
        self._detail_cache: dict[str, dict] = {}
        self._session_items: dict[str, QTreeWidgetItem] = {}
        self._detail_prefetch_queue: list[str] = []
        self._detail_request_session_id: str | None = None
        self._detail_loading_session_id: str | None = None
        self._sessions_thread: QThread | None = None
        self._sessions_worker: CloudSessionsWorker | None = None
        self._detail_thread: QThread | None = None
        self._detail_worker: CloudSessionDetailWorker | None = None

        self.setWindowTitle("Cloud Autotest Sessions")
        self.resize(1240, 760)

        self.base_edit = QLineEdit(DEFAULT_CLOUD_BASE_URL)
        self.token_edit = QLineEdit(DEFAULT_CLOUD_ACCESS_TOKEN)
        self.token_edit.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.status_label = QLabel("Ready to load cloud sessions.")
        self.summary_label = QLabel("")
        self.sessions_tree = QTreeWidget()
        self.sessions_tree.setRootIsDecorated(False)
        self.sessions_tree.setAlternatingRowColors(True)
        self.sessions_tree.setUniformRowHeights(True)
        self.sessions_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sessions_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.sessions_tree.setHeaderLabels(["Tag", "Session", "Created", "Status", "IV", "Tran", "Output", "Rows", "Cols", "Images", "Files", "Size"])
        self.detail_view = QTextEdit()
        self.detail_view.setReadOnly(True)
        self.tag_status_label = QLabel("Tag: none")
        self.tag_button_group = QButtonGroup(self)
        self.tag_button_group.setExclusive(True)
        self.tag_buttons: dict[str, QPushButton] = {}
        for tag in ["!", "#", "?"]:
            button = QPushButton(tag)
            button.setCheckable(True)
            button.setMinimumWidth(42)
            self.tag_button_group.addButton(button)
            self.tag_buttons[tag] = button
        self.clear_tag_button = QPushButton("Clear Tag")
        self.save_tag_button = QPushButton("Save Tag")
        self.save_tag_button.setEnabled(False)
        self.refresh_button = QPushButton("Refresh Sessions")
        self.use_button = QPushButton("Use Selected Session")
        self.use_button.setEnabled(False)
        self.close_button = QPushButton("Close")
        self._build_ui()
        self._wire_events()
        self.refresh_sessions()

    def selected_session(self) -> CloudSessionSelection | None:
        return self._selected_session

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        service_box = QGroupBox("Cloud Service")
        service_layout = QFormLayout(service_box)
        service_layout.addRow("Base URL", self.base_edit)
        service_layout.addRow("Access Token", self.token_edit)
        service_layout.addRow(self.refresh_button)
        root.addWidget(service_box)

        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.sessions_tree)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        detail_box = QGroupBox("Session Detail")
        detail_layout = QVBoxLayout(detail_box)
        detail_layout.addWidget(self.detail_view)
        right_layout.addWidget(detail_box)
        tag_box = QGroupBox("Session Tag")
        tag_layout = QVBoxLayout(tag_box)
        tag_layout.addWidget(self.tag_status_label)
        tag_row = QHBoxLayout()
        for tag in ["!", "#", "?"]:
            tag_row.addWidget(self.tag_buttons[tag])
        tag_row.addWidget(self.clear_tag_button)
        tag_row.addStretch(1)
        tag_layout.addLayout(tag_row)
        tag_layout.addWidget(self.save_tag_button, 0, Qt.AlignLeft)
        right_layout.addWidget(tag_box)
        splitter.addWidget(right)
        splitter.setSizes([760, 440])
        root.addWidget(splitter, 1)

        button_row = QHBoxLayout()
        button_row.addWidget(self.status_label, 1)
        button_row.addWidget(self.use_button)
        button_row.addWidget(self.close_button)
        root.addLayout(button_row)

        header = self.sessions_tree.header()
        header.setStretchLastSection(False)
        for section in range(self.sessions_tree.columnCount()):
            header.setSectionResizeMode(section, QHeaderView.Interactive)
        header.resizeSection(self.columns.tag, 34)
        header.resizeSection(self.columns.session_id, 168)
        header.resizeSection(self.columns.created_at, 132)
        header.resizeSection(self.columns.status, 76)
        header.resizeSection(self.columns.iv, 52)
        header.resizeSection(self.columns.b1500_tran, 56)
        header.resizeSection(self.columns.b1500_output, 62)
        header.resizeSection(self.columns.rows, 48)
        header.resizeSection(self.columns.cols, 48)
        header.resizeSection(self.columns.images, 58)
        header.resizeSection(self.columns.files, 54)
        header.resizeSection(self.columns.size, 74)

    def _wire_events(self) -> None:
        self.refresh_button.clicked.connect(self.refresh_sessions)
        self.close_button.clicked.connect(self.reject)
        self.use_button.clicked.connect(self._accept_selected_session)
        self.sessions_tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.sessions_tree.itemDoubleClicked.connect(lambda *_: self._accept_selected_session())
        self.clear_tag_button.clicked.connect(self._clear_tag_selection)
        self.save_tag_button.clicked.connect(self._save_selected_tag)
        for button in self.tag_button_group.buttons():
            button.toggled.connect(self._sync_tag_controls)

    def refresh_sessions(self) -> None:
        self.status_label.setText("Loading cloud sessions...")
        self.refresh_button.setEnabled(False)
        self._detail_cache = {}
        self._session_items = {}
        self._detail_prefetch_queue = []
        self._detail_request_session_id = None
        self._detail_loading_session_id = None
        config = self._config()
        self._sessions_thread = QThread(self)
        self._sessions_worker = CloudSessionsWorker(config, limit=100)
        self._sessions_worker.moveToThread(self._sessions_thread)
        self._sessions_thread.started.connect(self._sessions_worker.run)
        self._sessions_worker.finished.connect(self._sessions_loaded)
        self._sessions_worker.failed.connect(self._sessions_failed)
        self._sessions_worker.finished.connect(self._sessions_thread.quit)
        self._sessions_worker.failed.connect(self._sessions_thread.quit)
        self._sessions_thread.finished.connect(self._sessions_worker.deleteLater)
        self._sessions_thread.finished.connect(self._sessions_thread.deleteLater)
        self._sessions_thread.start()

    def _sessions_loaded(self, payload: object) -> None:
        self.refresh_button.setEnabled(True)
        self.sessions_tree.clear()
        data = payload if isinstance(payload, dict) else {}
        sessions = data.get("sessions", [])
        totals = data.get("totals", {})
        self.summary_label.setText(
            f"Showing {len(sessions)} package(s). "
            f"Files: {totals.get('files', 0)} | IV: {totals.get('csv', 0)} CSV/JSON mix | Images: {totals.get('images', 0)}"
        )
        for session in sessions:
            categories = session.get("categories", {})
            session_id = str(session.get("id", ""))
            tag = self._tag_for_session(session_id, session, None)
            item = QTreeWidgetItem(
                [
                    tag or "--",
                    session_id,
                    str(session.get("created_at", "")),
                    str(session.get("status", "")),
                    str(categories.get("iv", {}).get("file_count", 0)),
                    "--",
                    "--",
                    "--",
                    "--",
                    str(categories.get("images", {}).get("file_count", 0)),
                    str(session.get("file_count", 0)),
                    _format_size(session.get("size_bytes", 0)),
                ]
            )
            item.setData(self.columns.session_id, Qt.UserRole, session)
            self._style_session_item(item)
            self.sessions_tree.addTopLevelItem(item)
            self._session_items[session_id] = item
            self._detail_prefetch_queue.append(session_id)
        self.status_label.setText("Cloud sessions loaded.")
        if self.sessions_tree.topLevelItemCount():
            self.sessions_tree.setCurrentItem(self.sessions_tree.topLevelItem(0))
        self._start_next_detail_load()

    def _sessions_failed(self, message: str) -> None:
        self.refresh_button.setEnabled(True)
        self.status_label.setText("Cloud sessions load failed.")
        QMessageBox.critical(self, "Cloud Sessions Failed", message)

    def _on_selection_changed(self) -> None:
        item = self.sessions_tree.currentItem()
        if item is None:
            self.use_button.setEnabled(False)
            self.detail_view.clear()
            self._apply_tag_to_controls("")
            return
        summary = item.data(self.columns.session_id, Qt.UserRole)
        if not isinstance(summary, dict):
            self.use_button.setEnabled(False)
            return
        has_required = summary.get("categories", {}).get(self.required_category, {}).get("file_count", 0) > 0
        self.use_button.setEnabled(has_required)
        session_id = str(summary.get("id", ""))
        self.detail_view.setHtml(_build_summary_html(summary, None, self.required_category, self._tag_for_session(session_id, summary, None)))
        self._apply_tag_to_controls(self._tag_for_session(session_id, summary, None))
        if session_id in self._detail_cache:
            detail = self._detail_cache[session_id]
            self.detail_view.setHtml(_build_summary_html(summary, detail, self.required_category, self._tag_for_session(session_id, summary, detail)))
            self._apply_detail_to_item(item, summary, detail)
            return
        self._detail_request_session_id = session_id
        self._queue_detail_load(session_id, prioritize=True)

    def _detail_loaded(self, session_id: str, payload: object) -> None:
        self._detail_loading_session_id = None
        if not isinstance(payload, dict):
            self._start_next_detail_load()
            return
        self._detail_cache[session_id] = payload
        item = self._session_items.get(session_id)
        item_summary = item.data(self.columns.session_id, Qt.UserRole) if item is not None else None
        if item is not None and isinstance(item_summary, dict):
            self._apply_detail_to_item(item, item_summary, payload)
        current_item = self.sessions_tree.currentItem()
        if current_item is None:
            self._start_next_detail_load()
            return
        summary = current_item.data(self.columns.session_id, Qt.UserRole)
        if not isinstance(summary, dict):
            self._start_next_detail_load()
            return
        if str(summary.get("id", "")) != session_id:
            self._start_next_detail_load()
            return
        self.detail_view.setHtml(_build_summary_html(summary, payload, self.required_category, self._tag_for_session(session_id, summary, payload)))
        self._apply_tag_to_controls(self._tag_for_session(session_id, summary, payload))
        self.status_label.setText(f"Detail loaded for {session_id}.")
        self._start_next_detail_load()

    def _detail_failed(self, session_id: str, message: str) -> None:
        self._detail_loading_session_id = None
        if self._detail_request_session_id == session_id:
            self.status_label.setText(f"Detail load failed for {session_id}.")
        current_item = self.sessions_tree.currentItem()
        if current_item is None:
            self._start_next_detail_load()
            return
        summary = current_item.data(self.columns.session_id, Qt.UserRole)
        if not isinstance(summary, dict):
            self._start_next_detail_load()
            return
        if str(summary.get("id", "")) == session_id:
            self.detail_view.setHtml(
                _build_summary_html(summary, None, self.required_category, self._tag_for_session(session_id, summary, None))
                + f"<p><b>Detail error:</b> {message}</p>"
            )
        self._start_next_detail_load()

    def _accept_selected_session(self) -> None:
        item = self.sessions_tree.currentItem()
        if item is None:
            return
        summary = item.data(self.columns.session_id, Qt.UserRole)
        if not isinstance(summary, dict):
            return
        if summary.get("categories", {}).get(self.required_category, {}).get("file_count", 0) <= 0:
            QMessageBox.information(
                self,
                "No Matching Data",
                f"Session {summary.get('id', '')} does not contain {self.required_category} data.",
            )
            return
        session_id = str(summary.get("id", ""))
        self._selected_session = CloudSessionSelection(
            config=self._config(),
            session_id=session_id,
            required_category=self.required_category,
            summary=summary,
            detail=self._detail_cache.get(session_id),
        )
        self.accept()

    def _config(self) -> CloudServiceConfig:
        return CloudServiceConfig(
            base_url=self.base_edit.text().strip() or DEFAULT_CLOUD_BASE_URL,
            access_token=self.token_edit.text().strip() or DEFAULT_CLOUD_ACCESS_TOKEN,
        )

    def _style_session_item(self, item: QTreeWidgetItem) -> None:
        iv_color = QColor("#ffd166")
        tran_color = QColor("#34d399")
        output_color = QColor("#60a5fa")
        tag_color = QColor("#c084fc")
        item.setForeground(self.columns.iv, iv_color)
        item.setForeground(self.columns.b1500_tran, tran_color)
        item.setForeground(self.columns.b1500_output, output_color)
        item.setForeground(self.columns.tag, tag_color)
        for column in [self.columns.tag, self.columns.iv, self.columns.b1500_tran, self.columns.b1500_output, self.columns.rows, self.columns.cols]:
            item.setTextAlignment(column, Qt.AlignCenter)

    def _apply_detail_to_item(self, item: QTreeWidgetItem, summary: dict, detail: dict) -> None:
        devices = detail.get("devices", {}) if isinstance(detail, dict) else {}
        files = detail.get("files", []) if isinstance(detail, dict) else []
        rows = devices.get("rows")
        cols = devices.get("cols")
        b1500_tran, b1500_output = _split_b1500_counts(files)
        if not b1500_tran and not b1500_output:
            b1500_total = int(summary.get("categories", {}).get("b1500", {}).get("file_count", 0) or 0)
            b1500_output = b1500_total
        item.setText(self.columns.b1500_tran, str(b1500_tran))
        item.setText(self.columns.b1500_output, str(b1500_output))
        item.setText(self.columns.rows, str(rows) if rows not in (None, "") else "--")
        item.setText(self.columns.cols, str(cols) if cols not in (None, "") else "--")
        session_id = str(summary.get("id", ""))
        tag = self._tag_for_session(session_id, summary, detail)
        item.setText(self.columns.tag, tag or "--")
        self._style_session_item(item)

    def _selected_tag(self) -> str:
        for tag, button in self.tag_buttons.items():
            if button.isChecked():
                return tag
        return ""

    def _apply_tag_to_controls(self, tag: str) -> None:
        normalized = tag if tag in self.tag_buttons else ""
        for value, button in self.tag_buttons.items():
            button.blockSignals(True)
            button.setChecked(value == normalized)
            button.blockSignals(False)
        self.tag_status_label.setText(f"Tag: {normalized or 'none'}")
        self._sync_tag_controls()

    def _clear_tag_selection(self) -> None:
        self._apply_tag_to_controls("")

    def _sync_tag_controls(self) -> None:
        item = self.sessions_tree.currentItem()
        enabled = item is not None
        self.clear_tag_button.setEnabled(enabled)
        self.save_tag_button.setEnabled(enabled)
        self.tag_status_label.setText(f"Tag: {self._selected_tag() or 'none'}")

    def _save_selected_tag(self) -> None:
        item = self.sessions_tree.currentItem()
        if item is None:
            return
        summary = item.data(self.columns.session_id, Qt.UserRole)
        if not isinstance(summary, dict):
            return
        session_id = str(summary.get("id", ""))
        tag = self._selected_tag() or None
        self.save_tag_button.setEnabled(False)
        storage_key = self._local_tag_key(session_id)
        if tag:
            self.local_tags[storage_key] = tag
        else:
            self.local_tags.pop(storage_key, None)
        save_local_session_tags(self.local_tag_store, self.local_tags)
        item.setText(self.columns.tag, tag or "--")
        self._style_session_item(item)
        self.status_label.setText(f"Local tag saved for {session_id}.")
        self._sync_tag_controls()
        self._on_selection_changed()

    def _local_tag_key(self, session_id: str) -> str:
        return f"{self._config().base_url.rstrip('/')}::{session_id}"

    def _tag_for_session(self, session_id: str, summary: dict | None, detail: dict | None) -> str:
        local = self.local_tags.get(self._local_tag_key(session_id), "").strip()
        if local:
            return local
        return extract_session_tag(summary, detail).strip()

    def _queue_detail_load(self, session_id: str, prioritize: bool = False) -> None:
        if session_id in self._detail_cache or session_id == self._detail_loading_session_id:
            return
        self._detail_prefetch_queue = [value for value in self._detail_prefetch_queue if value != session_id]
        if prioritize:
            self._detail_prefetch_queue.insert(0, session_id)
        else:
            self._detail_prefetch_queue.append(session_id)
        self._start_next_detail_load()

    def _start_next_detail_load(self) -> None:
        if self._detail_loading_session_id is not None or not self._detail_prefetch_queue:
            return
        session_id = self._detail_prefetch_queue.pop(0)
        if session_id in self._detail_cache:
            self._start_next_detail_load()
            return
        self._detail_loading_session_id = session_id
        if self._detail_request_session_id == session_id:
            self.status_label.setText(f"Loading detail for {session_id}...")
        self._detail_thread = QThread(self)
        self._detail_worker = CloudSessionDetailWorker(self._config(), session_id)
        self._detail_worker.moveToThread(self._detail_thread)
        self._detail_thread.started.connect(self._detail_worker.run)
        self._detail_worker.finished.connect(self._detail_loaded)
        self._detail_worker.failed.connect(self._detail_failed)
        self._detail_worker.finished.connect(self._detail_thread.quit)
        self._detail_worker.failed.connect(self._detail_thread.quit)
        self._detail_thread.finished.connect(self._detail_worker.deleteLater)
        self._detail_thread.finished.connect(self._detail_thread.deleteLater)
        self._detail_thread.start()


def _build_summary_html(summary: dict, detail: dict | None, required_category: str, display_tag: str | None = None) -> str:
    categories = summary.get("categories", {})
    detail_devices = detail.get("devices", {}) if detail else {}
    result_counts = detail.get("result_counts", {}) if detail else {}
    session_tag = display_tag or extract_session_tag(summary, detail) or "None"
    b1500_tran, b1500_output = _split_b1500_counts(detail.get("files", []) if detail else [])
    sample_files = []
    if detail:
        sample_files = [str(entry.get("path", "")) for entry in detail.get("files", []) if entry.get("category") == required_category][:12]
    files_html = "<br>".join(sample_files) if sample_files else "No file list loaded yet."
    result_html = "<br>".join(f"{key}: {value}" for key, value in result_counts.items()) or "No parsed result summary."
    device_sample = ", ".join(detail_devices.get("sample", [])) if detail_devices else "Not loaded"
    return f"""
    <h3>{summary.get('id', '')}</h3>
    <p><b>Created:</b> {summary.get('created_at', '')}<br>
    <b>Modified:</b> {summary.get('modified_at', '')}<br>
    <b>Status:</b> {summary.get('status', '')}<br>
    <b>Tag:</b> {session_tag}<br>
    <b>Total Files:</b> {summary.get('file_count', 0)}<br>
    <b>Package Size:</b> {_format_size(summary.get('size_bytes', 0))}</p>
    <p><b>Category Counts</b><br>
    IV: {categories.get('iv', {}).get('file_count', 0)} files<br>
    B1500 Tran: {b1500_tran}<br>
    B1500 Output: {b1500_output}<br>
    B1500 Total: {categories.get('b1500', {}).get('file_count', 0)} files<br>
    Images: {categories.get('images', {}).get('file_count', 0)} files<br>
    WOBB: {categories.get('wobb', {}).get('file_count', 0)} files<br>
    Other: {categories.get('other', {}).get('file_count', 0)} files</p>
    <p><b>Devices</b><br>
    Count: {detail_devices.get('count', 'Not loaded')}<br>
    Rows: {detail_devices.get('rows', 'Not loaded')}<br>
    Cols: {detail_devices.get('cols', 'Not loaded')}<br>
    Sample: {device_sample}</p>
    <p><b>Result Types</b><br>{result_html}</p>
    <p><b>{required_category.upper()} Sample Files</b><br>{files_html}</p>
    """


def _format_size(size_bytes: int | float | None) -> str:
    value = float(size_bytes or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    unit = units[0]
    for candidate in units:
        unit = candidate
        if value < 1024 or candidate == units[-1]:
            break
        value /= 1024
    if unit == "B":
        return f"{int(value)} {unit}"
    return f"{value:.1f} {unit}"


def _split_b1500_counts(files: list[dict]) -> tuple[int, int]:
    tran = 0
    output = 0
    for entry in files:
        if str(entry.get("category", "")).lower() != "b1500":
            continue
        path = str(entry.get("path", "")).lower()
        name = str(entry.get("name", "")).lower()
        target = f"{path} {name}"
        if "tran" in target:
            tran += 1
        elif "output" in target:
            output += 1
    return tran, output
