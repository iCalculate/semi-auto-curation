from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import numpy as np


_DEVICE_COLUMNS = ("point_name", "device_name", "device", "name")
_ROW_COLUMNS = ("row", "device_row")
_COL_COLUMNS = ("col", "column", "device_col")
_CURVE_COLUMNS = ("curve_index", "channel", "trace", "bias_v")


@dataclass(slots=True)
class PreviewDevice:
    device_name: str
    row: int
    col: int
    gds_u: float | None = None
    gds_v: float | None = None
    stage_x_um: float | None = None
    stage_y_um: float | None = None
    records: list[dict[str, object]] = field(default_factory=list)
    numeric: dict[str, list[float]] = field(default_factory=dict)
    source_files: list[Path] = field(default_factory=list)
    image_files: list[Path] = field(default_factory=list)


@dataclass(slots=True)
class PreviewDataset:
    devices: dict[str, PreviewDevice]
    channels: list[str]
    curve_channels: list[str]
    suggested_x: str | None = None
    suggested_y: str | None = None

    @property
    def positions(self) -> dict[str, tuple[int, int]]:
        return {name: (device.row, device.col) for name, device in self.devices.items()}


def discover_preview_files(source_dir: Path, *, hp6614c_only: bool = False) -> list[Path]:
    pattern = "*_6614c_transfer.csv" if hp6614c_only else "*.csv"
    return sorted(path for path in source_dir.rglob(pattern) if path.is_file())


def load_preview_dataset(source_dir: Path, *, hp6614c_only: bool = False) -> PreviewDataset:
    files = discover_preview_files(source_dir, hp6614c_only=hp6614c_only)
    if not files:
        kind = "HP 6614C transfer CSV" if hp6614c_only else "CSV"
        raise FileNotFoundError(f"No {kind} files found in {source_dir}")

    devices: dict[str, PreviewDevice] = {}
    for csv_path in files:
        rows = _read_rows(csv_path)
        if not rows:
            continue
        sidecar = _read_json(csv_path.with_suffix(".json"))
        default_name, default_row, default_col = _sidecar_identity(sidecar, csv_path)
        gds_u, gds_v, stage_x_um, stage_y_um = _sidecar_coordinates(sidecar)
        grouped: dict[str, list[dict[str, object]]] = {}
        for raw in rows:
            name = _first_text(raw, _DEVICE_COLUMNS) or default_name
            grouped.setdefault(name, []).append(raw)
        for name, group_rows in grouped.items():
            row = _first_int(group_rows[0], _ROW_COLUMNS)
            col = _first_int(group_rows[0], _COL_COLUMNS)
            row_gds_u = _first_float(group_rows[0], ("gds_u", "u"))
            row_gds_v = _first_float(group_rows[0], ("gds_v", "v"))
            row_stage_x = _first_float(group_rows[0], ("stage_x_um", "stage_x"))
            row_stage_y = _first_float(group_rows[0], ("stage_y_um", "stage_y"))
            inferred = infer_device_position(name)
            row = default_row if row is None else row
            col = default_col if col is None else col
            if row is None or col is None:
                row, col = inferred if inferred is not None else (-1, -1)
            device = devices.get(name)
            if device is None:
                device = PreviewDevice(
                    name,
                    int(row),
                    int(col),
                    gds_u=gds_u if gds_u is not None else row_gds_u,
                    gds_v=gds_v if gds_v is not None else row_gds_v,
                    stage_x_um=stage_x_um if stage_x_um is not None else row_stage_x,
                    stage_y_um=stage_y_um if stage_y_um is not None else row_stage_y,
                )
                devices[name] = device
            else:
                device.gds_u = device.gds_u if device.gds_u is not None else gds_u
                device.gds_v = device.gds_v if device.gds_v is not None else gds_v
                device.stage_x_um = device.stage_x_um if device.stage_x_um is not None else stage_x_um
                device.stage_y_um = device.stage_y_um if device.stage_y_um is not None else stage_y_um
            device.records.extend(group_rows)
            if csv_path not in device.source_files:
                device.source_files.append(csv_path)
            _append_numeric_records(device.numeric, group_rows)
            for key, value in _flatten_numeric_sidecar(sidecar).items():
                device.numeric.setdefault(key, []).append(value)
            if _is_hp6614c_transfer(csv_path, sidecar, group_rows):
                device.numeric.update(_hp6614c_transfer_features(group_rows))

    if not devices:
        raise ValueError(f"CSV files in {source_dir} contained no data rows")
    _assign_missing_positions(devices.values())
    _associate_device_images(source_dir, devices)
    channels = sorted({key for device in devices.values() for key in device.numeric})
    curve_channels = sorted({key for device in devices.values() for key in _numeric_record_columns(device.records)})
    suggested_x = _preferred_channel(curve_channels, ("gate_voltage_v", "source_value", "voltage_v", "elapsed_s", "index"))
    suggested_y = _preferred_channel(curve_channels, ("drain_current_a", "current_a", "resistance_ohm"), exclude={suggested_x})
    return PreviewDataset(devices, channels, curve_channels, suggested_x, suggested_y)


