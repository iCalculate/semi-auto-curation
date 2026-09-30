from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QObject,
    QStandardPaths,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QGridLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from semi_auto_curation.services.session_index import (
    SessionRecord,
    filter_sessions,
    load_index,
    refresh_index,
)
from semi_auto_curation.settings import DEFAULT_AUTOTEST_SESSION_ROOT
from semi_auto_curation.ui.iv_panel import THEMES


STATUS_COLORS = {
    "Complete": "#2f9e62",
    "Stopped": "#d39a2c",
    "Failed": "#d95656",
    "Unknown": "#6d7888",
}

SHORT_SLOT_MINUTES = 15.0


class SessionEventItem(QGraphicsRectItem):
    def __init__(self, width: float, height: float, color: QColor, theme: str) -> None:
        super().__init__(0, 0, width, height)
        self._base_edge = color.lighter(125)
        self._theme = theme
        self._hovered = False
        self._selected = False
        self.setBrush(color)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)
        self._update_edge()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._update_edge()

    def set_hovered(self, hovered: bool) -> None:
        self._hovered = hovered
        self._update_edge()

    def hoverEnterEvent(self, event) -> None:
        self.set_hovered(True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self.set_hovered(False)
        super().hoverLeaveEvent(event)

    def _update_edge(self) -> None:
        if self._selected:
            edge = QColor("#d8edff" if self._theme == "dark" else "#175f96")
            width = 2.4
            self.setZValue(10)
        elif self._hovered:
            edge = QColor("#aab3be" if self._theme == "dark" else "#7b8794")
            width = 1.8
            self.setZValue(5)
        else:
            edge = self._base_edge
            width = 1.0
            self.setZValue(2)
        self.setPen(QPen(edge, width))


class SessionIndexWorker(QObject):
    finished = Signal(object, object)
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, root: Path, database_path: Path) -> None:
        super().__init__()
        self.root = root
        self.database_path = database_path

    def run(self) -> None:
        try:
            records, warnings = refresh_index(self.root, self.database_path, self.progress.emit)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(records, warnings)


