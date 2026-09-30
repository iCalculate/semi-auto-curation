from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QScrollArea, QSplitter, QWidget

from semi_auto_curation.ui.main_window import MainWindow
from semi_auto_curation.ui.touch_support import TouchSwipeNavigator, enable_touch_scrolling


class TouchSupportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_horizontal_swipe_dispatches_directional_callbacks(self) -> None:
        calls: list[str] = []
        target = QWidget()
        navigator = TouchSwipeNavigator(
            target,
            on_swipe_left=lambda: calls.append("left"),
            on_swipe_right=lambda: calls.append("right"),
        )

        navigator._start_pos = QPointF(200, 20)
        navigator._last_pos = QPointF(40, 35)
        self.assertTrue(navigator._finish_swipe())

        navigator._start_pos = QPointF(40, 20)
        navigator._last_pos = QPointF(180, 25)
        self.assertTrue(navigator._finish_swipe())

        self.assertEqual(calls, ["left", "right"])

    def test_vertical_drag_is_not_treated_as_workspace_swipe(self) -> None:
        calls: list[str] = []
        target = QWidget()
        navigator = TouchSwipeNavigator(
            target,
            on_swipe_left=lambda: calls.append("left"),
            on_swipe_right=lambda: calls.append("right"),
        )

        navigator._start_pos = QPointF(20, 20)
        navigator._last_pos = QPointF(150, 180)

        self.assertFalse(navigator._finish_swipe())
        self.assertEqual(calls, [])

    def test_mouse_drag_fallback_dispatches_swipe(self) -> None:
        calls: list[str] = []
        target = QWidget()
        navigator = TouchSwipeNavigator(
            target,
            on_swipe_left=lambda: calls.append("left"),
            on_swipe_right=lambda: calls.append("right"),
        )

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(220, 40),
            QPointF(220, 40),
            QPointF(220, 40),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(40, 45),
            QPointF(40, 45),
            QPointF(40, 45),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(40, 45),
            QPointF(40, 45),
            QPointF(40, 45),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )

        self.assertFalse(navigator.eventFilter(target, press))
        self.assertFalse(navigator.eventFilter(target, move))
        self.assertTrue(navigator.eventFilter(target, release))
        self.assertEqual(calls, ["left"])

    def test_scroll_area_viewport_accepts_touch_events(self) -> None:
        scroll_area = QScrollArea()
        scroll_area.setWidget(QWidget())

        enable_touch_scrolling(scroll_area)

        self.assertTrue(scroll_area.testAttribute(Qt.WA_AcceptTouchEvents))
        self.assertTrue(scroll_area.viewport().testAttribute(Qt.WA_AcceptTouchEvents))

    def test_splitter_handles_are_touch_friendly(self) -> None:
        splitter = QSplitter()
        splitter.addWidget(QWidget())
        splitter.addWidget(QWidget())

        enable_touch_scrolling(splitter)

        self.assertGreaterEqual(splitter.handleWidth(), 14)
        self.assertTrue(splitter.handle(1).testAttribute(Qt.WA_AcceptTouchEvents))

    def test_main_window_switches_workspaces_by_relative_direction(self) -> None:
        window = MainWindow()
        try:
            window._new_workspace("iv")
            first = window.mdi_area.activeSubWindow()
            window._new_workspace("script")
            second = window.mdi_area.activeSubWindow()

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertIsNot(first, second)

            window._activate_previous_workspace()
            self.assertIs(window.mdi_area.activeSubWindow(), first)

            window._activate_next_workspace()
            self.assertIs(window.mdi_area.activeSubWindow(), second)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
