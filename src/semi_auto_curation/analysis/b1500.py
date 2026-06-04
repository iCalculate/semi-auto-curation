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


TRANSFER_PEAK_FRAC = 0.8
TRANSFER_IOFF_FRAC = 3.0
TRANSFER_MIN_WINDOW_POINTS = 6
TRANSFER_SMOOTHING_POINTS = 5
TRANSFER_DENOISE_WINDOW = 5
TRANSFER_MIN_VON_POINTS = 5
TRANSFER_MIN_VON_R2 = 0.95
TRANSFER_MIN_VON_SLOPE = 1e-12
TRANSFER_MIN_VON_DELTA_V = 0.02
TRANSFER_IREF_A = 1e-7
OUTPUT_LINEAR_REGION_MAX_V = 0.1
OUTPUT_MIN_LINEAR_POINTS = 5
OUTPUT_MIN_SATURATION_POINTS = 5


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
    transfer_ss = None
    transfer_slope = None
    transfer_ss_fit_r2 = None
    transfer_vth = None
    transfer_vth_iref = None
    transfer_vth_gm = None
    transfer_vth_cross = None
    transfer_von = None
    transfer_von_fit_r2 = None
    output_sat_resistance = None
    output_max_current = None
    output_on_resistance = None
    output_linear_slope = None
    output_gds_sat = None
    output_ro_sat = None
    output_id_sat = None
    output_lambda = None
    output_early_voltage = None
    output_knee_voltage = None
    curve_features: list[dict[str, object]] = []

    if measurement.measurement_type == "transfer":
        per_curve = [_analyze_transfer_curve(curve, settings) for curve in measurement.curves]
        curve_features = [_feature_row(curve, metrics) for curve, metrics in zip(measurement.curves, per_curve)]
        representative = _representative_curve_metrics(measurement.curves, per_curve)
        transfer_on_current = max(current_values) if current_values else None
        transfer_off_current = _min_positive(current_values, settings.transfer_leakage_floor_a)
        if transfer_on_current is not None and transfer_off_current is not None and transfer_off_current > 0:
            transfer_on_off_ratio = transfer_on_current / transfer_off_current
        gm_values = [metrics.get("gm_max_s") for metrics in per_curve]
        gm_values = [value for value in gm_values if value is not None and np.isfinite(value)]
        transfer_gm_max = float(max(gm_values)) if gm_values else None
        transfer_ss = _finite_or_none(representative.get("subthreshold_swing_mv_dec"))
        transfer_slope = _finite_or_none(representative.get("subthreshold_slope_dec_per_v"))
        transfer_ss_fit_r2 = _finite_or_none(representative.get("ss_fit_r2"))
        transfer_vth = _finite_or_none(representative.get("threshold_voltage_v"))
        transfer_vth_iref = _finite_or_none(representative.get("threshold_voltage_iref_v"))
        transfer_vth_gm = _finite_or_none(representative.get("threshold_voltage_gm_v"))
        transfer_vth_cross = _finite_or_none(representative.get("threshold_voltage_cross_v"))
        transfer_von = _finite_or_none(representative.get("turn_on_voltage_v"))
        transfer_von_fit_r2 = _finite_or_none(representative.get("von_fit_r2"))
    elif measurement.measurement_type == "output":
        output_max_current = max(current_values) if current_values else None
        per_curve = [_analyze_output_curve(curve, settings) for curve in measurement.curves]
        curve_features = [_feature_row(curve, metrics) for curve, metrics in zip(measurement.curves, per_curve)]
        representative = _representative_curve_metrics(measurement.curves, per_curve)
        output_sat_resistance = _finite_or_none(representative.get("sat_resistance_ohm"))
        output_on_resistance = _finite_or_none(representative.get("on_resistance_ohm"))
        output_linear_slope = _finite_or_none(representative.get("linear_slope_a_per_v"))
        output_gds_sat = _finite_or_none(representative.get("gds_sat_s"))
        output_ro_sat = _finite_or_none(representative.get("ro_sat_ohm"))
        output_id_sat = _finite_or_none(representative.get("id_sat_a"))
        output_lambda = _finite_or_none(representative.get("lambda_1_v"))
        output_early_voltage = _finite_or_none(representative.get("early_voltage_v"))
        output_knee_voltage = _finite_or_none(representative.get("knee_voltage_v"))

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
        transfer_subthreshold_swing_mv_dec=transfer_ss,
        transfer_subthreshold_slope_dec_per_v=transfer_slope,
        transfer_ss_fit_r2=transfer_ss_fit_r2,
        transfer_threshold_voltage_v=transfer_vth,
        transfer_threshold_voltage_iref_v=transfer_vth_iref,
        transfer_threshold_voltage_gm_v=transfer_vth_gm,
        transfer_threshold_voltage_cross_v=transfer_vth_cross,
        transfer_turn_on_voltage_v=transfer_von,
        transfer_von_fit_r2=transfer_von_fit_r2,
        output_max_current_a=output_max_current,
        output_sat_resistance_ohm=output_sat_resistance,
        output_on_resistance_ohm=output_on_resistance,
        output_linear_slope_a_per_v=output_linear_slope,
        output_gds_sat_s=output_gds_sat,
        output_ro_sat_ohm=output_ro_sat,
        output_id_sat_a=output_id_sat,
        output_lambda_1_v=output_lambda,
        output_early_voltage_v=output_early_voltage,
        output_knee_voltage_v=output_knee_voltage,
        curve_features=curve_features,
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
        mean_transfer_subthreshold_swing_mv_dec=_mean_or_none([device.transfer_subthreshold_swing_mv_dec for device in devices]),
        mean_transfer_threshold_voltage_v=_mean_or_none([device.transfer_threshold_voltage_v for device in devices]),
        mean_transfer_turn_on_voltage_v=_mean_or_none([device.transfer_turn_on_voltage_v for device in devices]),
        mean_output_sat_resistance_ohm=_mean_or_none([device.output_sat_resistance_ohm for device in devices]),
        mean_output_on_resistance_ohm=_mean_or_none([device.output_on_resistance_ohm for device in devices]),
        mean_output_gds_sat_s=_mean_or_none([device.output_gds_sat_s for device in devices]),
        exported_files=[],
    )
    return B1500BatchResult(settings=settings, summary=summary, devices=devices)


