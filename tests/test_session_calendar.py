from __future__ import annotations

import gc
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QGraphicsTextItem

from semi_auto_curation.services.session_index import (
    CategorySummary,
    SessionRecord,
    filter_sessions,
    load_index,
    parse_session_timestamp,
    refresh_index,
    scan_session,
)
from semi_auto_curation.ui.test_calendar_panel import (
    SessionEventItem,
    TestCalendarPanel,
    _range_title,
    _visual_duration_minutes,
    available_routes,
)


class SessionIndexTests(unittest.TestCase):
    def test_parses_timestamp_and_rejects_unrelated_directories(self) -> None:
        self.assertEqual(parse_session_timestamp("20260924_185258"), datetime(2026, 9, 24, 18, 52, 58))
        self.assertIsNone(parse_session_timestamp("notes"))
        self.assertIsNone(parse_session_timestamp("20261399_999999"))

    def test_scans_categories_devices_progress_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / "20260924_185258"
            (session / "iv").mkdir(parents=True)
            (session / "images").mkdir()
            (session / "autotest_resume.json").write_text(
                json.dumps(
                    {
                        "points": [
                            {"name": "DevA1", "order": 1},
                            {"name": "DevA2", "order": 2},
                        ],
                        "completed": [1],
                    }
                ),
                encoding="utf-8",
            )
            (session / "iv" / "DevA1_iv.csv").write_text("v,i\n0,1\n", encoding="utf-8")
            (session / "iv" / "DevA1_iv.json").write_text(
                json.dumps({"device": {"name": "DevA1"}, "status": "failed"}), encoding="utf-8"
            )
            (session / "images" / "DevA1.png").write_bytes(b"image")

            record = scan_session(session)

            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.status, "Failed")
            self.assertEqual((record.completed_count, record.planned_count), (1, 2))
            self.assertEqual(record.devices, ("DevA1", "DevA2"))
            self.assertEqual({item.name for item in record.categories}, {"images", "iv"})
            self.assertEqual(next(item for item in record.categories if item.name == "iv").failed_count, 1)

    def test_empty_and_malformed_sessions_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            empty = Path(temporary) / "20260924_120000"
            empty.mkdir()
            self.assertEqual(scan_session(empty).status, "Unknown")
            broken = Path(temporary) / "20260924_130000"
            broken.mkdir()
            (broken / "autotest_resume.json").write_text("{", encoding="utf-8")
            record = scan_session(broken)
            self.assertEqual(record.status, "Unknown")
            self.assertTrue(record.warnings)

    def test_refresh_is_manual_and_removes_deleted_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "sessions"
            root.mkdir()
            database = Path(temporary) / "index.sqlite3"
            first = root / "20260924_120000"
            first.mkdir()
            refresh_index(root, database)
            second = root / "20260924_130000"
            second.mkdir()
            self.assertEqual([item.session_id for item in load_index(database)], [first.name])
            refresh_index(root, database)
            self.assertEqual(len(load_index(database)), 2)
            first.rmdir()
            refresh_index(root, database)
            self.assertEqual([item.session_id for item in load_index(database)], [second.name])

    def test_filter_searches_metadata_and_device_names(self) -> None:
        records = [
            _record("20260924_120000", "Complete", "iv", ("DevA1",)),
            _record("20260925_120000", "Stopped", "it", ("SensorB2",)),
        ]
        self.assertEqual([item.session_id for item in filter_sessions(records, query="sensorb2")], [records[1].session_id])
        self.assertEqual([item.session_id for item in filter_sessions(records, category="iv")], [records[0].session_id])
        self.assertEqual([item.session_id for item in filter_sessions(records, status="Stopped")], [records[1].session_id])


class SessionCalendarUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_view_switches_without_refreshing_disk(self) -> None:
        panel = TestCalendarPanel()
        panel._set_view("Week")
        self.assertEqual(panel.view_mode, "Week")
        panel._set_view("Day")
        self.assertEqual(panel.view_mode, "Day")
        panel._open_day(date(2026, 9, 24))
        self.assertEqual(panel.anchor, date(2026, 9, 24))
        self.assertIn(str(date.today().year), _range_title("Day", date.today()))
        panel.close()

    def test_double_click_drills_from_month_to_week_then_day(self) -> None:
        panel = TestCalendarPanel()
        selected = date(2026, 9, 24)
        panel.view_mode = "Month"

        panel._drill_into_date(selected)

        self.assertEqual(panel.view_mode, "Week")
        self.assertEqual(panel.anchor, selected)
        self.assertTrue(next(button for button in panel.view_buttons.buttons() if button.text() == "Week").isChecked())

        panel._drill_into_date(selected)

        self.assertEqual(panel.view_mode, "Day")
        self.assertTrue(next(button for button in panel.view_buttons.buttons() if button.text() == "Day").isChecked())
        panel.close()

    def test_double_clicking_slot_only_selects_it(self) -> None:
        panel = TestCalendarPanel()
        record = _record("20260924_120000", "Complete", "iv", ("DevA1",))
        record = replace(record, ended_at=record.started_at.replace(hour=13))
        opened: list[tuple[str, str]] = []
        selected: list[SessionRecord] = []
        panel.open_session_requested.connect(lambda key, path: opened.append((key, path)))
        panel.surface.session_clicked.connect(selected.append)
        panel.resize(1200, 760)
        panel.show()
        panel.surface.set_content([record], "Week", record.started_at.date(), "dark")
        self.app.processEvents()
        card = next(item for item in panel.surface.scene().items() if isinstance(item, SessionEventItem))
        point = panel.surface.mapFromScene(card.sceneBoundingRect().center())

        QTest.mouseDClick(panel.surface.viewport(), Qt.LeftButton, Qt.NoModifier, point)
        self.app.processEvents()

        self.assertEqual(opened, [])
        self.assertEqual(selected, [record])
        self.assertEqual(panel.surface.selected_session_id, record.session_id)
        del card
        panel.close()

    def test_week_date_drill_hit_area_is_header_only(self) -> None:
        panel = TestCalendarPanel()
        panel.resize(1200, 760)
        panel.show()
        selected = date(2026, 9, 24)
        panel.surface.set_content([], "Week", selected, "dark")
        self.app.processEvents()
        scene = panel.surface.sceneRect()
        time_width = 54.0
        day_width = (scene.width() - time_width) / 7.0
        thursday_x = time_width + day_width * 3.5
        header_point = panel.surface.mapFromScene(QPointF(thursday_x, 18.0))
        body_point = panel.surface.mapFromScene(QPointF(thursday_x, 180.0))
        drilled: list[date] = []
        panel.surface.date_double_clicked.connect(drilled.append)

        self.assertEqual(panel.surface._date_at_position(header_point), selected)
        self.assertIsNone(panel.surface._date_at_position(body_point))
        QTest.mouseDClick(panel.surface.viewport(), Qt.LeftButton, Qt.NoModifier, body_point)
        self.app.processEvents()
        self.assertEqual(drilled, [])
        QTest.mouseDClick(panel.surface.viewport(), Qt.LeftButton, Qt.NoModifier, header_point)
        self.app.processEvents()
        self.assertEqual(drilled, [selected])
        panel.close()

    def test_week_wheel_scrolls_timeline_explicitly(self) -> None:
        panel = TestCalendarPanel()
        panel.view_mode = "Week"
        panel.resize(1200, 760)
        panel.show()
        panel._render(animate=False)
        self.app.processEvents()
        scroll_bar = panel.surface.verticalScrollBar()
        scroll_bar.setValue(0)
        local = QPointF(panel.surface.viewport().rect().center())
        event = QWheelEvent(
            local,
            QPointF(panel.surface.viewport().mapToGlobal(local.toPoint())),
            QPoint(),
            QPoint(0, -120),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.ScrollUpdate,
            False,
        )

        QApplication.sendEvent(panel.surface.viewport(), event)

        self.assertTrue(event.isAccepted())
        self.assertGreater(scroll_bar.value(), 0)
        panel.close()

    def test_single_route_emits_workspace_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = _record("20260924_120000", "Complete", "iv", ("DevA1",), Path(temporary))
            (record.path / "iv").mkdir(parents=True)
            routes = available_routes(record)
            self.assertEqual([(item[1], item[2].name) for item in routes], [("iv", "iv")])
            panel = TestCalendarPanel()
            emitted: list[tuple[str, str]] = []
            panel.open_session_requested.connect(lambda key, path: emitted.append((key, path)))
            panel._open_session(record)
            self.assertEqual(emitted[0][0], "iv")
            panel.close()

    def test_detail_workspace_action_creates_and_loads_known_format(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = _record("20260924_120000", "Complete", "it", ("DevA1",), Path(temporary))
            (record.path / "it").mkdir(parents=True)
            panel = TestCalendarPanel()
            emitted: list[tuple[str, str]] = []
            panel.open_session_requested.connect(lambda key, path: emitted.append((key, path)))

            panel._show_details(record)

            self.assertEqual(panel.category_table.columnCount(), 5)
            self.assertEqual(len(panel.workspace_buttons), 1)
            self.assertIn("Preview IT", panel.workspace_buttons[0].text())
            self.assertIsNotNone(panel.category_table.cellWidget(0, 4))
            panel.workspace_buttons[0].click()
            self.assertEqual(emitted, [("data_preview", str(record.path / "it"))])
            self.assertGreaterEqual(panel.detail_panel.minimumWidth(), 420)
            panel.close()

    def test_event_label_is_positioned_inside_its_card(self) -> None:
        panel = TestCalendarPanel()
        record = _record("20260924_120000", "Complete", "iv", ("DevA1",))
        record = replace(record, ended_at=record.started_at.replace(hour=13))
        panel.surface.resize(900, 700)
        panel.surface.set_content([record], "Day", record.started_at.date(), "dark")
        card = next(item for item in panel.surface.scene().items() if item.data(0) is record)
        label = next(
            item
            for item in panel.surface.scene().items()
            if isinstance(item, QGraphicsTextItem) and item.parentItem() is card
        )
        self.assertIn(record.session_id, label.toPlainText())
        self.assertGreaterEqual(label.sceneBoundingRect().left(), card.sceneBoundingRect().left())
        self.assertGreaterEqual(label.sceneBoundingRect().top(), card.sceneBoundingRect().top())
        del label
        del card
        panel.close()

    def test_scene_retains_event_items_after_temporary_wrapper_is_released(self) -> None:
        panel = TestCalendarPanel()
        record = _record("20260924_120000", "Complete", "iv", ("DevA1",))
        panel.surface.set_content([record], "Day", record.started_at.date(), "dark")
        items = panel.surface.scene().items()
        card = next(item for item in items if item.data(0) is record)

        del items
        del card
        gc.collect()

        self.assertTrue(any(item.data(0) is record for item in panel.surface.scene().items()))
        panel.close()

    def test_event_hover_and_selection_use_distinct_edge_states(self) -> None:
        panel = TestCalendarPanel()
        record = _record("20260924_120000", "Complete", "iv", ("DevA1",))
        panel.surface.set_content([record], "Day", record.started_at.date(), "dark")
        card = next(item for item in panel.surface.scene().items() if isinstance(item, SessionEventItem))
        normal_width = card.pen().widthF()

        card.set_hovered(True)
        hover_width = card.pen().widthF()
        panel.surface.set_selected_record(record)
        selected_width = card.pen().widthF()
        card.set_hovered(False)

        self.assertGreater(hover_width, normal_width)
        self.assertGreater(selected_width, hover_width)
        self.assertEqual(card.pen().widthF(), selected_width)
        self.assertEqual(panel.surface.selected_session_id, record.session_id)
        del card
        panel.close()

    def test_visual_duration_is_clamped_to_the_next_session(self) -> None:
        first = _record("20260924_120000", "Complete", "iv", ("DevA1",))
        second = _record("20260924_123000", "Stopped", "iv", ("DevA2",))
        first = replace(first, ended_at=first.started_at.replace(hour=14))
        self.assertEqual(_visual_duration_minutes(first, second), 30.0)

    def test_selecting_slot_keeps_calendar_items_and_populates_details(self) -> None:
        panel = TestCalendarPanel()
        record = _record("20260924_120000", "Complete", "iv", ("DevA1",))
        record = replace(record, ended_at=record.started_at.replace(hour=13))
        panel.surface.set_content([record], "Day", record.started_at.date(), "dark")
        before = len(panel.surface.scene().items())

        panel._show_details(record)
        self.app.processEvents()

        target_items = [item for item in panel.surface.scene().items() if item.data(0) is record]
        self.assertEqual(len(panel.surface.scene().items()), before)
        self.assertEqual(len(target_items), 1)
        self.assertTrue(target_items[0].isVisible())
        self.assertEqual(panel.detail_id.text(), record.session_id)
        self.assertIn("24 Sep 2026", panel.detail_title.text())
        self.assertEqual(panel.category_table.rowCount(), 1)
        del target_items
        panel.close()


def _record(
    session_id: str,
    status: str,
    category: str,
    devices: tuple[str, ...],
    root: Path | None = None,
) -> SessionRecord:
    path = (root or Path("C:/sessions")) / session_id
    return SessionRecord(
        session_id=session_id,
        path=path,
        started_at=parse_session_timestamp(session_id),
        ended_at=None,
        status=status,
        planned_count=1,
        completed_count=1 if status == "Complete" else 0,
        file_count=1,
        size_bytes=10,
        devices=devices,
        categories=(CategorySummary(category, file_count=1, csv_count=1, device_count=len(devices)),),
    )


if __name__ == "__main__":
    unittest.main()