class CalendarSurface(QGraphicsView):
    session_clicked = Signal(object)
    day_requested = Signal(object)
    date_double_clicked = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setFrameShape(QFrame.NoFrame)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setProperty("preserveMouseClicks", True)
        self.setMinimumSize(620, 520)
        self.records: list[SessionRecord] = []
        self.view_mode = "Month"
        self.anchor = date.today()
        self.theme = "dark"
        self.selected_session_id: str | None = None
        self.hovered_session_id: str | None = None
        self._pressed_record: SessionRecord | None = None
        self._pressed_day: date | None = None
        self._press_consumed = False
        self._rendering = False
        self._scene_item_refs: list[object] = []
        self._resize_render_timer = QTimer(self)
        self._resize_render_timer.setSingleShot(True)
        self._resize_render_timer.timeout.connect(self.render)

    def set_content(self, records: list[SessionRecord], view_mode: str, anchor: date, theme: str) -> None:
        self.records = records
        self.view_mode = view_mode
        self.anchor = anchor
        self.theme = theme
        self.render()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._resize_render_timer.start(0)

    def mousePressEvent(self, event) -> None:
        self._pressed_record = None
        self._pressed_day = None
        self._press_consumed = False
        day = self._day_at(event.position().toPoint())
        if day is not None:
            self._pressed_day = day
            self._press_consumed = True
            event.accept()
            return
        record = self._record_at(event.position().toPoint())
        if record is not None:
            self._pressed_record = record
            self._press_consumed = True
            event.accept()
            return
        if self.view_mode in {"Month", "Week"} and self._date_at_position(event.position().toPoint()) is not None:
            self._press_consumed = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if not self._press_consumed:
            super().mouseReleaseEvent(event)
            return
        record = self._pressed_record
        selected_day = self._pressed_day
        self._pressed_record = None
        self._pressed_day = None
        self._press_consumed = False
        event.accept()
        if record is not None and self._record_at(event.position().toPoint()) is record:
            self.set_selected_record(record)
            QTimer.singleShot(0, lambda record=record: self.session_clicked.emit(record))
        elif selected_day is not None:
            QTimer.singleShot(0, lambda selected_day=selected_day: self.day_requested.emit(selected_day))

    def mouseMoveEvent(self, event) -> None:
        record = self._record_at(event.position().toPoint())
        self.set_hovered_record(record)
        super().mouseMoveEvent(event)

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.MouseMove and hasattr(event, "position"):
            self.set_hovered_record(self._record_at(event.position().toPoint()))
        elif event.type() == QEvent.Type.Leave:
            self.set_hovered_record(None)
        return super().viewportEvent(event)

    def leaveEvent(self, event) -> None:
        self.set_hovered_record(None)
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        record = self._record_at(event.position().toPoint())
        if record is not None:
            self._pressed_record = None
            self._pressed_day = None
            self._press_consumed = True
            self.set_selected_record(record)
            QTimer.singleShot(0, lambda record=record: self.session_clicked.emit(record))
            event.accept()
            return
        selected_day = self._date_at_position(event.position().toPoint())
        if selected_day is not None and self.view_mode in {"Month", "Week"}:
            self._press_consumed = True
            QTimer.singleShot(0, lambda selected_day=selected_day: self.date_double_clicked.emit(selected_day))
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:
        if self.view_mode in {"Week", "Day"} and self.verticalScrollBar().maximum() > 0:
            pixel_delta = event.pixelDelta().y()
            if pixel_delta:
                distance = -pixel_delta
            else:
                angle_delta = event.angleDelta().y()
                step = max(48, self.verticalScrollBar().singleStep() * 3)
                distance = round(-angle_delta / 120.0 * step)
            if distance:
                scroll_bar = self.verticalScrollBar()
                scroll_bar.setValue(scroll_bar.value() + distance)
                event.accept()
                return
        super().wheelEvent(event)

    def _record_at(self, point) -> SessionRecord | None:
        item = self.itemAt(point)
        while item is not None:
            value = item.data(0)
            if isinstance(value, SessionRecord):
                return value
            item = item.parentItem()
        return None

    def _day_at(self, point) -> date | None:
        item = self.itemAt(point)
        while item is not None:
            value = item.data(1)
            if isinstance(value, date):
                return value
            item = item.parentItem()
        return None

    def _date_at_position(self, point) -> date | None:
        scene_point = self.mapToScene(point)
        scene_rect = self.sceneRect()
        if self.view_mode == "Month":
            header_h = 28.0
            if scene_point.y() < header_h or not scene_rect.contains(scene_point):
                return None
            cell_w = scene_rect.width() / 7.0
            cell_h = (scene_rect.height() - header_h) / 6.0
            column = min(6, max(0, int(scene_point.x() // cell_w)))
            row = min(5, max(0, int((scene_point.y() - header_h) // cell_h)))
            first = self.anchor.replace(day=1)
            grid_start = first - timedelta(days=first.weekday())
            return grid_start + timedelta(days=row * 7 + column)
        if self.view_mode == "Week":
            time_w = 54.0
            header_h = 38.0
            if (
                scene_point.x() < time_w
                or scene_point.x() > scene_rect.right()
                or scene_point.y() < scene_rect.top()
                or scene_point.y() >= header_h
            ):
                return None
            day_w = (scene_rect.width() - time_w) / 7.0
            column = min(6, max(0, int((scene_point.x() - time_w) // day_w)))
            week_start = self.anchor - timedelta(days=self.anchor.weekday())
            return week_start + timedelta(days=column)
        return None

    def set_selected_record(self, record: SessionRecord | None) -> None:
        self.selected_session_id = record.session_id if record is not None else None
        for item in self._scene_item_refs:
            if isinstance(item, SessionEventItem):
                item_record = item.data(0)
                item.set_selected(
                    isinstance(item_record, SessionRecord) and item_record.session_id == self.selected_session_id
                )

    def set_hovered_record(self, record: SessionRecord | None) -> None:
        hovered_session_id = record.session_id if record is not None else None
        if hovered_session_id == self.hovered_session_id:
            return
        self.hovered_session_id = hovered_session_id
        for item in self._scene_item_refs:
            if isinstance(item, SessionEventItem):
                item_record = item.data(0)
                item.set_hovered(
                    isinstance(item_record, SessionRecord) and item_record.session_id == self.hovered_session_id
                )

    def render(self) -> None:
        if self._rendering:
            self._resize_render_timer.start(0)
            return
        self._rendering = True
        try:
            self._render_scene()
        finally:
            self._rendering = False

    def _render_scene(self) -> None:
        scene = self.scene()
        self.release_scene_items()
        width = max(600.0, float(self.viewport().width() - 2))
        height = max(480.0, float(self.viewport().height() - 2))
        scene.setSceneRect(0, 0, width, height)
        if self.view_mode == "Month":
            self._draw_month(width, height)
        else:
            self._draw_timeline(width, height, self.view_mode == "Day")

    def _keep_scene_item(self, item):
        self._scene_item_refs.append(item)
        return item

    def release_scene_items(self) -> None:
        while self._scene_item_refs:
            item = self._scene_item_refs.pop()
            del item
        self.scene().clear()

    def _draw_month(self, width: float, height: float) -> None:
        scene = self.scene()
        first = self.anchor.replace(day=1)
        grid_start = first - timedelta(days=first.weekday())
        header_h = 28.0
        cell_w = width / 7.0
        cell_h = (height - header_h) / 6.0
        muted = QColor("#8d99a8" if self.theme == "dark" else "#667085")
        line = QPen(QColor("#3d4652" if self.theme == "dark" else "#d7dce2"), 1)
        grid_font = QFont("Segoe UI", 9)
        for column, label in enumerate(calendar.day_abbr):
            text = self._keep_scene_item(scene.addText(label, grid_font))
            text.setDefaultTextColor(muted)
            text.setPos(column * cell_w + 8, 3)
        records_by_day: dict[date, list[SessionRecord]] = {}
        for record in self.records:
            records_by_day.setdefault(record.started_at.date(), []).append(record)
        for index in range(42):
            day = grid_start + timedelta(days=index)
            row, column = divmod(index, 7)
            x, y = column * cell_w, header_h + row * cell_h
            background = QColor("#202833" if self.theme == "dark" else "#ffffff")
            if day.month != first.month:
                background = QColor("#191f27" if self.theme == "dark" else "#f3f5f7")
            self._keep_scene_item(scene.addRect(x, y, cell_w, cell_h, line, background))
            number = self._keep_scene_item(scene.addText(str(day.day), grid_font))
            number.setDefaultTextColor(muted)
            number.setPos(x + 7, y + 3)
            day_records = sorted(records_by_day.get(day, []), key=lambda item: item.started_at)
            available = min(3, max(1, int((cell_h - 27) // 22)))
            visible = day_records[:available]
            for slot, record in enumerate(visible):
                next_record = day_records[slot + 1] if slot + 1 < len(day_records) else None
                duration = _visual_duration_minutes(record, next_record)
                self._add_event_card(
                    x + 6,
                    y + 25 + slot * 22,
                    cell_w - 12,
                    18,
                    record,
                    compact=True,
                    line_mode=duration < SHORT_SLOT_MINUTES,
                    show_line_label=True,
                )
            hidden = len(day_records) - len(visible)
            if hidden:
                more = self._keep_scene_item(scene.addText(f"+{hidden} more", grid_font))
                more.setDefaultTextColor(muted)
                more.setPos(x + 8, y + cell_h - 22)
                more.setData(1, day)
                more.setToolTip("Open this day")

    def _draw_timeline(self, width: float, height: float, single_day: bool) -> None:
        scene = self.scene()
        days = 1 if single_day else 7
        start_day = self.anchor if single_day else self.anchor - timedelta(days=self.anchor.weekday())
        time_w = 54.0
        header_h = 38.0
        content_h = max(1200.0, height - header_h)
        hour_h = content_h / 24.0
        day_w = (width - time_w) / days
        line_color = QColor("#3d4652" if self.theme == "dark" else "#d7dce2")
        muted = QColor("#8d99a8" if self.theme == "dark" else "#667085")
        grid_font = QFont("Segoe UI", 9)
        for day_index in range(days):
            current = start_day + timedelta(days=day_index)
            label = self._keep_scene_item(scene.addText(current.strftime("%a  %b %d"), grid_font))
            label.setDefaultTextColor(muted)
            label.setPos(time_w + day_index * day_w + 8, 7)
            self._keep_scene_item(
                scene.addLine(time_w + day_index * day_w, 0, time_w + day_index * day_w, header_h + content_h, QPen(line_color))
            )
        for hour in range(25):
            y = header_h + hour * hour_h
            self._keep_scene_item(scene.addLine(time_w, y, width, y, QPen(line_color, 0.7)))
            if hour < 24:
                label = self._keep_scene_item(scene.addText(f"{hour:02d}:00", grid_font))
                label.setDefaultTextColor(muted)
                label.setPos(5, y - 8)
        scene.setSceneRect(0, 0, width, header_h + content_h)

        for day_index in range(days):
            current = start_day + timedelta(days=day_index)
            day_records = sorted(
                (record for record in self.records if record.started_at.date() == current),
                key=lambda item: item.started_at,
            )
            visual_cursor = header_h
            for record_index, record in enumerate(day_records):
                next_record = day_records[record_index + 1] if record_index + 1 < len(day_records) else None
                duration_minutes = _visual_duration_minutes(record, next_record)
                minutes = record.started_at.hour * 60 + record.started_at.minute + record.started_at.second / 60.0
                x = time_w + day_index * day_w + 5
                y = max(header_h + minutes / 60.0 * hour_h, visual_cursor)
                card_w = day_w - 10
                line_mode = duration_minutes < SHORT_SLOT_MINUTES
                card_h = 4.0 if line_mode else max(24.0, duration_minutes / 60.0 * hour_h)
                self._add_event_card(
                    x,
                    y,
                    card_w,
                    card_h,
                    record,
                    compact=card_h < 48,
                    line_mode=line_mode,
                )
                visual_cursor = y + card_h + (3.0 if line_mode else 0.0)

    def _add_event_card(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        record: SessionRecord,
        *,
        compact: bool,
        line_mode: bool = False,
        show_line_label: bool = False,
    ) -> None:
        scene = self.scene()
        color = QColor(STATUS_COLORS.get(record.status, STATUS_COLORS["Unknown"]))
        color.setAlpha(230 if self.theme == "dark" else 240)
        card_height = 4.0 if line_mode else max(18.0, height)
        card = self._keep_scene_item(SessionEventItem(max(20.0, width), card_height, color, self.theme))
        scene.addItem(card)
        card.setPos(x, y + (14 if line_mode and show_line_label else 0))
        card.setData(0, record)
        card.setToolTip(_event_tooltip(record))
        card.set_selected(record.session_id == self.selected_session_id)
        card.set_hovered(record.session_id == self.hovered_session_id)
        if line_mode:
            if show_line_label:
                font = QFont("Segoe UI", 9)
                label = QFontMetrics(font).elidedText(record.session_id, Qt.ElideRight, max(10, int(width - 8)))
                text = self._keep_scene_item(scene.addText(label, font))
                text.setDefaultTextColor(QColor("#dce5ef" if self.theme == "dark" else "#253246"))
                text.setParentItem(card)
                text.setPos(4, -17)
            return
        font = QFont("Segoe UI", 9 if compact else 10)
        metrics = QFontMetrics(font)
        available_width = max(10, int(width - 10))
        label = metrics.elidedText(record.session_id, Qt.ElideRight, available_width)
        if not compact:
            categories = " · ".join(item.name.upper() for item in record.categories[:3])
            lines = (
                record.started_at.strftime("%H:%M"),
                metrics.elidedText(record.session_id, Qt.ElideRight, available_width),
                metrics.elidedText(categories or "NO DATA", Qt.ElideRight, available_width),
                metrics.elidedText(f"{record.progress_text} points", Qt.ElideRight, available_width),
            )
            label = "\n".join(lines)
        text = self._keep_scene_item(scene.addText(label, font))
        text.setDefaultTextColor(QColor("#ffffff"))
        text.setParentItem(card)
        text.setPos(4, 1)


class TestCalendarPanel(QWidget):
    title = "Test Calendar"
    status_changed = Signal(str)
    progress_changed = Signal(int)
    open_session_requested = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        cache_root = Path(QStandardPaths.writableLocation(QStandardPaths.CacheLocation) or (Path.cwd() / ".cache"))
        self.database_path = cache_root / "test_calendar" / "sessions.sqlite3"
        cache_error = ""
        try:
            self.records = load_index(self.database_path)
        except Exception as exc:
            self.records = []
            cache_error = f"Could not read the cached index: {exc}. Click Refresh to rebuild it."
        self.filtered_records: list[SessionRecord] = []
        self.anchor = date.today()
        self.view_mode = "Month"
        self.theme = "dark"
        self._thread: QThread | None = None
        self._worker: SessionIndexWorker | None = None
        self.range_label = QLabel()
        self.range_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search session, device, category, status…")
        self.category_combo = QComboBox()
        self.status_combo = QComboBox()
        self.status_combo.addItems(["All statuses", "Complete", "Stopped", "Failed", "Unknown"])
        self.refresh_button = QPushButton("Refresh")
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setVisible(False)
        self.surface = CalendarSurface()
        self.detail_panel = QFrame()
        self.detail_panel.setFrameShape(QFrame.StyledPanel)
        self.detail_panel.setMinimumWidth(420)
        self.detail_panel.setMaximumWidth(580)
        self.detail_header = QFrame()
        self.detail_header.setObjectName("SessionHeaderCard")
        self.detail_eyebrow = QLabel("TEST SESSION")
        self.detail_eyebrow.setObjectName("SessionHeaderEyebrow")
        self.detail_title = QLabel("No session selected")
        self.detail_title.setObjectName("SessionHeaderTitle")
        self.detail_id = QLabel("Select a slot from the calendar")
        self.detail_id.setObjectName("SessionHeaderId")
        self.detail_subtitle = QLabel("Session timing and measurement summary will appear here.")
        self.detail_subtitle.setObjectName("SessionHeaderSubtitle")
        self.detail_subtitle.setWordWrap(True)
        self.status_badge = QLabel("NO SELECTION")
        self.status_badge.setObjectName("SessionStatusBadge")
        self.status_badge.setAlignment(Qt.AlignCenter)
        self.status_badge.setFixedSize(112, 24)
        self.stat_values = {name: QLabel("—") for name in ("Duration", "Progress", "Devices", "Data")}
        self.stat_secondary = {name: QLabel("—") for name in self.stat_values}
        self.workspace_buttons: list[QPushButton] = []
        self.category_table = QTableWidget(0, 5)
        self.category_table.setHorizontalHeaderLabels(["Category", "Files", "Devices", "Size", "Workspace"])
        self.category_table.verticalHeader().setVisible(False)
        self.category_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.category_table.setSelectionMode(QTableWidget.NoSelection)
        self.category_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            self.category_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.category_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.category_table.setColumnWidth(4, 138)
        self.category_table.setMaximumHeight(235)
        self.device_list = QListWidget()
        self.device_list.setMaximumHeight(180)
        self.path_label = QLabel("—")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail_warning = QLabel()
        self.detail_warning.setWordWrap(True)
        self.detail_warning.setVisible(False)
        self._build_ui()
        self._connect_signals()
        self.set_theme("dark")
        self._rebuild_filters()
        self._apply_filters()
        if cache_error:
            self.warning_label.setText(cache_error)
            self.warning_label.setVisible(True)

    def build_toolbar_actions(self) -> list[QAction]:
        refresh = QAction("Refresh Sessions", self)
        refresh.triggered.connect(self.refresh_sessions)
        today = QAction("Today", self)
        today.triggered.connect(self.go_today)
        return [refresh, today]

    def set_theme(self, theme: str) -> None:
        if theme not in THEMES:
            return
        self.theme = theme
        if theme == "dark":
            calendar_style = """
                QFrame#SessionHeaderCard { background: #242e39; border: 0; border-left: 3px solid #6da2cc; border-radius: 0; }
                QLabel#SessionHeaderEyebrow { color: #8fa8c2; font: 650 11px "Segoe UI"; letter-spacing: 1.5px; }
                QLabel#SessionHeaderTitle { color: #f7f9fc; font: 650 24px "Segoe UI Variable Display"; }
                QLabel#SessionHeaderId { color: #aab8c8; font: 13px "Cascadia Mono"; letter-spacing: 0.5px; }
                QLabel#SessionHeaderSubtitle { color: #bdc9d6; font-size: 13px; }
                QFrame#SessionMetricsRail { background: #212933; border: 0; border-top: 2px solid #6da2cc; border-bottom: 1px solid #414b58; border-radius: 0; }
                QFrame#SessionMetricCell { background: transparent; border: 0; }
                QFrame#SessionMetricDivider { background: #3b4653; border: 0; }
                QLabel#SessionStatCaption, QLabel#SessionSectionTitle { color: #91a0b2; font-size: 11px; font-weight: 650; letter-spacing: 0.8px; }
                QLabel#SessionStatValue { color: #f8fafc; font: 600 23px "Segoe UI Variable Display"; }
                QLabel#SessionStatSecondary { color: #9cabbc; font-size: 12px; }
                QPushButton#SessionTableAction { background: transparent; color: #9dcaed; border: 0; border-left: 2px solid #6da2cc; border-radius: 0; padding: 4px 8px; text-align: left; font-size: 12px; font-weight: 600; }
                QPushButton#SessionTableAction:hover { background: #2c3946; color: #c7e4fa; }
                QPushButton#SessionTableAction:pressed { background: #202a34; }
                QTableWidget, QListWidget { border: 1px solid #3b4653; border-radius: 0; background: #20262e; font-size: 12px; }
                QHeaderView::section { background: #2a323c; color: #c8d2dd; border: 0; padding: 7px 6px; font-size: 11px; font-weight: 600; }
            """
        else:
            calendar_style = """
                QFrame#SessionHeaderCard { background: #f1f5f9; border: 0; border-left: 3px solid #438bc5; border-radius: 0; }
                QLabel#SessionHeaderEyebrow { color: #52708f; font: 650 11px "Segoe UI"; letter-spacing: 1.5px; }
                QLabel#SessionHeaderTitle { color: #172b3f; font: 650 24px "Segoe UI Variable Display"; }
                QLabel#SessionHeaderId { color: #61758b; font: 13px "Cascadia Mono"; letter-spacing: 0.5px; }
                QLabel#SessionHeaderSubtitle { color: #526579; font-size: 13px; }
                QFrame#SessionMetricsRail { background: #f7f9fb; border: 0; border-top: 2px solid #438bc5; border-bottom: 1px solid #d7e0ea; border-radius: 0; }
                QFrame#SessionMetricCell { background: transparent; border: 0; }
                QFrame#SessionMetricDivider { background: #d7e0ea; border: 0; }
                QLabel#SessionStatCaption, QLabel#SessionSectionTitle { color: #667085; font-size: 11px; font-weight: 650; letter-spacing: 0.8px; }
                QLabel#SessionStatValue { color: #16283b; font: 600 23px "Segoe UI Variable Display"; }
                QLabel#SessionStatSecondary { color: #66778b; font-size: 12px; }
                QPushButton#SessionTableAction { background: transparent; color: #286b9e; border: 0; border-left: 2px solid #438bc5; border-radius: 0; padding: 4px 8px; text-align: left; font-size: 12px; font-weight: 600; }
                QPushButton#SessionTableAction:hover { background: #e4edf4; color: #174f79; }
                QPushButton#SessionTableAction:pressed { background: #d3e0ea; }
                QTableWidget, QListWidget { border: 1px solid #d9dfe7; border-radius: 0; background: #ffffff; font-size: 12px; }
                QHeaderView::section { background: #eef1f5; color: #475467; border: 0; padding: 7px 6px; font-size: 11px; font-weight: 600; }
            """
        calendar_style += """
            QFrame#SessionHeaderCard QLabel, QFrame#SessionMetricCell QLabel {
                background: transparent; border: 0;
            }
            QLineEdit, QComboBox, QPushButton { font-size: 12px; }
        """
        self.setStyleSheet(THEMES[theme]["qt_stylesheet"] + calendar_style)
        self._render(animate=False)

    def refresh_sessions(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self.refresh_button.setEnabled(False)
        self.warning_label.setVisible(False)
        self.status_changed.emit("Scanning test sessions…")
        self.progress_changed.emit(1)
        self._thread = QThread(self)
        self._worker = SessionIndexWorker(DEFAULT_AUTOTEST_SESSION_ROOT, self.database_path)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._refresh_finished)
        self._worker.failed.connect(self._refresh_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._clear_worker)
        self._thread.start()

    def closeEvent(self, event) -> None:
        if self._thread is not None and self._thread.isRunning():
            event.ignore()
            self.status_changed.emit("Wait for the session refresh to finish before closing this workspace.")
            return
        self.surface.release_scene_items()
        super().closeEvent(event)

    def go_today(self) -> None:
        self.anchor = date.today()
        self._render(direction=0)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        navigation = QHBoxLayout()
        previous = QPushButton("‹")
        today = QPushButton("Today")
        next_button = QPushButton("›")
        previous.clicked.connect(lambda: self._navigate(-1))
        today.clicked.connect(self.go_today)
        next_button.clicked.connect(lambda: self._navigate(1))
        navigation.addWidget(previous)
        navigation.addWidget(today)
        navigation.addWidget(next_button)
        navigation.addWidget(self.range_label)
        navigation.addStretch()
        self.view_buttons = QButtonGroup(self)
        self.view_buttons.setExclusive(True)
        for label in ("Month", "Week", "Day"):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(label == self.view_mode)
            button.clicked.connect(lambda checked=False, value=label: self._set_view(value))
            self.view_buttons.addButton(button)
            navigation.addWidget(button)
        navigation.addWidget(self.refresh_button)
        root.addLayout(navigation)

        filters = QHBoxLayout()
        filters.addWidget(self.search_edit, 2)
        filters.addWidget(self.category_combo, 1)
        filters.addWidget(self.status_combo, 1)
        root.addLayout(filters)

        legend = QHBoxLayout()
        legend.setSpacing(14)
        for status in ("Complete", "Stopped", "Failed", "Unknown"):
            item = QLabel(f"●  {status}")
            item.setStyleSheet(f"color: {STATUS_COLORS[status]}; font-weight: 600;")
            legend.addWidget(item)
        legend.addStretch()
        root.addLayout(legend)
        root.addWidget(self.warning_label)

        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(16, 16, 16, 16)
        detail_layout.setSpacing(10)
        header_layout = QVBoxLayout(self.detail_header)
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(5)
        header_top = QHBoxLayout()
        header_top.addWidget(self.detail_eyebrow)
        header_top.addStretch()
        header_top.addWidget(self.status_badge)
        header_layout.addLayout(header_top)
        header_layout.addWidget(self.detail_title)
        header_layout.addWidget(self.detail_id)
        header_layout.addWidget(self.detail_subtitle)
        detail_layout.addWidget(self.detail_header)
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QFrame.NoFrame)
        detail_container = QWidget()
        detail_container_layout = QVBoxLayout(detail_container)
        detail_container_layout.setContentsMargins(0, 8, 0, 0)
        detail_container_layout.setSpacing(10)

        metrics_frame = QFrame()
        metrics_frame.setObjectName("SessionMetricsRail")
        metrics_grid = QGridLayout(metrics_frame)
        metrics_grid.setContentsMargins(0, 0, 0, 0)
        metrics_grid.setHorizontalSpacing(0)
        metrics_grid.setVerticalSpacing(0)
        for index, (name, value_label) in enumerate(self.stat_values.items()):
            cell = QFrame()
            cell.setObjectName("SessionMetricCell")
            cell.setMinimumHeight(84)
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(13, 11, 13, 10)
            cell_layout.setSpacing(2)
            caption = QLabel(name.upper())
            caption.setObjectName("SessionStatCaption")
            value_label.setObjectName("SessionStatValue")
            value_label.setWordWrap(True)
            secondary = self.stat_secondary[name]
            secondary.setObjectName("SessionStatSecondary")
            secondary.setWordWrap(True)
            cell_layout.addWidget(caption)
            cell_layout.addWidget(value_label)
            cell_layout.addWidget(secondary)
            row = (index // 2) * 2
            column = (index % 2) * 2
            metrics_grid.addWidget(cell, row, column)

        vertical_divider = QFrame()
        vertical_divider.setObjectName("SessionMetricDivider")
        vertical_divider.setFixedWidth(1)
        metrics_grid.addWidget(vertical_divider, 0, 1, 3, 1)
        horizontal_divider = QFrame()
        horizontal_divider.setObjectName("SessionMetricDivider")
        horizontal_divider.setFixedHeight(1)
        metrics_grid.addWidget(horizontal_divider, 1, 0, 1, 3)
        metrics_grid.setColumnStretch(0, 1)
        metrics_grid.setColumnStretch(2, 1)
        detail_container_layout.addWidget(metrics_frame)
        category_title = QLabel("DATA BY CATEGORY")
        category_title.setObjectName("SessionSectionTitle")
        detail_container_layout.addWidget(category_title)
        detail_container_layout.addWidget(self.category_table)
        device_title = QLabel("DEVICES")
        device_title.setObjectName("SessionSectionTitle")
        detail_container_layout.addWidget(device_title)
        detail_container_layout.addWidget(self.device_list)
        path_title = QLabel("SESSION PATH")
        path_title.setObjectName("SessionSectionTitle")
        detail_container_layout.addWidget(path_title)
        detail_container_layout.addWidget(self.path_label)
        detail_container_layout.addWidget(self.detail_warning)
        detail_container_layout.addStretch()
        detail_scroll.setWidget(detail_container)
        detail_layout.addWidget(detail_scroll, 1)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.surface)
        splitter.addWidget(self.detail_panel)
        splitter.setSizes([920, 500])
        root.addWidget(splitter, 1)

    def _connect_signals(self) -> None:
        self.refresh_button.clicked.connect(self.refresh_sessions)
        self.search_edit.textChanged.connect(self._apply_filters)
        self.category_combo.currentTextChanged.connect(self._apply_filters)
        self.status_combo.currentTextChanged.connect(self._apply_filters)
        self.surface.session_clicked.connect(self._show_details)
        self.surface.day_requested.connect(self._open_day)
        self.surface.date_double_clicked.connect(self._drill_into_date)

    def _rebuild_filters(self) -> None:
        current = self.category_combo.currentText()
        categories = sorted({item.name for record in self.records for item in record.categories})
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItems(["All categories", *categories])
        if current in categories:
            self.category_combo.setCurrentText(current)
        self.category_combo.blockSignals(False)

    def _apply_filters(self) -> None:
        self.filtered_records = filter_sessions(
            self.records,
            query=self.search_edit.text(),
            category=self.category_combo.currentText() or "All categories",
            status=self.status_combo.currentText() or "All statuses",
        )
        self._render(animate=False)

    def _set_view(self, view_mode: str) -> None:
        if view_mode == self.view_mode:
            return
        order = {"Month": 0, "Week": 1, "Day": 2}
        direction = 1 if order[view_mode] > order[self.view_mode] else -1
        self.view_mode = view_mode
        self._render(direction=direction)

    def _open_day(self, selected_day: date) -> None:
        self.anchor = selected_day
        self.view_mode = "Day"
        for button in self.view_buttons.buttons():
            button.setChecked(button.text() == "Day")
        self._render(direction=1)

    def _drill_into_date(self, selected_day: date) -> None:
        if self.view_mode == "Month":
            target_view = "Week"
        elif self.view_mode == "Week":
            target_view = "Day"
        else:
            return
        self.anchor = selected_day
        self.view_mode = target_view
        for button in self.view_buttons.buttons():
            button.setChecked(button.text() == target_view)
        self._render(direction=1)

    def _navigate(self, direction: int) -> None:
        if self.view_mode == "Month":
            month = self.anchor.month - 1 + direction
            year = self.anchor.year + month // 12
            month = month % 12 + 1
            self.anchor = date(year, month, 1)
        elif self.view_mode == "Week":
            self.anchor += timedelta(days=7 * direction)
        else:
            self.anchor += timedelta(days=direction)
        self._render(direction=direction)

    def _render(self, direction: int = 0, *, animate: bool = True) -> None:
        self.range_label.setText(_range_title(self.view_mode, self.anchor))
        self.surface.set_content(self.filtered_records, self.view_mode, self.anchor, self.theme)

    def _show_details(self, record: SessionRecord) -> None:
        self.surface.set_selected_record(record)
        self.detail_title.setText(record.started_at.strftime("%d %b %Y  ·  %H:%M"))
        self.detail_id.setText(record.session_id)
        self.detail_subtitle.setText(
            f"Started {record.started_at:%H:%M:%S}  ·  Last data {_format_time(record.ended_at)}"
        )
        color = STATUS_COLORS.get(record.status, STATUS_COLORS["Unknown"])
        self.status_badge.setText(f"●  {record.status.upper()}")
        self.status_badge.setStyleSheet(
            f"background: transparent; color: {color}; border: 0; border-radius: 0; "
            "font: 700 11px 'Segoe UI'; letter-spacing: 1px; padding: 0;"
        )
        self.stat_values["Duration"].setText(_duration_text(record.started_at, record.ended_at))
        self.stat_secondary["Duration"].setText(f"{record.started_at:%H:%M} → {_format_time(record.ended_at)[:5]}")
        self.stat_values["Progress"].setText(record.progress_text.replace("/", " / "))
        progress_percent = round(record.completed_count * 100 / record.planned_count) if record.planned_count else 0
        self.stat_secondary["Progress"].setText(f"{progress_percent}% completed" if record.planned_count else "No plan recorded")
        self.stat_values["Devices"].setText(str(len(record.devices)))
        self.stat_secondary["Devices"].setText("measured devices")
        self.stat_values["Data"].setText(_format_size(record.size_bytes))
        self.stat_secondary["Data"].setText(f"{record.file_count} files")

        self.category_table.setRowCount(len(record.categories))
        for row, category in enumerate(record.categories):
            values = (category.name.upper(), str(category.file_count), str(category.device_count), _format_size(category.size_bytes))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.category_table.setItem(row, column, item)
        self._populate_workspace_actions(record)
        self.category_table.resizeRowsToContents()

        self.device_list.clear()
        if record.devices:
            self.device_list.addItems(record.devices)
        else:
            self.device_list.addItem("No device names recorded")
        self.path_label.setText(str(record.path))
        if record.warnings:
            self.detail_warning.setText("Warnings\n" + "\n".join(record.warnings[:8]))
            self.detail_warning.setStyleSheet("color: #d95656;")
            self.detail_warning.setVisible(True)
        else:
            self.detail_warning.setVisible(False)

    def _populate_workspace_actions(self, record: SessionRecord) -> None:
        for button in self.workspace_buttons:
            button.deleteLater()
        self.workspace_buttons.clear()

        routes = available_routes(record)
        for row, category in enumerate(record.categories):
            category_routes = [route for route in routes if route[2].name.casefold() == category.name.casefold()]
            if not category_routes:
                item = QTableWidgetItem("—")
                item.setTextAlignment(Qt.AlignCenter)
                self.category_table.setItem(row, 4, item)
                continue

            cell = QWidget()
            cell.setStyleSheet("background: transparent;")
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(5, 3, 5, 3)
            button = QPushButton()
            button.setObjectName("SessionTableAction")
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(30)
            if len(category_routes) == 1:
                _label, workspace_key, source_path = category_routes[0]
                action_names = {
                    "iv": "K2450-IV  →",
                    "image": "Images  →",
                    "hp6614c_transfer": "HP6614C  →",
                    "data_preview": f"Preview {category.name.upper()}  →",
                }
                button.setText(action_names.get(workspace_key, f"Open  →"))
                button.setToolTip(f"Create workspace and load:\n{source_path}")
                button.setProperty("workspace_key", workspace_key)
                button.clicked.connect(
                    lambda checked=False, key=workspace_key, path=source_path: self.open_session_requested.emit(key, str(path))
                )
            else:
                button.setText(f"Choose {len(category_routes)}  ▾")
                menu = QMenu(button)
                for label, workspace_key, source_path in category_routes:
                    action = menu.addAction(label)
                    action.triggered.connect(
                        lambda checked=False, key=workspace_key, path=source_path: self.open_session_requested.emit(key, str(path))
                    )
                button.setMenu(menu)
            self.workspace_buttons.append(button)
            cell_layout.addWidget(button)
            self.category_table.setCellWidget(row, 4, cell)

    def _open_session(self, record: SessionRecord) -> None:
        routes = available_routes(record)
        if not routes:
            QMessageBox.information(self, "No compatible workspace", "This session has no data category supported by a workspace.")
            return
        if len(routes) == 1:
            _label, workspace_key, source_path = routes[0]
            self.open_session_requested.emit(workspace_key, str(source_path))
            return
        menu = QMenu(self)
        for label, workspace_key, source_path in routes:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda checked=False, key=workspace_key, path=source_path: self.open_session_requested.emit(key, str(path))
            )
        menu.exec(self.mapToGlobal(self.rect().center()))

    def _on_progress(self, value: int, message: str) -> None:
        self.progress_changed.emit(value)
        self.status_changed.emit(message)

    def _refresh_finished(self, records: object, warnings: object) -> None:
        self.records = list(records) if isinstance(records, list) else []
        warning_items = list(warnings) if isinstance(warnings, list) else []
        self._rebuild_filters()
        self._apply_filters()
        self.refresh_button.setEnabled(True)
        self.progress_changed.emit(100)
        self.status_changed.emit(f"Indexed {len(self.records)} test sessions")
        if warning_items:
            self.warning_label.setText(f"Indexed with {len(warning_items)} warning(s). Select affected sessions for details.")
            self.warning_label.setVisible(True)

    def _refresh_failed(self, message: str) -> None:
        self.refresh_button.setEnabled(True)
        self.progress_changed.emit(0)
        self.status_changed.emit("Session refresh failed")
        self.warning_label.setText(message)
        self.warning_label.setVisible(True)

    def _clear_worker(self) -> None:
        self._thread = None
        self._worker = None


def available_routes(record: SessionRecord) -> list[tuple[str, str, Path]]:
    summaries = {item.name: item for item in record.categories}
    routes: list[tuple[str, str, Path]] = []
    if summaries.get("iv") and summaries["iv"].csv_count:
        routes.append(("K2450-IV", "iv", record.path / "iv"))
    if summaries.get("b1500") and summaries["b1500"].csv_count:
        b1500 = record.path / "b1500"
        if any(b1500.rglob("*_transfer_ALL_wide.csv")):
            routes.append(("B1500 Transfer", "b1500_transfer", b1500))
        if any(b1500.rglob("*_output_ALL_wide.csv")):
            routes.append(("B1500 Output", "b1500_output", b1500))
    if summaries.get("hp6614c") and summaries["hp6614c"].csv_count:
        routes.append(("HP6614C Transfer", "hp6614c_transfer", record.path / "hp6614c"))
    if summaries.get("images") and summaries["images"].image_count:
        routes.append(("Image Analysis", "image", record.path / "images"))
    dedicated = {"iv", "b1500", "hp6614c", "images"}
    for category in sorted(name for name, item in summaries.items() if item.csv_count and name not in dedicated):
        routes.append((f"Data Preview · {category}", "data_preview", record.path / category))
    return routes


def _range_title(view_mode: str, anchor: date) -> str:
    if view_mode == "Month":
        return anchor.strftime("%B %Y")
    if view_mode == "Week":
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=6)
        return f"{start:%b %d} – {end:%b %d, %Y}"
    return anchor.strftime("%A, %B %d, %Y")


def _visual_duration_minutes(record: SessionRecord, next_record: SessionRecord | None = None) -> float:
    if record.ended_at is None or record.ended_at <= record.started_at:
        return 0.0
    visual_end = record.ended_at
    if next_record is not None and record.started_at < next_record.started_at < visual_end:
        visual_end = next_record.started_at
    day_end = datetime.combine(record.started_at.date(), time(23, 59, 59))
    visual_end = min(visual_end, day_end)
    return max(0.0, (visual_end - record.started_at).total_seconds() / 60.0)


def _format_time(value: datetime | None) -> str:
    return value.strftime("%H:%M:%S") if value else "—"


def _duration_text(start: datetime, end: datetime | None) -> str:
    if end is None or end <= start:
        return "< 1 min"
    seconds = int((end - start).total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _event_tooltip(record: SessionRecord) -> str:
    categories = ", ".join(item.name for item in record.categories) or "No data"
    return (
        f"{record.session_id}\n{record.status} · {record.progress_text}\n"
        f"{record.file_count} files · {len(record.devices)} devices\n{categories}"
    )
