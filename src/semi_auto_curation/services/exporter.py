from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from semi_auto_curation.models import IVBatchResult


def export_iv_batch(batch: IVBatchResult) -> list[str]:
    out_dir = batch.settings.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_csv = out_dir / "iv_fit_summary.csv"
    detail_json = out_dir / "iv_fit_detail.json"
    _write_summary_csv(summary_csv, batch)
    _write_detail_json(detail_json, batch)
    return [str(summary_csv), str(detail_json)]


def _write_summary_csv(path: Path, batch: IVBatchResult) -> None:
    fieldnames = [
        "device_name",
        "row",
        "col",
        "fit_point_count",
        "fit_voltage_min",
        "fit_voltage_max",
        "fit_slope_a_per_v",
        "fit_intercept_a",
        "fit_r2",
        "fit_resistance_ohm",
        "abs_fit_resistance_ohm",
        "is_dummy",
        "dummy_reason",
        "csv_path",
        "json_path",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for device in batch.devices:
            writer.writerow(
                {
                    "device_name": device.device_name,
                    "row": device.metadata.row,
                    "col": device.metadata.col,
                    "fit_point_count": device.fit_point_count,
                    "fit_voltage_min": device.fit_voltage_min,
                    "fit_voltage_max": device.fit_voltage_max,
                    "fit_slope_a_per_v": device.fit_slope_a_per_v,
                    "fit_intercept_a": device.fit_intercept_a,
                    "fit_r2": device.fit_r2,
                    "fit_resistance_ohm": device.fit_resistance_ohm,
                    "abs_fit_resistance_ohm": device.abs_fit_resistance_ohm,
                    "is_dummy": device.is_dummy,
                    "dummy_reason": device.dummy_reason,
                    "csv_path": device.csv_path,
                    "json_path": device.json_path,
                }
            )


def _write_detail_json(path: Path, batch: IVBatchResult) -> None:
    payload = {
        "settings": {
            "source_dir": str(batch.settings.source_dir),
            "output_dir": str(batch.settings.output_dir),
            "fit_voltage_min": batch.settings.fit_voltage_min,
            "fit_voltage_max": batch.settings.fit_voltage_max,
            "dummy_min_resistance_ohm": batch.settings.dummy_min_resistance_ohm,
            "dummy_max_resistance_ohm": batch.settings.dummy_max_resistance_ohm,
            "dummy_min_r2": batch.settings.dummy_min_r2,
            "dummy_min_fit_points": batch.settings.dummy_min_fit_points,
            "heatmap_metric": batch.settings.heatmap_metric,
        },
        "summary": asdict(batch.summary),
        "devices": [asdict(device) for device in batch.devices],
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