def feature_value(values: Iterable[float], feature: str) -> float | None:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if not finite:
        return None
    operations = {
        "first": lambda data: data[0],
        "last": lambda data: data[-1],
        "min": min,
        "max": max,
        "max_abs": lambda data: max(abs(value) for value in data),
        "mean": lambda data: sum(data) / len(data),
        "median": median,
    }
    if feature not in operations:
        raise ValueError(f"Unsupported preview feature: {feature}")
    return float(operations[feature](finite))


def heatmap_values(dataset: PreviewDataset, channel: str, feature: str) -> dict[str, float | None]:
    return {
        name: feature_value(device.numeric.get(channel, ()), feature)
        for name, device in dataset.devices.items()
    }


def curve_groups(device: PreviewDevice) -> dict[str, list[dict[str, object]]]:
    group_column = next((key for key in _CURVE_COLUMNS if any(str(row.get(key, "")).strip() for row in device.records)), None)
    if group_column is None:
        return {"": device.records}
    groups: dict[str, list[dict[str, object]]] = {}
    for row in device.records:
        raw = row.get(group_column, "")
        label = f"{group_column}={raw}" if str(raw).strip() else ""
        groups.setdefault(label, []).append(row)
    return groups


def infer_device_position(device_name: str) -> tuple[int, int] | None:
    match = re.search(r"([A-Z]+)(\d+)$", device_name, re.IGNORECASE)
    if not match:
        return None
    row = 0
    for char in match.group(1).upper():
        row = row * 26 + ord(char) - ord("A") + 1
    return row - 1, int(match.group(2)) - 1


def _read_rows(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle) if row]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _sidecar_identity(sidecar: dict[str, Any], path: Path) -> tuple[str, int | None, int | None]:
    device = sidecar.get("device") if isinstance(sidecar.get("device"), dict) else {}
    fallback = re.sub(r"_(?:6614c_)?(?:transfer|output|iv)(?:_ALL_wide)?$", "", path.stem, flags=re.IGNORECASE)
    return (
        str(device.get("name") or fallback),
        _optional_int(device.get("row")),
        _optional_int(device.get("col")),
    )


def _sidecar_coordinates(sidecar: dict[str, Any]) -> tuple[float | None, float | None, float | None, float | None]:
    coordinates = sidecar.get("coordinates") if isinstance(sidecar.get("coordinates"), dict) else {}
    gds = coordinates.get("gds") if isinstance(coordinates.get("gds"), dict) else {}
    stage = coordinates.get("stage_um") if isinstance(coordinates.get("stage_um"), dict) else {}
    return (
        _optional_float(gds.get("u")),
        _optional_float(gds.get("v")),
        _optional_float(stage.get("x")),
        _optional_float(stage.get("y")),
    )