def _analyze_transfer_curve(curve, settings: B1500AnalysisSettings) -> dict[str, float | None]:
    x, y = _sorted_xy(curve.sweep_values, curve.current_values)
    if x.size < 3 or y.size < 3:
        return _transfer_empty_metrics()
    abs_y = np.maximum(np.abs(y), settings.transfer_leakage_floor_a)
    log_y = np.log10(abs_y)
    smoothed_log = _smooth_series(log_y, TRANSFER_DENOISE_WINDOW)
    dlog_dv = _safe_gradient(smoothed_log, x)
    dlog_dv = _smooth_series(dlog_dv, TRANSFER_SMOOTHING_POINTS)
    ioff = _median_low_fraction(abs_y, 0.10)
    leave_threshold = ioff * TRANSFER_IOFF_FRAC if ioff is not None else settings.transfer_leakage_floor_a
    leave_idx = int(np.argmax(abs_y >= leave_threshold)) if np.any(abs_y >= leave_threshold) else 0
    slope_slice = dlog_dv[leave_idx:]
    finite_slope = np.where(np.isfinite(slope_slice), slope_slice, -np.inf)
    peak_offset = int(np.argmax(finite_slope)) if finite_slope.size else 0
    peak_idx = leave_idx + peak_offset
    peak_slope = finite_slope[peak_offset] if finite_slope.size else np.nan
    if not np.isfinite(peak_slope):
        peak_slope = 0.0
    slope_threshold = TRANSFER_PEAK_FRAC * peak_slope if peak_slope > 0 else 0.0
    left = peak_idx
    right = peak_idx
    while left > 0 and dlog_dv[left - 1] >= slope_threshold:
        left -= 1
    while right < len(x) - 1 and dlog_dv[right + 1] >= slope_threshold:
        right += 1
    if right - left + 1 < TRANSFER_MIN_WINDOW_POINTS:
        pad = int(np.ceil((TRANSFER_MIN_WINDOW_POINTS - (right - left + 1)) / 2))
        left = max(0, left - pad)
        right = min(len(x) - 1, right + pad)
    fit_x = x[left : right + 1]
    fit_y = log_y[left : right + 1]
    slope_dec, intercept_dec, ss_fit_r2 = _linear_fit_with_r2(fit_x, fit_y)
    gm = _safe_gradient(y, x)
    gm_abs = np.abs(gm)
    gm_max = float(np.nanmax(gm_abs)) if np.any(np.isfinite(gm_abs)) else None
    if gm_max is not None and np.isfinite(gm_max):
        gm_idx = int(np.nanargmax(gm_abs))
        vth_gm = float(x[gm_idx])
    else:
        vth_gm = None
    ion = float(np.nanmax(abs_y)) if np.any(np.isfinite(abs_y)) else None
    ioff_pos = _min_positive(abs_y.tolist(), settings.transfer_leakage_floor_a)
    on_off_ratio = ion / ioff_pos if ion is not None and ioff_pos not in (None, 0) else None
    ss_mv_dec = None
    vth = None
    vth_iref = None
    if slope_dec is not None and slope_dec > 0:
        ss_mv_dec = float(1e3 / slope_dec)
        if ioff is not None and ioff > 0:
            vth = float((np.log10(ioff * 10.0) - intercept_dec) / slope_dec)
        vth_iref = float((np.log10(TRANSFER_IREF_A) - intercept_dec) / slope_dec)
    von, von_fit_r2, vth_cross = _transfer_von_metrics(x, y, slope_dec, intercept_dec)
    return {
        "on_current_a": ion,
        "off_current_a": ioff_pos,
        "on_off_ratio": on_off_ratio,
        "gm_max_s": gm_max,
        "subthreshold_swing_mv_dec": ss_mv_dec,
        "subthreshold_slope_dec_per_v": slope_dec,
        "ss_fit_r2": ss_fit_r2,
        "threshold_voltage_v": vth,
        "threshold_voltage_iref_v": vth_iref,
        "threshold_voltage_gm_v": vth_gm,
        "threshold_voltage_cross_v": vth_cross,
        "turn_on_voltage_v": von,
        "von_fit_r2": von_fit_r2,
    }


