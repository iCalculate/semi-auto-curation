from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import numpy as np
from semi_auto_curation.data.loader import discover_iv_files

from semi_auto_curation.models import (
    DeviceMeasurement,
    IVAnalysisSettings,
    IVBatchResult,
    IVBatchSummary,
    IVDeviceAnalysis,
)
from semi_auto_curation.services.exporter import export_iv_batch
from semi_auto_curation.services.iv_cache import IVCacheDatabase
from semi_auto_curation.utils.logging import log_info


def build_iv_database(settings: IVAnalysisSettings, progress_callback=None) -> tuple[Path, int, float | None, float | None]:
    cache_db_path = settings.cache_db_path or (settings.output_dir / ".cache" / "iv_cache.sqlite3")
    log_info(f"Building IV cache database at {cache_db_path}")
    csv_files = discover_iv_files(settings.source_dir)
    if not csv_files:
        raise FileNotFoundError(f"No *_iv.csv files found in {settings.source_dir}")
    cache = IVCacheDatabase(cache_db_path)
    try:
        count = cache.build_cache(settings.source_dir, progress_callback=_cache_progress(progress_callback))
        measurements = cache.load_measurements(settings.source_dir)
    finally:
        cache.close()
    voltages = [point.voltage_v for measurement in measurements for point in measurement.points]
    voltage_min = min(voltages) if voltages else None
    voltage_max = max(voltages) if voltages else None
    log_info(f"IV cache database ready with {count} measurements.")
    if progress_callback is not None:
        progress_callback(100, "Database build finished")
    return cache_db_path, count, voltage_min, voltage_max


def run_iv_batch(settings: IVAnalysisSettings, progress_callback=None) -> IVBatchResult:
    cache_db_path = settings.cache_db_path or (settings.output_dir / ".cache" / "iv_cache.sqlite3")
    log_info(f"Preparing IV cache database at {cache_db_path}")
    cache = IVCacheDatabase(cache_db_path)
    try:
        measurements = cache.load_measurements(settings.source_dir, progress_callback=_cache_progress(progress_callback))
    finally:
        cache.close()
    log_info(f"Loaded {len(measurements)} IV measurements from source/cache.")
    devices: list[IVDeviceAnalysis] = []
    total_measurements = len(measurements)
    for index, measurement in enumerate(measurements, start=1):
        devices.append(analyze_iv_measurement(measurement, settings))
        if progress_callback is not None:
            progress = 45 + int(index / max(total_measurements, 1) * 45)
            progress_callback(progress, f"Analyzing {measurement.metadata.device_name} ({index}/{total_measurements})")
    rows = [device.metadata.row for device in devices if device.metadata.row is not None]
    cols = [device.metadata.col for device in devices if device.metadata.col is not None]
    valid_resistances = [
        device.abs_fit_resistance_ohm
        for device in devices
        if not device.is_dummy and device.abs_fit_resistance_ohm is not None and np.isfinite(device.abs_fit_resistance_ohm)
    ]
    valid_r2 = [device.fit_r2 for device in devices if not device.is_dummy and device.fit_r2 is not None]
    summary = IVBatchSummary(
        source_dir=str(settings.source_dir),
        output_dir=str(settings.output_dir),
        total_devices=len(devices),
        dummy_devices=sum(1 for device in devices if device.is_dummy),
        valid_devices=sum(1 for device in devices if not device.is_dummy),
        rows=(max(rows) + 1) if rows else 0,
        cols=(max(cols) + 1) if cols else 0,
        mean_abs_fit_resistance_ohm=(float(np.mean(valid_resistances)) if valid_resistances else None),
        mean_fit_r2=(float(np.mean(valid_r2)) if valid_r2 else None),
        exported_files=[],
    )
    batch = IVBatchResult(settings=settings, summary=summary, devices=devices)
    if progress_callback is not None:
        progress_callback(95, "Exporting IV analysis results")
    batch.summary.exported_files = export_iv_batch(batch)
    log_info("IV batch export completed.")
    if progress_callback is not None:
        progress_callback(100, "IV analysis completed")
    return batch


def _cache_progress(progress_callback):
    if progress_callback is None:
        return None

    def callback(index: int, total: int, message: str) -> None:
        progress = int(index / max(total, 1) * 40)
        progress_callback(progress, message)

    return callback


def analyze_iv_measurement(measurement: DeviceMeasurement, settings: IVAnalysisSettings) -> IVDeviceAnalysis:
    points = measurement.points
    abs_currents = [abs(point.current_a) for point in points]
    fit_points = [
        point
        for point in points
        if settings.fit_voltage_min <= point.voltage_v <= settings.fit_voltage_max
    ]
    slope, intercept, r2, resistance = _linear_fit(fit_points)
    abs_resistance = abs(resistance) if resistance is not None else None
    is_dummy, reason = _classify_dummy(abs_resistance, r2, len(fit_points), settings)
    return IVDeviceAnalysis(
        device_name=measurement.metadata.device_name,
        csv_path=str(measurement.csv_path),
        json_path=str(measurement.json_path) if measurement.json_path else None,
        metadata=measurement.metadata,
        points=points,
        fit_point_count=len(fit_points),
        fit_voltage_min=settings.fit_voltage_min,
        fit_voltage_max=settings.fit_voltage_max,
        fit_slope_a_per_v=slope,
        fit_intercept_a=intercept,
        fit_r2=r2,
        fit_resistance_ohm=resistance,
        abs_fit_resistance_ohm=abs_resistance,
        max_abs_current_a=max(abs_currents) if abs_currents else 0.0,
        min_abs_current_a=min(abs_currents) if abs_currents else None,
        is_dummy=is_dummy,
        dummy_reason=reason,
    )


def iv_batch_to_dict(batch: IVBatchResult) -> dict[str, object]:
    return {
        "settings": asdict(batch.settings),
        "summary": asdict(batch.summary),
        "devices": [asdict(device) for device in batch.devices],
    }


def _linear_fit(points) -> tuple[float | None, float | None, float | None, float | None]:
    if len(points) < 2:
        return None, None, None, None
    voltages = np.array([point.voltage_v for point in points], dtype=float)
    currents = np.array([point.current_a for point in points], dtype=float)
    if np.unique(voltages).size < 2:
        return None, None, None, None
    slope, intercept = np.polyfit(voltages, currents, 1)
    fitted = slope * voltages + intercept
    residual = np.sum((currents - fitted) ** 2)
    total = np.sum((currents - np.mean(currents)) ** 2)
    r2 = 1.0 if total == 0 else 1.0 - residual / total
    if slope == 0.0:
        resistance = 1e30
    else:
        resistance = 1.0 / slope
    return float(slope), float(intercept), float(r2), float(resistance)


def _classify_dummy(
    abs_resistance: float | None,
    r2: float | None,
    fit_point_count: int,
    settings: IVAnalysisSettings,
) -> tuple[bool, str]:
    if fit_point_count < settings.dummy_min_fit_points:
        return True, "Too few points in fit window."
    if abs_resistance is None or r2 is None:
        return True, "Linear fit is not available."
    if r2 < settings.dummy_min_r2:
        return True, f"Fit R^2 below threshold ({r2:.4f})."
    if settings.dummy_min_resistance_ohm is not None and abs_resistance < settings.dummy_min_resistance_ohm:
        return True, f"Resistance below dummy lower bound ({abs_resistance:.4e})."
    if settings.dummy_max_resistance_ohm is not None and abs_resistance > settings.dummy_max_resistance_ohm:
        return True, f"Resistance above dummy upper bound ({abs_resistance:.4e})."
    return False, ""
