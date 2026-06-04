from __future__ import annotations

from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from semi_auto_curation.analysis.b1500 import run_b1500_analysis
from semi_auto_curation.models import B1500AnalysisBundle, B1500AnalysisSettings, B1500BatchResult, B1500DeviceAnalysis
from semi_auto_curation.services.cloud_api import CloudSyncResult
from semi_auto_curation.ui.cloud_session_dialog import CloudSessionsDialog, CloudSyncWorker
from semi_auto_curation.ui.iv_panel import (
    HeatmapCanvas,
    HeatmapRenderState,
    THEMES,
    _format_array_position,
    _format_engineering,
    _format_fixed_scale,
    _format_fixed_scale_with_unit,
    _format_number,
    _optional_float,
    _pick_engineering_unit,
)
from semi_auto_curation.ui.parallel_preview import ParallelPreviewPanel
from semi_auto_curation.utils.logging import log_error, log_info, log_warn
from semi_auto_curation.utils.units import parse_si_number


MODE_LABELS = {"transfer": "Transfer", "output": "Output"}
PANEL_LABELS = {"transfer": "B1500-Trans", "output": "B1500-Output"}
MODE_METRICS = {
    "transfer": [
        "transfer_on_off_ratio",
        "transfer_gm_max_s",
        "transfer_subthreshold_swing_mv_dec",
        "transfer_threshold_voltage_v",
        "transfer_threshold_voltage_iref_v",
        "transfer_threshold_voltage_gm_v",
        "transfer_threshold_voltage_cross_v",
        "transfer_turn_on_voltage_v",
        "transfer_subthreshold_slope_dec_per_v",
        "transfer_ss_fit_r2",
        "transfer_von_fit_r2",
        "transfer_on_current_a",
        "transfer_off_current_a",
        "max_abs_gate_leakage_a",
    ],
    "output": [
        "output_on_resistance_ohm",
        "output_gds_sat_s",
        "output_ro_sat_ohm",
        "output_lambda_1_v",
        "output_early_voltage_v",
        "output_knee_voltage_v",
        "output_id_sat_a",
        "output_linear_slope_a_per_v",
        "output_max_current_a",
        "output_sat_resistance_ohm",
        "max_abs_gate_leakage_a",
    ],
}


class B1500BatchWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, settings: B1500AnalysisSettings) -> None:
        super().__init__()
        self.settings = settings

    def run(self) -> None:
        try:
            result = run_b1500_analysis(self.settings, progress_callback=self._emit_progress)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)

    def _emit_progress(self, value: int, message: str) -> None:
        self.progress.emit(value, message)


