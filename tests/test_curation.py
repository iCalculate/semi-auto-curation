from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from semi_auto_curation.models import DeviceMetadata, IVDeviceAnalysis
from semi_auto_curation.services.curation import filter_iv_devices, export_iv_coordinate_selection


def _device(
    name: str,
    row: int,
    col: int,
    value: float | None,
    dummy: bool = False,
    *,
    u: float | None = None,
    v: float | None = None,
) -> IVDeviceAnalysis:
    extra = {"coordinates": {"gds": {"u": u, "v": v}}} if u is not None and v is not None else {}
    return IVDeviceAnalysis(
        device_name=name,
        csv_path=f"{name}_iv.csv",
        json_path=None,
        metadata=DeviceMetadata(device_name=name, row=row, col=col, extra=extra),
        points=[],
        fit_point_count=3,
        fit_voltage_min=0,
        fit_voltage_max=1,
        fit_slope_a_per_v=None,
        fit_intercept_a=None,
        fit_r2=value,
        fit_resistance_ohm=None,
        abs_fit_resistance_ohm=None,
        max_abs_current_a=0,
        min_abs_current_a=None,
        is_dummy=dummy,
        dummy_reason="",
    )


class CurationTests(unittest.TestCase):
    def test_filter_applies_bounds_and_excludes_dummy(self) -> None:
        devices = [
            _device("A1", 0, 0, 0.8),
            _device("A2", 0, 1, 0.95),
            _device("A3", 0, 2, 0.99, dummy=True),
        ]
        selected = filter_iv_devices(devices, "fit_r2", minimum=0.9)
        self.assertEqual([device.device_name for device in selected], ["A2"])

    def test_export_contains_list_and_named_positions(self) -> None:
        devices = [_device("A2", 0, 1, 0.95, u=123.5, v=-45.25)]
        with TemporaryDirectory() as folder:
            path = Path(folder) / "selection.json"
            export_iv_coordinate_selection(
                path,
                devices,
                metric="fit_r2",
                minimum=0.9,
                maximum=None,
                exclude_dummy=True,
                source_dir="source",
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["coordinate_list"], [[0, 1]])
        self.assertEqual(payload["devices"][0]["array_position"], "A2")
        self.assertEqual(payload["format"], "semi_auto_probe.autotest_point_list")
        self.assertEqual(payload["version"], 1)
        self.assertEqual(
            payload["points"],
            [{"name": "A2", "u": 123.5, "v": -45.25, "row": 0, "col": 1}],
        )

    def test_export_rejects_selected_devices_without_gds_coordinates(self) -> None:
        with TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "missing GDS coordinates.*A2"):
                export_iv_coordinate_selection(
                    Path(folder) / "selection.json",
                    [_device("A2", 0, 1, 0.95)],
                    metric="fit_r2",
                    minimum=0.9,
                    maximum=None,
                    exclude_dummy=True,
                    source_dir="source",
                )

    def test_rejects_inverted_bounds(self) -> None:
        with self.assertRaises(ValueError):
            filter_iv_devices([], "fit_r2", minimum=1.0, maximum=0.5)


if __name__ == "__main__":
    unittest.main()