def _analyze_output_curve(curve, settings: B1500AnalysisSettings) -> dict[str, float | None]:
    x, y = _sorted_xy(curve.sweep_values, curve.current_values)
    if x.size < 3 or y.size < 3:
        return _output_empty_metrics()
    abs_x = np.abs(x)
    lin_mask = abs_x <= OUTPUT_LINEAR_REGION_MAX_V
    if np.count_nonzero(lin_mask) < OUTPUT_MIN_LINEAR_POINTS:
        lin_mask = _smallest_abs_mask(x, OUTPUT_MIN_LINEAR_POINTS)
    sat_fraction = np.clip(settings.output_fit_tail_fraction, 0.05, 0.95)
    sat_count = max(OUTPUT_MIN_SATURATION_POINTS, int(np.ceil(len(x) * sat_fraction)))
    sat_mask = _largest_abs_mask(x, sat_count)
    lin_slope, _lin_intercept, _ = _linear_fit_with_r2(x[lin_mask], y[lin_mask])
    sat_slope, _sat_intercept, _ = _linear_fit_with_r2(x[sat_mask], y[sat_mask])
    on_resistance = _safe_inverse_abs(lin_slope)
    sat_resistance = _safe_inverse_abs(sat_slope)
    id_sat = float(np.nanmean(np.abs(y[sat_mask]))) if np.count_nonzero(sat_mask) else None
    gds_sat = float(abs(sat_slope)) if sat_slope is not None else None
    lambda_1_v = None
    early_voltage = None
    if gds_sat is not None and id_sat not in (None, 0):
        lambda_1_v = float(gds_sat / max(id_sat, np.finfo(float).eps))
        if lambda_1_v > 0:
            early_voltage = float(1.0 / lambda_1_v)
    knee_voltage = None
    if lin_slope is not None:
        lin_pred = lin_slope * x + (_lin_intercept if _lin_intercept is not None else 0.0)
        diff = np.abs(y - lin_pred)
        if np.any(np.isfinite(diff)):
            knee_voltage = float(x[int(np.nanargmax(diff))])
    return {
        "max_current_a": float(np.nanmax(np.abs(y))) if np.any(np.isfinite(y)) else None,
        "on_resistance_ohm": on_resistance,
        "linear_slope_a_per_v": lin_slope,
        "gds_sat_s": gds_sat,
        "ro_sat_ohm": sat_resistance,
        "sat_resistance_ohm": sat_resistance,
        "id_sat_a": id_sat,
        "lambda_1_v": lambda_1_v,
        "early_voltage_v": early_voltage,
        "knee_voltage_v": knee_voltage,
    }