class B1500CurveCanvas(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.figure = Figure(figsize=(6, 5), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.ax = self.figure.add_subplot(111)
        self.theme = "light"
        self.y_scale_mode = "linear"
        self.rendered_devices: list[B1500DeviceAnalysis] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)
        self._style_axes()

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        self._style_axes()
        self.canvas.draw_idle()

    def set_y_scale_mode(self, mode: str) -> None:
        self.y_scale_mode = mode

    def render_devices(self, devices: list[B1500DeviceAnalysis]) -> None:
        self.rendered_devices = list(devices)
        self.ax.clear()
        self._style_axes()
        if not devices:
            self.ax.set_title("Selected B1500 Curves")
            self.canvas.draw_idle()
            return
        colors = THEMES[self.theme]["curve_colors"]
        all_y_values: list[float] = []
        line_index = 0
        for device in devices:
            for curve in device.curves:
                y_values = [abs(value) for value in curve.current_values] if self.y_scale_mode == "log" else list(curve.current_values)
                all_y_values.extend(y_values)
                self.ax.plot(
                    curve.sweep_values,
                    y_values,
                    color=colors[line_index % len(colors)],
                    linewidth=1.6,
                    label=f"{device.device_name} {curve.bias_label}",
                )
                line_index += 1
        if self.y_scale_mode == "log":
            positive_y = [value for value in all_y_values if value > 0]
            if positive_y:
                self.ax.set_yscale("log")
            all_y_values = positive_y
        else:
            self.ax.set_yscale("linear")
        y_scale, y_unit_label = _pick_engineering_unit(all_y_values, "A")
        primary = devices[-1]
        self.ax.set_xlabel(primary.sweep_axis_label.replace("_", " "))
        self.ax.set_ylabel("Abs Current" if self.y_scale_mode == "log" else "Current")
        if len(devices) == 1:
            self.ax.set_title(f"{primary.device_name} {MODE_LABELS.get(primary.measurement_type, primary.measurement_type)} Curves")
        else:
            self.ax.set_title(f"{len(devices)} Selected {MODE_LABELS.get(primary.measurement_type, primary.measurement_type)} Devices")
        self.ax.grid(color=THEMES[self.theme]["grid"], linewidth=0.5, alpha=0.5)
        if self.y_scale_mode == "log":
            self.ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: _format_engineering(value, "A")))
        else:
            self.ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: _format_fixed_scale_with_unit(value, y_scale, y_unit_label)))
        self.ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _pos: _format_number(value)))
        if line_index:
            self.ax.legend(loc="best", fontsize=8)
        self.canvas.draw_idle()

    def copy_image_to_clipboard(self) -> None:
        QApplication.clipboard().setPixmap(self.canvas.grab())

    def rawdata_tsv(self) -> str:
        lines = ["device\tcurve\tseries_type\tsweep_value\tcurrent_a"]
        if not self.rendered_devices:
            return "\n".join(lines)
        for device in self.rendered_devices:
            for curve in device.curves:
                series_type = "abs_current" if self.y_scale_mode == "log" else "current"
                for sweep, current in zip(curve.sweep_values, curve.current_values):
                    value = abs(current) if self.y_scale_mode == "log" else current
                    lines.append(f"{device.device_name}\t{curve.bias_label}\t{series_type}\t{sweep:.12g}\t{value:.12g}")
        return "\n".join(lines)

    def _style_axes(self) -> None:
        theme_cfg = THEMES[self.theme]
        self.figure.patch.set_facecolor(theme_cfg["figure"])
        self.ax.set_facecolor(theme_cfg["axes"])
        self.ax.tick_params(colors=theme_cfg["text"])
        for spine in self.ax.spines.values():
            spine.set_color(theme_cfg["text"])
        self.ax.xaxis.label.set_color(theme_cfg["text"])
        self.ax.yaxis.label.set_color(theme_cfg["text"])
        self.ax.title.set_color(theme_cfg["text"])


