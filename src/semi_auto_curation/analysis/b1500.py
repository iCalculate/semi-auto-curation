from __future__ import annotations

from dataclasses import asdict

import numpy as np

from semi_auto_curation.data.b1500_loader import discover_b1500_files, load_b1500_measurement
from semi_auto_curation.models import (
    B1500AnalysisBundle,
    B1500AnalysisSettings,
    B1500BatchResult,
    B1500BatchSummary,
    B1500DeviceAnalysis,
    B1500Measurement,
)
from semi_auto_curation.services.exporter import export_b1500_bundle
from semi_auto_curation.utils.logging import log_info


def run_b1500_analysis(settings: B1500AnalysisSettings, progress_callback=None) -> B1500AnalysisBundle:
    results: dict[str, B1500BatchResult] = {}
    measurement_types = ["transfer", "output"]
    total_steps = len(measurement_types)
    for step_index, measurement_type in enumerate(measurement_types, start=1):
        files = discover_b1500_files(settings.source_dir, measurement_type)
        if not files:
            continue
        if progress_callback is not None:
            progress_callback(int((step_index - 1) / total_steps * 20), f"Loading B1500 {measurement_type} files")
        devices = _analyze_measurement_group(files, measurement_type, settings, progress_callback, step_index, total_steps)
        results[measurement_type] = _build_batch_result(settings, measurement_type, devices)
    if not results:
        raise FileNotFoundError(f"No B1500 transfer/output files found in {settings.source_dir}")
    bundle = B1500AnalysisBundle(settings=settings, results=results)
    exported_files = export_b1500_bundle(bundle)
    log_info(f"B1500 analysis completed with {len(exported_files)} exported files.")
    if progress_callback is not None:
        progress_callback(100, "B1500 analysis completed")
    return bundle


def b1500_bundle_to_dict(bundle: B1500AnalysisBundle) -> dict[str, object]:
    return {
        "settings": {
            "source_dir": str(bundle.settings.source_dir),
            "output_dir": str(bundle.settings.output_dir),
            "transfer_leakage_floor_a": bundle.settings.transfer_leakage_floor_a,
            "output_fit_tail_fraction": bundle.settings.output_fit_tail_fraction,
        },
        "results": {name: {"summary": asdict(result.summary), "devices": [asdict(device) for device in result.devices]} for name, result in bundle.results.items()},
    }


def _analyze_measurement_group(files, measurement_type: str, settings: B1500AnalysisSettings, progress_callback, step_index: int, total_steps: int):
    devices: list[B1500DeviceAnalysis] = []
    total_files = len(files)
    for file_index, csv_path in enumerate(files, start=1):
        measurement = load_b1500_measurement(csv_path, measurement_type)
        devices.append(_analyze_measurement(measurement, settings))
        if progress_callback is not None:
            progress = 20 + int(((step_index - 1) + file_index / max(total_files, 1)) / total_steps * 70)
            progress_callback(progress, f"Analyzing {measurement_type} {measurement.metadata.device_name} ({file_index}/{total_files})")
    return devices