def _transfer_von_metrics(
    x: np.ndarray,
    y: np.ndarray,
    slope_dec: float | None,
    intercept_dec: float | None,
) -> tuple[float | None, float | None, float | None]:
    abs_y = np.abs(y)
    if x.size < TRANSFER_MIN_VON_POINTS or not np.any(np.isfinite(abs_y)):
        return None, None, None
    threshold = float(np.nanpercentile(abs_y, 80))
    select = abs_y >= threshold
    if np.count_nonzero(select) < TRANSFER_MIN_VON_POINTS:
        return None, None, None
    fit_x = x[select]
    fit_y = y[select]
    if float(np.nanmax(fit_x) - np.nanmin(fit_x)) < TRANSFER_MIN_VON_DELTA_V:
        return None, None, None
    slope_lin, intercept_lin, fit_r2 = _linear_fit_with_r2(fit_x, fit_y)
    if slope_lin is None or abs(slope_lin) < TRANSFER_MIN_VON_SLOPE:
        return None, fit_r2, None
    von = float(-intercept_lin / slope_lin) if fit_r2 is not None and fit_r2 >= TRANSFER_MIN_VON_R2 else None
    vth_cross = None
    if slope_dec is not None and intercept_dec is not None:
        vth_cross = _solve_crossing(x, slope_dec, intercept_dec, slope_lin, intercept_lin)
    return von, fit_r2, vth_cross


def _solve_crossing(
    x: np.ndarray,
    slope_dec: float,
    intercept_dec: float,
    slope_lin: float,
    intercept_lin: float,
) -> float | None:
    x_grid = np.linspace(float(np.nanmin(x)) - 0.5, float(np.nanmax(x)) + 0.5, 500)
    y_grid = np.power(10.0, slope_dec * x_grid + intercept_dec) - (slope_lin * x_grid + intercept_lin)
    valid = np.isfinite(y_grid)
    if np.count_nonzero(valid) < 2:
        return None
    adjacent_valid = valid[:-1] & valid[1:]
    idx = np.where(adjacent_valid & (np.sign(y_grid[:-1]) != np.sign(y_grid[1:])))[0]
    if idx.size:
        i = int(idx[0])
        x0 = x_grid[i]
        x1 = x_grid[i + 1]
        y0 = y_grid[i]
        y1 = y_grid[i + 1]
        if np.isfinite(y0) and np.isfinite(y1) and y1 != y0:
            return float(x0 - y0 * (x1 - x0) / (y1 - y0))
        return float((x0 + x1) / 2.0)
    finite_y = np.where(valid, np.abs(y_grid), np.inf)
    if np.all(~np.isfinite(finite_y)):
        return None
    return float(x_grid[int(np.argmin(finite_y))])


def _feature_row(curve, metrics: dict[str, float | None]) -> dict[str, object]:
    payload = {
        "bias_label": curve.bias_label,
        "bias_value": curve.bias_value,
        "point_count": len(curve.sweep_values),
    }
    payload.update(metrics)
    return payload


