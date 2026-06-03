from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from semi_auto_curation.models import B1500Curve, B1500Measurement, DeviceMetadata


_FILE_PATTERNS = {
    "transfer": "*_transfer_ALL_wide.csv",
    "output": "*_output_ALL_wide.csv",
}


def discover_b1500_files(source_dir: Path, measurement_type: str) -> list[Path]:
    pattern = _FILE_PATTERNS[measurement_type]
    return sorted(path for path in source_dir.rglob(pattern) if not path.name.endswith("_Ig.csv"))


def load_b1500_measurement(csv_path: Path, measurement_type: str) -> B1500Measurement:
    leak_csv_path = csv_path.with_name(f"{csv_path.stem}_Ig.csv")
    json_path = _candidate_json_path(csv_path, measurement_type)
    metadata = _load_metadata(json_path, measurement_type) if json_path and json_path.exists() else _fallback_metadata(csv_path, measurement_type)
    curves, sweep_axis_label, bias_axis_label = _load_wide_curves(csv_path, leak_csv_path if leak_csv_path.exists() else None)
    if not curves:
        raise ValueError(f"No B1500 curves found in {csv_path}")
    return B1500Measurement(
        measurement_type=measurement_type,
        csv_path=csv_path,
        leakage_csv_path=leak_csv_path if leak_csv_path.exists() else None,
        json_path=json_path if json_path and json_path.exists() else None,
        metadata=metadata,
        sweep_axis_label=sweep_axis_label,
        bias_axis_label=bias_axis_label,
        curves=curves,
    )


def _candidate_json_path(csv_path: Path, measurement_type: str) -> Path:
    base_name = csv_path.name.replace("_ALL_wide.csv", ".json")
    direct = csv_path.with_name(base_name)
    if direct.exists():
        return direct
    stem = csv_path.stem.replace("_ALL_wide", "")
    return csv_path.with_name(f"{stem}.json")


def _load_wide_curves(csv_path: Path, leak_csv_path: Path | None) -> tuple[list[B1500Curve], str, str]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = [row for row in reader if row]
    leak_rows: list[list[str]] = []
    if leak_csv_path is not None:
        with leak_csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            leak_reader = csv.reader(handle)
            next(leak_reader)
            leak_rows = [row for row in leak_reader if row]
    sweep_axis_label = header[0]
    bias_headers = header[1:]
    bias_axis_label = bias_headers[0].split("=")[0] if bias_headers else "Bias"
    curves: list[B1500Curve] = []
    for index, bias_header in enumerate(bias_headers, start=1):
        sweep_values: list[float] = []
        current_values: list[float] = []
        leakage_values: list[float] = []
        for row_idx, row in enumerate(rows):
            if len(row) <= index:
                continue
            sweep_values.append(float(row[0]))
            current_values.append(float(row[index]))
            if leak_rows and row_idx < len(leak_rows) and len(leak_rows[row_idx]) > index:
                leakage_values.append(float(leak_rows[row_idx][index]))
        curves.append(
            B1500Curve(
                bias_label=bias_header,
                bias_value=_parse_bias_value(bias_header),
                sweep_values=sweep_values,
                current_values=current_values,
                leakage_values=leakage_values,
            )
        )
    return curves, sweep_axis_label, bias_axis_label


def _load_metadata(json_path: Path, measurement_type: str) -> DeviceMetadata:
    with json_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    device = payload.get("device", {})
    stage = payload.get("coordinates", {}).get("stage_um", {})
    measurement = payload.get("measurement", {})
    device_name = device.get("name") or json_path.stem.replace(f"_{measurement_type}", "")
    row, col = _normalized_row_col(device_name, _to_optional_int(device.get("row")), _to_optional_int(device.get("col")))
    return DeviceMetadata(
        device_name=device_name,
        row=row,
        col=col,
        order=_to_optional_int(device.get("order")),
        created_at=payload.get("created_at"),
        stage_x_um=_to_optional_float(stage.get("x")),
        stage_y_um=_to_optional_float(stage.get("y")),
        start_v=_to_optional_float(measurement.get("sweep_start_v")),
        stop_v=_to_optional_float(measurement.get("sweep_end_v")),
        step_v=None,
        current_limit_a=_to_optional_float(measurement.get("drain_current_compliance_a")),
        extra=payload,
    )


def _fallback_metadata(csv_path: Path, measurement_type: str) -> DeviceMetadata:
    device_name = csv_path.stem.replace(f"_{measurement_type}_ALL_wide", "")
    row, col = _normalized_row_col(device_name, None, None)
    return DeviceMetadata(device_name=device_name, row=row, col=col)


def _parse_bias_value(header: str) -> float:
    match = re.search(r"=([-+0-9.eE]+)$", header)
    if not match:
        return 0.0
    return float(match.group(1))


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