class B1500AnalysisPanel(QWidget):
    title = "B1500 Trans/Output"
    status_changed = Signal(str)
    progress_changed = Signal(int)

    def __init__(self, fixed_measurement_type: str | None = None) -> None:
        super().__init__()
        if fixed_measurement_type not in {None, "transfer", "output"}:
            raise ValueError(f"Unsupported B1500 measurement type: {fixed_measurement_type}")
        self.fixed_measurement_type = fixed_measurement_type
        self.panel_label = "B1500 Trans/Output" if fixed_measurement_type is None else PANEL_LABELS[fixed_measurement_type]
        self.title = self.panel_label
        self.bundle_result: B1500AnalysisBundle | None = None
        self.worker_thread: QThread | None = None
        self.worker: B1500BatchWorker | None = None
        self.cloud_sync_thread: QThread | None = None
        self.cloud_sync_worker: CloudSyncWorker | None = None
        self.cloud_selection = None
        self.cloud_cache_root: Path | None = None
        self.cloud_detail: dict | None = None
        self.source_edit = QLineEdit(str(Path.cwd() / "rawdata" / "b1500"))
        self.output_edit = QLineEdit(str(Path.cwd() / "output"))
        self.metric_combo = QComboBox()
        self.mode_combo = QComboBox()
        mode_items = [fixed_measurement_type] if fixed_measurement_type is not None else ["transfer", "output"]
        self.mode_combo.addItems(mode_items)
        if fixed_measurement_type is not None:
            self.mode_combo.setCurrentText(fixed_measurement_type)
            self.mode_combo.setEnabled(False)
        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(["viridis", "plasma", "inferno", "magma", "cividis", "gray"])
        self.scale_mode_combo = QComboBox()
        self.scale_mode_combo.addItems(["linear", "log"])
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["light", "dark"])
        self.theme_combo.setCurrentText("dark")
        self.curve_y_scale_combo = QComboBox()
        self.curve_y_scale_combo.addItems(["linear", "log"])
        self.click_multi_select_button = QPushButton("Enable Click Multi-Select")
        self.click_multi_select_button.setCheckable(True)
        self.leakage_floor_edit = QLineEdit("1e-12")
        self.output_tail_edit = QLineEdit("0.25")
        self.level_min_edit = QLineEdit("")
        self.level_max_edit = QLineEdit("")
        self.selected_list = QListWidget()
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.summary_table = QTableWidget(2, 4)
        self.summary_table.setVerticalHeaderLabels(["Batch", "Array"])
        self.summary_table.setHorizontalHeaderLabels(["Devices", "Curves / Dev", "Metric A", "Metric B"])
        self.summary_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.summary_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.summary_table.setFixedHeight(110)
        self.heatmap = HeatmapCanvas()
        self.curve_plot = B1500CurveCanvas()
        self.preview_panel = ParallelPreviewPanel("Parallel Files Preview")
        self.cloud_source_label = QLabel("Source Mode: Local Folder")
        self.cloud_source_label.setWordWrap(True)
        self._build_ui()
        self._sync_metric_options()
        self.set_theme("dark")

    def build_toolbar_actions(self) -> list[QAction]:
        analyze = QAction(f"Analyze {self.panel_label}", self)
        analyze.triggered.connect(self.run_analysis)
        return [analyze]

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self._build_source_box())
        left_layout.addWidget(self._build_analysis_box())
        left_layout.addWidget(self._build_heatmap_box())
        left_layout.addWidget(self._build_selection_box(), 1)

        top_row = QSplitter(Qt.Horizontal)
        top_row.addWidget(self._build_heatmap_panel())
        top_row.addWidget(self._build_curve_panel())
        top_row.setSizes([620, 620])

        right = QSplitter(Qt.Vertical)
        right.addWidget(top_row)
        right.addWidget(self.preview_panel)
        right.setSizes([760, 220])

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([360, 1240])
        root.addWidget(splitter)

        self.metric_combo.currentTextChanged.connect(self.refresh_heatmap)
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        self.cmap_combo.currentTextChanged.connect(self.refresh_heatmap)
        self.scale_mode_combo.currentTextChanged.connect(self.refresh_heatmap)
        self.theme_combo.currentTextChanged.connect(self.set_theme)
        self.curve_y_scale_combo.currentTextChanged.connect(self._refresh_curve_panel)
        self.click_multi_select_button.toggled.connect(self._set_click_multi_select_mode)
        self.heatmap.selection_changed.connect(self._on_heatmap_selection_changed)

    def _build_source_box(self) -> QWidget:
        box = QGroupBox(f"{self.panel_label} Source")
        layout = QFormLayout(box)
        layout.addRow("Source Folder", self._browse_row(self.source_edit, self._choose_source))
        layout.addRow("Output Folder", self._browse_row(self.output_edit, self._choose_output))
        layout.addRow("Cloud Session", self.cloud_source_label)
        cloud_button = QPushButton("Open Cloud Session List")
        cloud_button.clicked.connect(self._open_cloud_sessions)
        layout.addRow(cloud_button)
        analyze_button = QPushButton(f"Analyze {self.panel_label}")
        analyze_button.clicked.connect(self.run_analysis)
        layout.addRow(analyze_button)
        return box

    def _build_analysis_box(self) -> QWidget:
        box = QGroupBox("Analysis Settings")
        layout = QFormLayout(box)
        if self.fixed_measurement_type is None:
            layout.addRow("View Mode", self.mode_combo)
        if self.fixed_measurement_type in {None, "transfer"}:
            layout.addRow("Transfer Leakage Floor (A)", self.leakage_floor_edit)
        if self.fixed_measurement_type in {None, "output"}:
            layout.addRow("Output Tail Fraction", self.output_tail_edit)
        return box

    def _build_heatmap_box(self) -> QWidget:
        box = QGroupBox("Heatmap Settings")
        layout = QFormLayout(box)
        layout.addRow("Metric", self.metric_combo)
        layout.addRow("Colormap", self.cmap_combo)
        layout.addRow("Color Scale", self.scale_mode_combo)
        layout.addRow("Scale Min", self.level_min_edit)
        layout.addRow("Scale Max", self.level_max_edit)
        layout.addRow("Theme", self.theme_combo)
        apply_button = QPushButton("Apply Heatmap Settings")
        apply_button.clicked.connect(self.refresh_heatmap)
        layout.addRow(apply_button)
        return box

    def _build_selection_box(self) -> QWidget:
        box = QGroupBox("Selected Devices")
        layout = QVBoxLayout(box)
        layout.addWidget(self.click_multi_select_button)
        layout.addWidget(self.selected_list, 2)
        clear_button = QPushButton("Clear Selection")
        clear_button.clicked.connect(self.clear_selection)
        layout.addWidget(clear_button)
        layout.addWidget(self.detail_text, 3)
        return box

    def _build_heatmap_panel(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(self.summary_table)
        actions = QHBoxLayout()
        copy_image_button = QPushButton("Copy Heatmap Image")
        copy_image_button.clicked.connect(self._copy_heatmap_image)
        copy_raw_button = QPushButton("Copy Heatmap Raw Data")
        copy_raw_button.clicked.connect(self._copy_heatmap_rawdata)
        actions.addWidget(copy_image_button)
        actions.addWidget(copy_raw_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addWidget(self.heatmap, 1)
        return container

    def _build_curve_panel(self) -> QWidget:
        box = QGroupBox("Selected Data")
        layout = QVBoxLayout(box)
        top = QHBoxLayout()
        top.addWidget(QLabel("Y Scale"))
        top.addWidget(self.curve_y_scale_combo)
        copy_image_button = QPushButton("Copy Curve Image")
        copy_image_button.clicked.connect(self._copy_curve_image)
        copy_raw_button = QPushButton("Copy Curve Raw Data")
        copy_raw_button.clicked.connect(self._copy_curve_rawdata)
        top.addWidget(copy_image_button)
        top.addWidget(copy_raw_button)
        top.addStretch(1)
        layout.addLayout(top)
        layout.addWidget(self.curve_plot)
        return box

    def _browse_row(self, edit: QLineEdit, callback) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        button = QPushButton("Browse...")
        button.clicked.connect(callback)
        layout.addWidget(button)
        return row

    def _choose_source(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select B1500 Source Folder", self.source_edit.text())
        if folder:
            self._clear_cloud_source()
            self.source_edit.setText(folder)

    def _choose_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)

    def _open_cloud_sessions(self) -> None:
        dialog = CloudSessionsDialog(required_category="b1500", parent=self)
        if not dialog.exec():
            return
        selection = dialog.selected_session()
        if selection is None:
            return
        output_dir = Path(self.output_edit.text().strip() or (Path.cwd() / "output"))
        self.cloud_selection = selection
        self.status_changed.emit("Loading...")
        self.progress_changed.emit(0)
        self.cloud_sync_thread = QThread(self)
        self.cloud_sync_worker = CloudSyncWorker(selection, output_dir)
        self.cloud_sync_worker.moveToThread(self.cloud_sync_thread)
        self.cloud_sync_thread.started.connect(self.cloud_sync_worker.run)
        self.cloud_sync_worker.finished.connect(self._cloud_sync_finished)
        self.cloud_sync_worker.failed.connect(self._cloud_sync_failed)
        self.cloud_sync_worker.progress.connect(self._on_cloud_sync_progress)
        self.cloud_sync_worker.finished.connect(self.cloud_sync_thread.quit)
        self.cloud_sync_worker.failed.connect(self.cloud_sync_thread.quit)
        self.cloud_sync_thread.finished.connect(self.cloud_sync_worker.deleteLater)
        self.cloud_sync_thread.finished.connect(self.cloud_sync_thread.deleteLater)
        self.cloud_sync_thread.start()

    def _cloud_sync_finished(self, result: object) -> None:
        if not isinstance(result, CloudSyncResult):
            self._cloud_sync_failed("Unexpected cloud sync payload.")
            return
        self.cloud_selection = result.selection
        self.cloud_cache_root = result.cache_root
        self.cloud_detail = result.detail
        self.source_edit.setText(str(result.source_dir))
        self.cloud_source_label.setText(f"Cloud Session: {result.selection.session_id}")
        self.preview_panel.clear_preview("Cloud session synced. Run analysis, then select a device for preview.")
        self.status_changed.emit("Finish")
        self.progress_changed.emit(100)

    def _cloud_sync_failed(self, message: str) -> None:
        log_error(f"B1500 cloud sync failed: {message}")
        QMessageBox.critical(self, "Cloud Sync Failed", message)
        self.status_changed.emit("Busy...")
        self.progress_changed.emit(0)

    def _on_cloud_sync_progress(self, value: int, message: str) -> None:
        self.progress_changed.emit(value)
        self.status_changed.emit("Loading...")

    def _clear_cloud_source(self) -> None:
        self.cloud_selection = None
        self.cloud_cache_root = None
        self.cloud_detail = None
        self.cloud_source_label.setText("Source Mode: Local Folder")

    def _current_settings(self) -> B1500AnalysisSettings:
        return B1500AnalysisSettings(
            source_dir=Path(self.source_edit.text().strip()),
            output_dir=Path(self.output_edit.text().strip()),
            transfer_leakage_floor_a=parse_si_number(self.leakage_floor_edit.text().strip() or "1e-12"),
            output_fit_tail_fraction=_optional_float(self.output_tail_edit.text()) or 0.25,
        )

    def run_analysis(self) -> None:
        try:
            settings = self._current_settings()
        except ValueError as exc:
            log_error(f"Invalid B1500 analysis input: {exc}")
            QMessageBox.critical(self, "Invalid Input", str(exc))
            return
        log_info(f"GUI B1500 analysis requested. source={settings.source_dir} output={settings.output_dir}")
        self.status_changed.emit("Running...")
        self.progress_changed.emit(0)
        self.worker_thread = QThread(self)
        self.worker = B1500BatchWorker(settings)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._analysis_finished)
        self.worker.failed.connect(self._analysis_failed)
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    def _analysis_finished(self, result: object) -> None:
        if not isinstance(result, B1500AnalysisBundle):
            self._analysis_failed("Unexpected result payload.")
            return
        self.bundle_result = result
        if self.fixed_measurement_type is None and self.mode_combo.currentText() not in result.results:
            self.mode_combo.setCurrentText(next(iter(result.results)))
        self._populate_summary(self._current_batch())
        self.status_changed.emit("Finish")
        self.progress_changed.emit(100)
        self.refresh_heatmap()

    def _analysis_failed(self, message: str) -> None:
        log_error(f"GUI B1500 analysis failed: {message}")
        QMessageBox.critical(self, "B1500 Analysis Failed", message)
        self.status_changed.emit("Busy...")
        self.progress_changed.emit(0)

    def _on_worker_progress(self, value: int, message: str) -> None:
        self.progress_changed.emit(value)
        if value < 40:
            self.status_changed.emit("Loading...")
        elif value < 100:
            self.status_changed.emit("Running...")
        else:
            self.status_changed.emit("Finish")

    def refresh_heatmap(self) -> None:
        batch = self._current_batch()
        if batch is None:
            log_warn("B1500 heatmap refresh requested before analysis result was available.")
            return
        state = HeatmapRenderState(
            metric=self.metric_combo.currentText(),
            cmap=self.cmap_combo.currentText(),
            level_min=_optional_float(self.level_min_edit.text()),
            level_max=_optional_float(self.level_max_edit.text()),
            theme=self.theme_combo.currentText(),
            scale_mode=self.scale_mode_combo.currentText(),
        )
        self.heatmap.render_batch(batch, state)
        self._populate_summary(batch)

    def clear_selection(self) -> None:
        self.heatmap.clear_selection()
        self.preview_panel.clear_preview()

    def _on_heatmap_selection_changed(self, devices: list[B1500DeviceAnalysis]) -> None:
        self.selected_list.clear()
        focus = devices[-1] if devices else None
        for device in devices:
            self.selected_list.addItem(QListWidgetItem(f"{device.device_name}  ({_format_array_position(device.metadata.row, device.metadata.col)})"))
        self.curve_plot.set_y_scale_mode(self.curve_y_scale_combo.currentText())
        self.curve_plot.render_devices(devices)
        if focus is None:
            self.detail_text.clear()
            self.preview_panel.clear_preview()
            return
        self.detail_text.setPlainText(
            "\n".join(
                self._detail_lines(focus)
            )
        )
        if self.cloud_selection is not None and self.cloud_detail is not None and self.cloud_cache_root is not None:
            self.preview_panel.update_cloud_preview(
                self.cloud_selection.config,
                self.cloud_selection.session_id,
                self.cloud_detail,
                self.cloud_cache_root,
                focus.device_name,
                self._remote_primary_paths([focus.csv_path, focus.leakage_csv_path or "", focus.json_path or ""]),
            )
        else:
            self.preview_panel.update_preview(
                Path(self.source_edit.text().strip()),
                focus.device_name,
                [focus.csv_path, focus.leakage_csv_path or "", focus.json_path or ""],
            )

    def _populate_summary(self, batch: B1500BatchResult | None) -> None:
        if batch is None:
            return
        metric_a = batch.summary.mean_transfer_on_off_ratio if batch.summary.measurement_type == "transfer" else batch.summary.mean_output_on_resistance_ohm
        metric_b = batch.summary.mean_transfer_subthreshold_swing_mv_dec if batch.summary.measurement_type == "transfer" else batch.summary.mean_output_gds_sat_s
        entries = [
            (0, 0, str(batch.summary.total_devices)),
            (0, 1, "--" if batch.summary.mean_curve_count is None else f"{batch.summary.mean_curve_count:.2f}"),
            (0, 2, "--" if metric_a is None else f"{metric_a:.4e}"),
            (0, 3, "--" if metric_b is None else f"{metric_b:.4e}"),
            (1, 0, f"{batch.summary.rows} x {batch.summary.cols}"),
            (1, 1, MODE_LABELS.get(batch.summary.measurement_type, batch.summary.measurement_type)),
            (1, 2, self.metric_combo.currentText()),
            (1, 3, "--" if batch.summary.mean_max_abs_gate_leakage_a is None else f"{batch.summary.mean_max_abs_gate_leakage_a:.4e}"),
        ]
        for row, col, value in entries:
            self.summary_table.setItem(row, col, QTableWidgetItem(value))

    def set_theme(self, theme: str) -> None:
        self.heatmap.set_theme(theme)
        self.curve_plot.set_theme(theme)
        self.setStyleSheet(THEMES[theme]["qt_stylesheet"])
        if self.bundle_result:
            self.refresh_heatmap()

    def _current_batch(self) -> B1500BatchResult | None:
        if self.bundle_result is None:
            return None
        return self.bundle_result.results.get(self._current_mode())

    def _on_mode_changed(self, mode: str) -> None:
        self._sync_metric_options()
        if self.bundle_result and mode in self.bundle_result.results:
            self.refresh_heatmap()
            self._on_heatmap_selection_changed(self.heatmap.selected_devices())

    def _refresh_curve_panel(self) -> None:
        devices = self.heatmap.selected_devices()
        self.curve_plot.set_y_scale_mode(self.curve_y_scale_combo.currentText())
        self.curve_plot.render_devices(devices)

    def _copy_heatmap_image(self) -> None:
        self.heatmap.copy_image_to_clipboard()

    def _copy_curve_image(self) -> None:
        self.curve_plot.copy_image_to_clipboard()

    def _copy_heatmap_rawdata(self) -> None:
        QApplication.clipboard().setText(self.heatmap.rawdata_tsv())

    def _copy_curve_rawdata(self) -> None:
        QApplication.clipboard().setText(self.curve_plot.rawdata_tsv())

    def _set_click_multi_select_mode(self, enabled: bool) -> None:
        self.heatmap.set_click_multi_select_mode(enabled)
        self.click_multi_select_button.setText(
            "Disable Click Multi-Select" if enabled else "Enable Click Multi-Select"
        )
        self._apply_click_multi_select_button_style(self.click_multi_select_button, enabled)

    def _apply_click_multi_select_button_style(self, button: QPushButton, enabled: bool) -> None:
        if enabled:
            button.setStyleSheet(
                "QPushButton { background-color: #1f6f5f; color: #ffffff; border: 1px solid #34d399; font-weight: 600; }"
            )
        else:
            button.setStyleSheet("")

    def _sync_metric_options(self) -> None:
        mode = self._current_mode()
        metrics = MODE_METRICS[mode]
        current = self.metric_combo.currentText()
        self.metric_combo.blockSignals(True)
        self.metric_combo.clear()
        self.metric_combo.addItems(metrics)
        if current in metrics:
            self.metric_combo.setCurrentText(current)
        self.metric_combo.blockSignals(False)

    def _current_mode(self) -> str:
        return self.fixed_measurement_type or self.mode_combo.currentText()

    def _detail_lines(self, focus: B1500DeviceAnalysis) -> list[str]:
        lines = [
            f"Device: {focus.device_name}",
            f"Array Position: {_format_array_position(focus.metadata.row, focus.metadata.col)}",
            f"Mode: {MODE_LABELS.get(focus.measurement_type, focus.measurement_type)}",
            f"Curves: {focus.curve_count}",
            f"Points: {focus.point_count}",
            f"Max |Id|: {focus.max_abs_current_a}",
            f"Max |Ig|: {focus.max_abs_gate_leakage_a}",
        ]
        if focus.measurement_type == "transfer":
            lines.extend(
                [
                    f"Transfer On/Off: {focus.transfer_on_off_ratio}",
                    f"Transfer gm max: {focus.transfer_gm_max_s}",
                    f"Transfer SS (mV/dec): {focus.transfer_subthreshold_swing_mv_dec}",
                    f"Transfer Vth (Ioff): {focus.transfer_threshold_voltage_v}",
                    f"Transfer Vth (Iref): {focus.transfer_threshold_voltage_iref_v}",
                    f"Transfer Vth (gm max): {focus.transfer_threshold_voltage_gm_v}",
                    f"Transfer Vth (cross): {focus.transfer_threshold_voltage_cross_v}",
                    f"Transfer Von: {focus.transfer_turn_on_voltage_v}",
                    f"Transfer SS fit R2: {focus.transfer_ss_fit_r2}",
                    f"Transfer Von fit R2: {focus.transfer_von_fit_r2}",
                ]
            )
        else:
            lines.extend(
                [
                    f"Output Ron: {focus.output_on_resistance_ohm}",
                    f"Output gds: {focus.output_gds_sat_s}",
                    f"Output ro: {focus.output_ro_sat_ohm}",
                    f"Output lambda: {focus.output_lambda_1_v}",
                    f"Output Early V: {focus.output_early_voltage_v}",
                    f"Output knee V: {focus.output_knee_voltage_v}",
                    f"Output Id_sat: {focus.output_id_sat_a}",
                    f"Output tail Rout: {focus.output_sat_resistance_ohm}",
                ]
            )
        lines.extend(
            [
                "",
                f"CSV: {focus.csv_path}",
                f"Ig CSV: {focus.leakage_csv_path}",
                f"JSON: {focus.json_path}",
            ]
        )
        return lines

    def _remote_primary_paths(self, paths: list[str]) -> list[str]:
        if self.cloud_cache_root is None:
            return paths
        remote_paths: list[str] = []
        cache_root = self.cloud_cache_root.resolve()
        for raw_path in paths:
            if not raw_path:
                continue
            try:
                relative = Path(raw_path).resolve().relative_to(cache_root)
                remote_paths.append(relative.as_posix())
            except Exception:
                remote_paths.append(raw_path)
        return remote_paths
