from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from semi_auto_curation.data.preview_loader import (
    PreviewDataset,
    PreviewDevice,
    feature_value,
    heatmap_values,
    load_preview_dataset,
)
from semi_auto_curation.ui.data_preview_panel import DataPreviewPanel


class PreviewLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_preview_defaults_side_by_side_and_can_stack(self) -> None:
        panel = DataPreviewPanel()
        try:
            self.assertEqual(panel.plots_splitter.orientation(), Qt.Horizontal)
            panel.plot_layout_combo.setCurrentText("Stacked")
            self.assertEqual(panel.plots_splitter.orientation(), Qt.Vertical)
        finally:
            panel.close()

    def test_information_panel_lists_every_selected_device(self) -> None:
        panel = DataPreviewPanel()
        try:
            dataset = PreviewDataset(
                devices={
                    "DevA1": PreviewDevice("DevA1", 0, 0, gds_u=10.5, gds_v=-2.0, stage_x_um=100.0, stage_y_um=200.0),
                    "DevB2": PreviewDevice("DevB2", 1, 1, gds_u=20.5, gds_v=-3.0, stage_x_um=110.0, stage_y_um=210.0),
                },
                channels=[],
                curve_channels=[],
            )
            panel.dataset = dataset
            panel.device_information.show_devices(dataset, ["DevA1", "DevB2"])
            self.assertEqual(panel.device_information.table.rowCount(), 2)
            self.assertEqual(panel.device_information.table.item(0, 3).text(), "10.5")
            self.assertEqual(panel.device_information.table.item(1, 6).text(), "210")
        finally:
            panel.close()

    def test_older_preview_load_cannot_replace_the_latest_dataset(self) -> None:
        panel = DataPreviewPanel()
        try:
            iv_dataset = PreviewDataset(
                devices={"DevA1": PreviewDevice("DevA1", 0, 0, records=[{"voltage_v": 1.0, "current_a": 2.0}])},
                channels=["current_a", "voltage_v"],
                curve_channels=["current_a", "voltage_v"],
                suggested_x="voltage_v",
                suggested_y="current_a",
            )
            it_dataset = PreviewDataset(
                devices={"DevA1": PreviewDevice("DevA1", 0, 0, records=[{"elapsed_s": 1.0, "current_a": 3.0}])},
                channels=["current_a", "elapsed_s"],
                curve_channels=["current_a", "elapsed_s"],
                suggested_x="elapsed_s",
                suggested_y="current_a",
            )

            panel._load_generation = 2
            panel._loaded(2, it_dataset)
            panel._loaded(1, iv_dataset)

            self.assertIs(panel.dataset, it_dataset)
            self.assertEqual(panel.curve_plot.dataset.suggested_x, "elapsed_s")
            self.assertEqual(panel.x_combo.currentText(), "elapsed_s")
        finally:
            panel.close()

    def test_loads_device_sidecars_and_extracts_lightweight_features(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, row, col, values in (
                ("DevA1", 0, 0, [1.0, 3.0, -2.0]),
                ("DevB2", 1, 1, [2.0, 4.0, 8.0]),
            ):
                path = root / f"{name}_custom.csv"
                self._write_csv(path, [
                    {"time_s": index, "channel_a": value, "channel_b": value * 10}
                    for index, value in enumerate(values)
                ])
                path.with_suffix(".json").write_text(json.dumps({
                    "device": {"name": name, "row": row, "col": col},
                    "coordinates": {
                        "gds": {"u": 12.5 + row, "v": -4.25 - col},
                        "stage_um": {"x": 100.0 + row, "y": 200.0 + col},
                    },
                    "analysis": {"quality": 0.9 + row * 0.01},
                }), encoding="utf-8")
            images = root / "images"
            images.mkdir()
            pixmap = QPixmap(8, 8)
            pixmap.fill(QColor("red"))
            self.assertTrue(pixmap.save(str(images / "DevA1.png")))

            dataset = load_preview_dataset(root)

        self.assertEqual(dataset.positions, {"DevA1": (0, 0), "DevB2": (1, 1)})
        self.assertIn("channel_a", dataset.channels)
        self.assertIn("meta.analysis.quality", dataset.channels)
        self.assertEqual(dataset.devices["DevA1"].gds_u, 12.5)
        self.assertEqual(dataset.devices["DevB2"].stage_y_um, 201.0)
        self.assertEqual([path.name for path in dataset.devices["DevA1"].image_files], ["DevA1.png"])
        self.assertEqual(dataset.devices["DevB2"].image_files, [])
        self.assertEqual(heatmap_values(dataset, "channel_a", "last"), {"DevA1": -2.0, "DevB2": 8.0})
        self.assertEqual(heatmap_values(dataset, "channel_a", "max_abs"), {"DevA1": 3.0, "DevB2": 8.0})

    def test_hp6614c_transfer_is_discovered_and_derives_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "DevC3_6614c_transfer.csv"
            rows = []
            for index, (gate, current) in enumerate(((0.0, 1e-10), (0.5, 1e-8), (1.0, 1e-5), (1.5, 1e-3)), 1):
                rows.append({
                    "point_name": "DevC3",
                    "test_type": "transfer",
                    "curve_index": 1,
                    "gate_index": index,
                    "gate_voltage_v": gate,
                    "drain_voltage_v": 0.1,
                    "drain_current_a": current,
                })
            self._write_csv(path, rows)
            path.with_suffix(".json").write_text(json.dumps({
                "result_type": "hp6614c_transfer",
                "device": {"name": "DevC3", "row": 2, "col": 2},
            }), encoding="utf-8")

            dataset = load_preview_dataset(root, hp6614c_only=True)

        self.assertEqual(dataset.suggested_x, "gate_voltage_v")
        self.assertEqual(dataset.suggested_y, "drain_current_a")
        self.assertIn("derived.vth_v", dataset.channels)
        self.assertGreater(dataset.devices["DevC3"].numeric["derived.on_off_ratio"][0], 1e6)
        self.assertGreater(dataset.devices["DevC3"].numeric["derived.ss_mv_dec"][0], 0)

    def test_groups_a_combined_csv_by_device_name(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "combined.csv"
            self._write_csv(path, [
                {"device_name": "A1", "row": 0, "col": 0, "x": 0, "signal": 1},
                {"device_name": "A1", "row": 0, "col": 0, "x": 1, "signal": 2},
                {"device_name": "B1", "row": 1, "col": 0, "x": 0, "signal": 5},
            ])
            dataset = load_preview_dataset(Path(folder))

        self.assertEqual(set(dataset.devices), {"A1", "B1"})
        self.assertEqual(dataset.devices["A1"].numeric["signal"], [1.0, 2.0])

    def test_feature_value_ignores_non_finite_values(self) -> None:
        self.assertEqual(feature_value([float("nan"), -4.0, 2.0], "max_abs"), 4.0)
        self.assertIsNone(feature_value([float("nan")], "mean"))

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