def _analyze_measurement(measurement: B1500Measurement, settings: B1500AnalysisSettings) -> B1500DeviceAnalysis:
    current_values = [abs(value) for curve in measurement.curves for value in curve.current_values]
    leakage_values = [abs(value) for curve in measurement.curves for value in curve.leakage_values]
    transfer_on_current = None
    transfer_off_current = None
    transfer_on_off_ratio = None
    transfer_gm_max = None
    output_sat_resistance = None
    output_max_current = None

    if measurement.measurement_type == "transfer":
        transfer_on_current = max(current_values) if current_values else None
        transfer_off_current = _min_positive(current_values, settings.transfer_leakage_floor_a)
        if transfer_on_current is not None and transfer_off_current is not None and transfer_off_current > 0:
            transfer_on_off_ratio = transfer_on_current / transfer_off_current
        gm_values = [_curve_gm_max(curve.sweep_values, curve.current_values) for curve in measurement.curves]
        gm_values = [value for value in gm_values if value is not None]
        transfer_gm_max = max(gm_values) if gm_values else None
    elif measurement.measurement_type == "output":
        output_max_current = max(current_values) if current_values else None
        target_curve = max(measurement.curves, key=lambda curve: abs(curve.bias_value), default=None)
        if target_curve is not None:
            output_sat_resistance = _tail_resistance(target_curve.sweep_values, target_curve.current_values, settings.output_fit_tail_fraction)

    return B1500DeviceAnalysis(
        device_name=measurement.metadata.device_name,
        measurement_type=measurement.measurement_type,
        csv_path=str(measurement.csv_path),
        leakage_csv_path=str(measurement.leakage_csv_path) if measurement.leakage_csv_path else None,
        json_path=str(measurement.json_path) if measurement.json_path else None,
        metadata=measurement.metadata,
        sweep_axis_label=measurement.sweep_axis_label,
        bias_axis_label=measurement.bias_axis_label,
        curves=measurement.curves,
        curve_count=len(measurement.curves),
        point_count=sum(len(curve.sweep_values) for curve in measurement.curves),
        max_abs_current_a=max(current_values) if current_values else None,
        max_abs_gate_leakage_a=max(leakage_values) if leakage_values else None,
        transfer_on_current_a=transfer_on_current,
        transfer_off_current_a=transfer_off_current,
        transfer_on_off_ratio=transfer_on_off_ratio,
        transfer_gm_max_s=transfer_gm_max,
        output_max_current_a=output_max_current,
        output_sat_resistance_ohm=output_sat_resistance,
    )


def _build_batch_result(settings: B1500AnalysisSettings, measurement_type: str, devices: list[B1500DeviceAnalysis]) -> B1500BatchResult:
    rows = [device.metadata.row for device in devices if device.metadata.row is not None]
    cols = [device.metadata.col for device in devices if device.metadata.col is not None]
    summary = B1500BatchSummary(
        source_dir=str(settings.source_dir),
        output_dir=str(settings.output_dir),
        measurement_type=measurement_type,
        total_devices=len(devices),
        rows=(max(rows) + 1) if rows else 0,
        cols=(max(cols) + 1) if cols else 0,
        mean_curve_count=_mean_or_none([device.curve_count for device in devices]),
        mean_max_abs_current_a=_mean_or_none([device.max_abs_current_a for device in devices]),
        mean_max_abs_gate_leakage_a=_mean_or_none([device.max_abs_gate_leakage_a for device in devices]),
        mean_transfer_on_off_ratio=_mean_or_none([device.transfer_on_off_ratio for device in devices]),
        mean_transfer_gm_max_s=_mean_or_none([device.transfer_gm_max_s for device in devices]),
        mean_output_sat_resistance_ohm=_mean_or_none([device.output_sat_resistance_ohm for device in devices]),
        exported_files=[],
    )
    return B1500BatchResult(settings=settings, summary=summary, devices=devices)


def _curve_gm_max(x_values: list[float], y_values: list[float]) -> float | None:
    if len(x_values) < 2 or len(y_values) < 2:
        return None
    dx = np.diff(np.asarray(x_values, dtype=float))
    dy = np.diff(np.asarray(y_values, dtype=float))
    valid = dx != 0
    if not np.any(valid):
        return None
    gm = np.abs(dy[valid] / dx[valid])
    return float(np.max(gm)) if gm.size else None


def _tail_resistance(x_values: list[float], y_values: list[float], tail_fraction: float) -> float | None:
    if len(x_values) < 3 or len(y_values) < 3:
        return None
    total = len(x_values)
    tail_points = max(3, int(total * tail_fraction))
    x = np.asarray(x_values[-tail_points:], dtype=float)
    y = np.asarray(y_values[-tail_points:], dtype=float)
    if np.unique(x).size < 2:
        return None
    slope, _intercept = np.polyfit(x, y, 1)
    if slope == 0:
        return None
    return float(abs(1.0 / slope))


def _min_positive(values: list[float], floor: float) -> float | None:
    if not values:
        return None
    clipped = [max(abs(value), floor) for value in values]
    return min(clipped) if clipped else None


def _mean_or_none(values) -> float | None:
    finite = [value for value in values if value is not None and np.isfinite(value)]
    if not finite:
        return None
    return float(np.mean(finite))
