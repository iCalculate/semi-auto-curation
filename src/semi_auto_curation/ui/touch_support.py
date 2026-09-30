from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtWidgets import QAbstractScrollArea, QScroller, QSplitter, QWidget


class TouchSwipeNavigator(QObject):
    """Recognize one-finger horizontal swipes on a widget."""

    def __init__(
        self,
        target: QWidget,
        on_swipe_left: Callable[[], None],
        on_swipe_right: Callable[[], None],
        *,
        threshold_px: int = 120,
        vertical_tolerance_px: int = 90,
    ) -> None:
        super().__init__(target)
        self._target = target
        self._on_swipe_left = on_swipe_left
        self._on_swipe_right = on_swipe_right
        self._threshold_px = threshold_px
        self._vertical_tolerance_px = vertical_tolerance_px
        self._start_pos: QPointF | None = None
        self._last_pos: QPointF | None = None
        self._mouse_drag_active = False

        target.setAttribute(Qt.WA_AcceptTouchEvents, True)
        target.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if obj is not self._target:
            return super().eventFilter(obj, event)

        event_type = event.type()
        if event_type == QEvent.Type.TouchBegin:
            pos = _touch_position(event)
            if pos is None:
                return False
            self._start_pos = pos
            self._last_pos = pos
            return False

        if event_type == QEvent.Type.TouchUpdate:
            pos = _touch_position(event)
            if pos is not None:
                self._last_pos = pos
            return False

        if event_type in {QEvent.Type.TouchEnd, QEvent.Type.TouchCancel}:
            handled = self._finish_swipe()
            self._start_pos = None
            self._last_pos = None
            return handled

        if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._start_mouse_drag(_event_position(event))
            return False

        if event_type == QEvent.Type.MouseMove and self._mouse_drag_active:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._last_pos = _event_position(event)
            return False

        if event_type == QEvent.Type.MouseButtonRelease and self._mouse_drag_active:
            handled = self._finish_swipe()
            self._clear_drag()
            return handled

        return super().eventFilter(obj, event)

    def _start_mouse_drag(self, pos: QPointF) -> None:
        self._mouse_drag_active = True
        self._start_pos = pos
        self._last_pos = pos

    def _clear_drag(self) -> None:
        self._mouse_drag_active = False
        self._start_pos = None
        self._last_pos = None

    def _finish_swipe(self) -> bool:
        if self._start_pos is None or self._last_pos is None:
            return False
        delta = self._last_pos - self._start_pos
        dx = float(delta.x())
        dy = float(delta.y())
        if abs(dx) < self._threshold_px:
            return False
        if abs(dy) > self._vertical_tolerance_px:
            return False
        if abs(dx) < abs(dy) * 1.6:
            return False
        if dx < 0:
            self._on_swipe_left()
        else:
            self._on_swipe_right()
        return True


def enable_touch_scrolling(root: QWidget) -> None:
    """Enable touch-friendly scrolling and splitter dragging below root."""
    _enable_scroll_area(root)
    if isinstance(root, QSplitter):
        _enable_splitter_dragging(root)
    for scroll_area in root.findChildren(QAbstractScrollArea):
        _enable_scroll_area(scroll_area)
    for splitter in root.findChildren(QSplitter):
        _enable_splitter_dragging(splitter)


def configure_touch_application() -> None:
    """Keep basic widgets clickable on touch devices."""
    QApplication = _application_class()
    QApplication.setAttribute(Qt.AA_SynthesizeMouseForUnhandledTouchEvents, True)


def _enable_scroll_area(scroll_area: QAbstractScrollArea | QWidget) -> None:
    widget = scroll_area
    preserve_mouse_clicks = bool(scroll_area.property("preserveMouseClicks"))
    if isinstance(scroll_area, QAbstractScrollArea):
        widget = scroll_area.viewport()
        scroll_area.setAttribute(Qt.WA_AcceptTouchEvents, True)
    widget.setAttribute(Qt.WA_AcceptTouchEvents, True)
    if preserve_mouse_clicks:
        QScroller.ungrabGesture(widget)
        return
    QScroller.grabGesture(widget, QScroller.ScrollerGestureType.TouchGesture)
    QScroller.grabGesture(widget, QScroller.ScrollerGestureType.LeftMouseButtonGesture)


def _enable_splitter_dragging(splitter: QSplitter) -> None:
    splitter.setAttribute(Qt.WA_AcceptTouchEvents, True)
    splitter.setHandleWidth(max(splitter.handleWidth(), 14))
    for index in range(1, splitter.count()):
        splitter.handle(index).setAttribute(Qt.WA_AcceptTouchEvents, True)


def _touch_position(event) -> QPointF | None:
    point_count = getattr(event, "pointCount", lambda: 0)()
    if point_count <= 0:
        return None
    point = event.point(0)
    if hasattr(point, "position"):
        return point.position()
    if hasattr(point, "pos"):
        return QPointF(point.pos())
    return None


def _event_position(event) -> QPointF:
    if hasattr(event, "position"):
        return event.position()
    return QPointF(event.pos())


def _application_class():
    from PySide6.QtWidgets import QApplication

    return QApplication
