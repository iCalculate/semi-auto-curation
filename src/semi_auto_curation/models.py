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


@dataclass(slots=True)
class B1500Curve:
    bias_label: str
    bias_value: float
    sweep_values: list[float]
    current_values: list[float]
    leakage_values: list[float] = field(default_factory=list)


@dataclass(slots=True)
class B1500Measurement:
    measurement_type: str
    csv_path: Path
    leakage_csv_path: Path | None
    json_path: Path | None
    metadata: DeviceMetadata
    sweep_axis_label: str
    bias_axis_label: str
    curves: list[B1500Curve]


@dataclass(slots=True)
class B1500AnalysisSettings:
    source_dir: Path
    output_dir: Path
    transfer_leakage_floor_a: float = 1e-12
    output_fit_tail_fraction: float = 0.25


@dataclass(slots=True)
class B1500DeviceAnalysis:
    device_name: str
    measurement_type: str
    csv_path: str
    leakage_csv_path: str | None
    json_path: str | None
    metadata: DeviceMetadata
    sweep_axis_label: str
    bias_axis_label: str
    curves: list[B1500Curve]
    curve_count: int
    point_count: int
    max_abs_current_a: float | None
    max_abs_gate_leakage_a: float | None
    transfer_on_current_a: float | None
    transfer_off_current_a: float | None
    transfer_on_off_ratio: float | None
    transfer_gm_max_s: float | None
    output_max_current_a: float | None
    output_sat_resistance_ohm: float | None
    is_dummy: bool = False
    dummy_reason: str = ""

    def metric_value(self, metric: str) -> float | None:
        metrics = {
            "max_abs_current_a": self.max_abs_current_a,
            "max_abs_gate_leakage_a": self.max_abs_gate_leakage_a,
            "transfer_on_current_a": self.transfer_on_current_a,
            "transfer_off_current_a": self.transfer_off_current_a,
            "transfer_on_off_ratio": self.transfer_on_off_ratio,
            "transfer_gm_max_s": self.transfer_gm_max_s,
            "output_max_current_a": self.output_max_current_a,
            "output_sat_resistance_ohm": self.output_sat_resistance_ohm,
        }
        return metrics.get(metric)


@dataclass(slots=True)
class B1500BatchSummary:
    source_dir: str
    output_dir: str
    measurement_type: str
    total_devices: int
    rows: int
    cols: int
    mean_curve_count: float | None
    mean_max_abs_current_a: float | None
    mean_max_abs_gate_leakage_a: float | None
    mean_transfer_on_off_ratio: float | None
    mean_transfer_gm_max_s: float | None
    mean_output_sat_resistance_ohm: float | None
    exported_files: list[str]


@dataclass(slots=True)
class B1500BatchResult:
    settings: B1500AnalysisSettings
    summary: B1500BatchSummary
    devices: list[B1500DeviceAnalysis]

    def device_map(self) -> dict[tuple[int, int], B1500DeviceAnalysis]:
        mapping: dict[tuple[int, int], B1500DeviceAnalysis] = {}
        for device in self.devices:
            if device.metadata.row is None or device.metadata.col is None:
                continue
            mapping[(device.metadata.row, device.metadata.col)] = device
        return mapping


@dataclass(slots=True)
class B1500AnalysisBundle:
    settings: B1500AnalysisSettings
    results: dict[str, B1500BatchResult]
