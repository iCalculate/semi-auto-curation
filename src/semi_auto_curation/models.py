from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class IVPoint:
    index: int
    elapsed_s: float
    source_value: float
    voltage_v: float
    current_a: float
    resistance_ohm: float


@dataclass(slots=True)
class DeviceMetadata:
    device_name: str
    row: int | None = None
    col: int | None = None
    order: int | None = None
    created_at: str | None = None
    stage_x_um: float | None = None
    stage_y_um: float | None = None
    start_v: float | None = None
    stop_v: float | None = None
    step_v: float | None = None
    current_limit_a: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeviceMeasurement:
    csv_path: Path
    json_path: Path | None
    metadata: DeviceMetadata
    points: list[IVPoint]


@dataclass(slots=True)
class IVAnalysisSettings:
    source_dir: Path
    output_dir: Path
    cache_db_path: Path | None = None
    fit_voltage_min: float = 0.0
    fit_voltage_max: float = 1.0
    dummy_min_resistance_ohm: float | None = None
    dummy_max_resistance_ohm: float | None = None
    dummy_min_r2: float = 0.0
    dummy_min_fit_points: int = 3
    heatmap_metric: str = "abs_fit_resistance_ohm"


@dataclass(slots=True)
class IVDeviceAnalysis:
    device_name: str
    csv_path: str
    json_path: str | None
    metadata: DeviceMetadata
    points: list[IVPoint]
    fit_point_count: int
    fit_voltage_min: float
    fit_voltage_max: float
    fit_slope_a_per_v: float | None
    fit_intercept_a: float | None
    fit_r2: float | None
    fit_resistance_ohm: float | None
    abs_fit_resistance_ohm: float | None
    max_abs_current_a: float
    min_abs_current_a: float | None
    is_dummy: bool
    dummy_reason: str

    def metric_value(self, metric: str) -> float | None:
        if metric == "fit_resistance_ohm":
            return self.fit_resistance_ohm
        if metric == "abs_fit_resistance_ohm":
            return self.abs_fit_resistance_ohm
        if metric == "fit_r2":
            return self.fit_r2
        if metric == "max_abs_current_a":
            return self.max_abs_current_a
        return None


@dataclass(slots=True)
class IVBatchSummary:
    source_dir: str
    output_dir: str
    total_devices: int
    dummy_devices: int
    valid_devices: int
    rows: int
    cols: int
    mean_abs_fit_resistance_ohm: float | None
    mean_fit_r2: float | None
    exported_files: list[str]


@dataclass(slots=True)
class IVBatchResult:
    settings: IVAnalysisSettings
    summary: IVBatchSummary
    devices: list[IVDeviceAnalysis]

    def device_map(self) -> dict[tuple[int, int], IVDeviceAnalysis]:
        mapping: dict[tuple[int, int], IVDeviceAnalysis] = {}
        for device in self.devices:
            if device.metadata.row is None or device.metadata.col is None:
                continue
            mapping[(device.metadata.row, device.metadata.col)] = device
        return mapping