def _associate_device_images(source_dir: Path, devices: dict[str, PreviewDevice]) -> None:
    search_root = source_dir.parent if source_dir.name.lower() in {
        "iv", "b1500", "hp6614c", "current", "wobb", "images",
    } else source_dir
    image_paths = sorted(
        path for path in search_root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
        and any(part.lower() == "images" for part in path.parts)
    )
    normalized_devices = sorted(
        ((_normalized_name(name), name) for name in devices),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for image_path in image_paths:
        image_key = _normalized_name(image_path.stem)
        for device_key, device_name in normalized_devices:
            if image_key == device_key or image_key.startswith(f"{device_key}_"):
                devices[device_name].image_files.append(image_path)
                break


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _append_numeric_records(target: dict[str, list[float]], rows: list[dict[str, object]]) -> None:
    for row in rows:
        for key, raw in row.items():
            value = _optional_float(raw)
            if value is not None:
                target.setdefault(str(key), []).append(value)


def _numeric_record_columns(rows: list[dict[str, object]]) -> set[str]:
    return {str(key) for row in rows for key, raw in row.items() if _optional_float(raw) is not None}


def _flatten_numeric_sidecar(payload: dict[str, Any]) -> dict[str, float]:
    output: dict[str, float] = {}

    def visit(value: object, prefix: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{prefix}.{key}" if prefix else str(key))
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
            if prefix.startswith(("analysis", "statistics", "processed", "features")):
                output[f"meta.{prefix}"] = float(value)

    visit(payload, "")
    return output


def _is_hp6614c_transfer(path: Path, sidecar: dict[str, Any], rows: list[dict[str, object]]) -> bool:
    result_type = str(sidecar.get("result_type", "")).lower()
    test_type = str(rows[0].get("test_type", "")).lower() if rows else ""
    return "6614c_transfer" in path.stem.lower() or result_type == "hp6614c_transfer" or (
        test_type == "transfer" and bool(rows) and "gate_voltage_v" in rows[0] and "drain_current_a" in rows[0]
    )


def _hp6614c_transfer_features(rows: list[dict[str, object]]) -> dict[str, list[float]]:
    raw_curves: dict[float, dict[float, list[float]]] = {}
    for record in rows:
        gate = _optional_float(record.get("gate_voltage_v"))
        drain = _optional_float(record.get("drain_voltage_v"))
        current = _optional_float(record.get("drain_current_a"))
        if None in (gate, drain, current):
            continue
        raw_curves.setdefault(round(float(drain), 9), {}).setdefault(round(float(gate), 12), []).append(abs(float(current)))
    vths: list[float] = []
    ratios: list[float] = []
    swings: list[float] = []
    for by_gate in raw_curves.values():
        points = sorted((gate, float(np.median(currents))) for gate, currents in by_gate.items())
        x = np.asarray([item[0] for item in points], dtype=float)
        y = np.asarray([max(item[1], 1e-30) for item in points], dtype=float)
        if x.size < 3:
            continue
        positive = y[np.isfinite(y) & (y > 0)]
        if positive.size:
            ratios.append(float(np.max(positive) / np.min(positive)))
        linear_fit = _max_abs_window_fit(list(zip(x.tolist(), y.tolist())))
        if linear_fit is not None and linear_fit[0] != 0:
            vths.append(float(-linear_fit[1] / linear_fit[0]))
        log_fit = _max_abs_window_fit(list(zip(x.tolist(), np.log10(y).tolist())))
        if log_fit is not None and log_fit[0] != 0:
            swings.append(float(1000.0 / abs(log_fit[0])))
    output: dict[str, list[float]] = {}
    if vths:
        output["derived.vth_v"] = [float(np.mean(vths))]
    if ratios:
        output["derived.on_off_ratio"] = [float(np.mean(ratios))]
    if swings:
        output["derived.ss_mv_dec"] = [float(np.mean(swings))]
    return output


def _max_abs_window_fit(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Match SAP's HP6614C feature window: steepest 25%, minimum 3 points."""
    if len(points) < 3:
        return None
    window_size = min(len(points), max(3, int(round(len(points) * 0.25))))
    best: tuple[float, float] | None = None
    best_score = -1.0
    for start in range(len(points) - window_size + 1):
        x = np.asarray([point[0] for point in points[start : start + window_size]], dtype=float)
        y = np.asarray([point[1] for point in points[start : start + window_size]], dtype=float)
        if np.unique(x).size < 2:
            continue
        slope, intercept = np.polyfit(x, y, 1)
        if math.isfinite(float(slope)) and abs(float(slope)) > best_score:
            best = float(slope), float(intercept)
            best_score = abs(float(slope))
    return best


def _assign_missing_positions(devices: Iterable[PreviewDevice]) -> None:
    items = list(devices)
    used = {(device.row, device.col) for device in items if device.row >= 0 and device.col >= 0}
    next_col = 0
    for device in items:
        if device.row >= 0 and device.col >= 0:
            continue
        while (0, next_col) in used:
            next_col += 1
        device.row, device.col = 0, next_col
        used.add((0, next_col))


def _preferred_channel(channels: list[str], preferred: tuple[str, ...], *, exclude: set[str | None] | None = None) -> str | None:
    excluded = exclude or set()
    for key in preferred:
        if key in channels and key not in excluded:
            return key
    return next((key for key in channels if key not in excluded), None)


def _first_text(row: dict[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = str(row.get(key, "")).strip()
        if value:
            return value
    return None


def _first_int(row: dict[str, object], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = _optional_int(row.get(key))
        if value is not None:
            return value
    return None


def _first_float(row: dict[str, object], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _optional_float(row.get(key))
        if value is not None:
            return value
    return None


def _optional_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _optional_int(value: object) -> int | None:
    value_float = _optional_float(value)
    return None if value_float is None else int(value_float)