def _representative_curve_metrics(curves, metrics_list: list[dict[str, float | None]]) -> dict[str, float | None]:
    if not curves or not metrics_list:
        return {}
    best_index = max(range(len(curves)), key=lambda idx: abs(curves[idx].bias_value))
    return metrics_list[best_index] if best_index < len(metrics_list) else {}


def _transfer_empty_metrics() -> dict[str, float | None]:
    return {
        "on_current_a": None,
        "off_current_a": None,
        "on_off_ratio": None,
        "gm_max_s": None,
        "subthreshold_swing_mv_dec": None,
        "subthreshold_slope_dec_per_v": None,
        "ss_fit_r2": None,
        "threshold_voltage_v": None,
        "threshold_voltage_iref_v": None,
        "threshold_voltage_gm_v": None,
        "threshold_voltage_cross_v": None,
        "turn_on_voltage_v": None,
        "von_fit_r2": None,
    }


def _output_empty_metrics() -> dict[str, float | None]:
    return {
        "max_current_a": None,
        "on_resistance_ohm": None,
        "linear_slope_a_per_v": None,
        "gds_sat_s": None,
        "ro_sat_ohm": None,
        "sat_resistance_ohm": None,
        "id_sat_a": None,
        "lambda_1_v": None,
        "early_voltage_v": None,
        "knee_voltage_v": None,
    }


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


def _sorted_xy(x_values: list[float], y_values: list[float]) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    size = min(x.size, y.size)
    x = x[:size]
    y = y[:size]
    order = np.argsort(x)
    return x[order], y[order]


def _smooth_series(values: np.ndarray, window: int) -> np.ndarray:
    if values.size == 0:
        return values
    win = max(1, int(window))
    if win <= 1:
        return values.astype(float, copy=True)
    kernel = np.ones(win, dtype=float) / win
    padded = np.pad(values, (win // 2, win - 1 - win // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _safe_gradient(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    if y.size < 2 or x.size < 2:
        return np.full_like(y, np.nan, dtype=float)
    dx = np.gradient(x)
    dy = np.gradient(y)
    with np.errstate(divide="ignore", invalid="ignore"):
        gradient = dy / dx
    gradient[~np.isfinite(gradient)] = np.nan
    return gradient


def _linear_fit_with_r2(x: np.ndarray, y: np.ndarray) -> tuple[float | None, float | None, float | None]:
    if x.size < 2 or y.size < 2:
        return None, None, None
    valid = np.isfinite(x) & np.isfinite(y)
    if np.count_nonzero(valid) < 2 or np.unique(x[valid]).size < 2:
        return None, None, None
    slope, intercept = np.polyfit(x[valid], y[valid], 1)
    predicted = slope * x[valid] + intercept
    y_valid = y[valid]
    ss_tot = float(np.sum((y_valid - np.mean(y_valid)) ** 2))
    ss_res = float(np.sum((y_valid - predicted) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    return float(slope), float(intercept), _finite_or_none(r2)


def _median_low_fraction(values: np.ndarray, fraction: float) -> float | None:
    finite = values[np.isfinite(values) & (values > 0)]
    if finite.size == 0:
        return None
    count = max(3, int(np.ceil(finite.size * fraction)))
    return float(np.median(np.sort(finite)[:count]))


def _smallest_abs_mask(values: np.ndarray, count: int) -> np.ndarray:
    mask = np.zeros(values.size, dtype=bool)
    if values.size == 0:
        return mask
    order = np.argsort(np.abs(values))
    mask[order[: min(count, values.size)]] = True
    return mask


def _largest_abs_mask(values: np.ndarray, count: int) -> np.ndarray:
    mask = np.zeros(values.size, dtype=bool)
    if values.size == 0:
        return mask
    order = np.argsort(np.abs(values))
    mask[order[-min(count, values.size) :]] = True
    return mask


def _safe_inverse_abs(value: float | None) -> float | None:
    if value is None or not np.isfinite(value) or value == 0:
        return None
    return float(abs(1.0 / value))


def _finite_or_none(value) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return float(value)
