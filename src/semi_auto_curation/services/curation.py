from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from semi_auto_curation.models import IVDeviceAnalysis


def filter_iv_devices(
    devices: Iterable[IVDeviceAnalysis],
    metric: str,
    minimum: float | None = None,
    maximum: float | None = None,
    exclude_dummy: bool = True,
) -> list[IVDeviceAnalysis]:
    """Return coordinate-bearing devices that satisfy the curation criteria."""
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("Selection minimum cannot be greater than selection maximum.")

    selected: list[IVDeviceAnalysis] = []
    for device in devices:
        if device.metadata.row is None or device.metadata.col is None:
            continue
        if exclude_dummy and device.is_dummy:
            continue
        value = device.metric_value(metric)
        if value is None or not np.isfinite(value):
            continue
        if minimum is not None and value < minimum:
            continue
        if maximum is not None and value > maximum:
            continue
        selected.append(device)
    return sorted(selected, key=lambda item: (item.metadata.row, item.metadata.col, item.device_name))


def export_iv_coordinate_selection(
    path: Path,
    devices: Iterable[IVDeviceAnalysis],
    *,
    metric: str,
    minimum: float | None,
    maximum: float | None,
    exclude_dummy: bool,
    source_dir: Path | str,
) -> Path:
    """Write an SAP-native AutoTest point list plus SAC selection details."""
    selected = list(devices)
    points: list[dict[str, object]] = []
    missing_gds: list[str] = []
    for device in selected:
        gds = _gds_coordinates(device)
        if gds is None:
            missing_gds.append(device.device_name)
            continue
        u, v = gds
        points.append(
            {
                "name": device.device_name,
                "u": u,
                "v": v,
                "row": device.metadata.row,
                "col": device.metadata.col,
            }
        )
    if missing_gds:
        names = ", ".join(missing_gds)
        raise ValueError(
            f"Selected devices are missing GDS coordinates: {names}. "
            "Load SAP measurement JSON sidecars before exporting an AutoTest list."
        )
    payload = {
        "format": "semi_auto_probe.autotest_point_list",
        "version": 1,
        "source": "semi_auto_curation",
        "points": points,
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir),
        "criteria": {
            "metric": metric,
            "minimum": minimum,
            "maximum": maximum,
            "exclude_dummy": exclude_dummy,
        },
        "count": len(selected),
        # Zero-based [row, col], matching SAC's heatmap and internal array indices.
        "coordinate_list": [
            [device.metadata.row, device.metadata.col]
            for device in selected
        ],
        "devices": [
            {
                "device_name": device.device_name,
                "row": device.metadata.row,
                "col": device.metadata.col,
                "array_position": _array_position(device.metadata.row, device.metadata.col),
                "metric": metric,
                "value": device.metric_value(metric),
                "is_dummy": device.is_dummy,
            }
            for device in selected
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def _gds_coordinates(device: IVDeviceAnalysis) -> tuple[float, float] | None:
    coordinates = device.metadata.extra.get("coordinates")
    if not isinstance(coordinates, dict):
        return None
    gds = coordinates.get("gds")
    if not isinstance(gds, dict):
        return None
    try:
        u = float(gds["u"])
        v = float(gds["v"])
    except (KeyError, TypeError, ValueError):
        return None
    if not np.isfinite(u) or not np.isfinite(v):
        return None
    return u, v


def _array_position(row: int | None, col: int | None) -> str | None:
    if row is None or col is None:
        return None
    if 0 <= row < 26:
        return f"{chr(ord('A') + row)}{col + 1}"
    return f"row={row}, col={col}"
