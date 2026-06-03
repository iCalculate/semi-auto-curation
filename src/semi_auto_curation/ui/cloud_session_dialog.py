from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    get_session_detail,
    list_sessions,
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
    session_id: int = 0
    created_at: int = 1
    status: int = 2
    iv: int = 3
    b1500: int = 4
    images: int = 5
    files: int = 6
    size: int = 7


class CloudSessionsDialog(QDialog):
    def __init__(self, required_category: str, parent=None) -> None:
        super().__init__(parent)
        self.required_category = required_category
        self.columns = SessionTreeColumns()
        self._selected_session: CloudSessionSelection | None = None
        self._detail_cache: dict[str, dict] = {}
        self._detail_request_session_id: str | None = None
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
        self.sessions_tree.setHeaderLabels(["Session", "Created", "Status", "IV", "B1500", "Images", "Files", "Size"])
        self.detail_view = QTextEdit()
        self.detail_view.setReadOnly(True)
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
        splitter.addWidget(right)
        splitter.setSizes([760, 440])
        root.addWidget(splitter, 1)

        button_row = QHBoxLayout()
        button_row.addWidget(self.status_label, 1)
        button_row.addWidget(self.use_button)
        button_row.addWidget(self.close_button)
        root.addLayout(button_row)

        header = self.sessions_tree.header()
        header.setSectionResizeMode(self.columns.session_id, QHeaderView.Stretch)
        for section in [
            self.columns.created_at,
            self.columns.status,
            self.columns.iv,
            self.columns.b1500,
            self.columns.images,
            self.columns.files,
            self.columns.size,
        ]:
            header.setSectionResizeMode(section, QHeaderView.ResizeToContents)

    def _wire_events(self) -> None:
        self.refresh_button.clicked.connect(self.refresh_sessions)
        self.close_button.clicked.connect(self.reject)
        self.use_button.clicked.connect(self._accept_selected_session)
        self.sessions_tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.sessions_tree.itemDoubleClicked.connect(lambda *_: self._accept_selected_session())

    def refresh_sessions(self) -> None:
        self.status_label.setText("Loading cloud sessions...")
        self.refresh_button.setEnabled(False)
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
            item = QTreeWidgetItem(
                [
                    str(session.get("id", "")),
                    str(session.get("created_at", "")),
                    str(session.get("status", "")),
                    str(categories.get("iv", {}).get("file_count", 0)),
                    str(categories.get("b1500", {}).get("file_count", 0)),
                    str(categories.get("images", {}).get("file_count", 0)),
                    str(session.get("file_count", 0)),
                    _format_size(session.get("size_bytes", 0)),
                ]
            )
            item.setData(self.columns.session_id, Qt.UserRole, session)
            self.sessions_tree.addTopLevelItem(item)
        self.status_label.setText("Cloud sessions loaded.")
        if self.sessions_tree.topLevelItemCount():
            self.sessions_tree.setCurrentItem(self.sessions_tree.topLevelItem(0))

    def _sessions_failed(self, message: str) -> None:
        self.refresh_button.setEnabled(True)
        self.status_label.setText("Cloud sessions load failed.")
        QMessageBox.critical(self, "Cloud Sessions Failed", message)

    def _on_selection_changed(self) -> None:
        item = self.sessions_tree.currentItem()
        if item is None:
            self.use_button.setEnabled(False)
            self.detail_view.clear()
            return
        summary = item.data(self.columns.session_id, Qt.UserRole)
        if not isinstance(summary, dict):
            self.use_button.setEnabled(False)
            return
        has_required = summary.get("categories", {}).get(self.required_category, {}).get("file_count", 0) > 0
        self.use_button.setEnabled(has_required)
        self.detail_view.setHtml(_build_summary_html(summary, None, self.required_category))
        session_id = str(summary.get("id", ""))
        if session_id in self._detail_cache:
            self.detail_view.setHtml(_build_summary_html(summary, self._detail_cache[session_id], self.required_category))
            return
        self._detail_request_session_id = session_id
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

    def _detail_loaded(self, session_id: str, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        self._detail_cache[session_id] = payload
        current_item = self.sessions_tree.currentItem()
        if current_item is None:
            return
        summary = current_item.data(self.columns.session_id, Qt.UserRole)
        if not isinstance(summary, dict):
            return
        if str(summary.get("id", "")) != session_id:
            return
        self.detail_view.setHtml(_build_summary_html(summary, payload, self.required_category))
        self.status_label.setText(f"Detail loaded for {session_id}.")

    def _detail_failed(self, session_id: str, message: str) -> None:
        if self._detail_request_session_id == session_id:
            self.status_label.setText(f"Detail load failed for {session_id}.")
        current_item = self.sessions_tree.currentItem()
        if current_item is None:
            return
        summary = current_item.data(self.columns.session_id, Qt.UserRole)
        if not isinstance(summary, dict):
            return
        if str(summary.get("id", "")) == session_id:
            self.detail_view.setHtml(_build_summary_html(summary, None, self.required_category) + f"<p><b>Detail error:</b> {message}</p>")

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


def _build_summary_html(summary: dict, detail: dict | None, required_category: str) -> str:
    categories = summary.get("categories", {})
    detail_devices = detail.get("devices", {}) if detail else {}
    result_counts = detail.get("result_counts", {}) if detail else {}
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
    <b>Total Files:</b> {summary.get('file_count', 0)}<br>
    <b>Package Size:</b> {_format_size(summary.get('size_bytes', 0))}</p>
    <p><b>Category Counts</b><br>
    IV: {categories.get('iv', {}).get('file_count', 0)} files<br>
    B1500: {categories.get('b1500', {}).get('file_count', 0)} files<br>
    Images: {categories.get('images', {}).get('file_count', 0)} files<br>
    WOBB: {categories.get('wobb', {}).get('file_count', 0)} files<br>
    Other: {categories.get('other', {}).get('file_count', 0)} files</p>
    <p><b>Devices</b><br>
    Count: {detail_devices.get('count', 'Not loaded')}<br>
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
