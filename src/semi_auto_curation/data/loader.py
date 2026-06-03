from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from semi_auto_curation.models import DeviceMeasurement, DeviceMetadata, IVPoint


def discover_iv_files(source_dir: Path) -> list[Path]:
    return sorted(source_dir.glob("*_iv.csv"))


def load_measurement(csv_path: Path) -> DeviceMeasurement:
    json_path = csv_path.with_suffix(".json")
    metadata = _load_metadata(json_path) if json_path.exists() else _fallback_metadata(csv_path)
    points: list[IVPoint] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            points.append(
                IVPoint(
                    index=int(float(row.get("index", 0) or 0)),
                    elapsed_s=float(row.get("elapsed_s", 0) or 0),
                    source_value=float(row.get("source_value", 0) or 0),
                    voltage_v=float(row.get("voltage_v", 0) or 0),
                    current_a=float(row.get("current_a", 0) or 0),
                    resistance_ohm=float(row.get("resistance_ohm", 0) or 0),
                )
            )
    if not points:
        raise ValueError(f"No IV points found in {csv_path}")
    return DeviceMeasurement(
        csv_path=csv_path,
        json_path=json_path if json_path.exists() else None,
        metadata=metadata,
        points=points,
    )


def _load_metadata(json_path: Path) -> DeviceMetadata:
    with json_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    device = payload.get("device", {})
    stage = payload.get("coordinates", {}).get("stage_um", {})
    measurement = payload.get("measurement", {})
    device_name = device.get("name") or json_path.stem.replace("_iv", "")
    row, col = _normalized_row_col(device_name, _to_optional_int(device.get("row")), _to_optional_int(device.get("col")))
    return DeviceMetadata(
        device_name=device_name,
        row=row,
        col=col,
        order=_to_optional_int(device.get("order")),
        created_at=payload.get("created_at"),
        stage_x_um=_to_optional_float(stage.get("x")),
        stage_y_um=_to_optional_float(stage.get("y")),
        start_v=_to_optional_float(measurement.get("start")),
        stop_v=_to_optional_float(measurement.get("stop")),
        step_v=_to_optional_float(measurement.get("step")),
        current_limit_a=_to_optional_float(measurement.get("current_limit_a")),
        extra=payload,
    )


def _fallback_metadata(csv_path: Path) -> DeviceMetadata:
    device_name = csv_path.stem.replace("_iv", "")
    row, col = _normalized_row_col(device_name, None, None)
    return DeviceMetadata(device_name=device_name, row=row, col=col)


def _to_optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _to_optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _normalized_row_col(device_name: str, row: int | None, col: int | None) -> tuple[int | None, int | None]:
    match = re.search(r"([A-Z]+)(\d+)$", device_name)
    if not match:
        return row, col
    letter_index = _letters_to_index(match.group(1))
    number_index = int(match.group(2)) - 1
    if row is None or col is None:
        return letter_index, number_index
    if row == number_index and col == letter_index:
        return letter_index, number_index
    return row, col


def _letters_to_index(value: str) -> int:
    index = 0
    for char in value.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1
