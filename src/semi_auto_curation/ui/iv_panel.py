from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.colors import LogNorm, Normalize
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter
from matplotlib.widgets import RectangleSelector
from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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

from semi_auto_curation.analysis.iv import build_iv_database, run_iv_batch
from semi_auto_curation.models import IVAnalysisSettings, IVBatchResult, IVDeviceAnalysis
from semi_auto_curation.utils.logging import log_error, log_info, log_warn
from semi_auto_curation.utils.units import parse_si_number


matplotlib.use("QtAgg")

THEMES = {
    "light": {
        "figure": "#ffffff",
        "axes": "#ffffff",
        "text": "#111111",
        "grid": "#d0d0d0",
        "selection": "#ff8c00",
        "dummy_fill": "#c0c0c0",
        "dummy_cross": "#666666",
        "curve_colors": ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#8c564b", "#e377c2", "#ff7f0e", "#17becf"],
        "qt_stylesheet": "",
    },
    "dark": {
        "figure": "#171717",
        "axes": "#171717",
        "text": "#f0f0f0",
        "grid": "#444444",
        "selection": "#ffd166",
        "dummy_fill": "#707070",
        "dummy_cross": "#8c8c8c",
        "curve_colors": ["#61afef", "#e06c75", "#98c379", "#c678dd", "#d19a66", "#56b6c2", "#e5c07b", "#be5046"],
        "qt_stylesheet": """
            QWidget { background-color: #1e1e1e; color: #f0f0f0; }
            QLineEdit, QTextEdit, QListWidget, QTableWidget, QComboBox { background-color: #2a2a2a; color: #f0f0f0; }
            QPushButton { background-color: #333333; color: #f0f0f0; }
            QGroupBox { border: 1px solid #666666; margin-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
        """,
    },
}


@dataclass(slots=True)
class HeatmapRenderState:
    metric: str
    cmap: str
    level_min: float | None
    level_max: float | None
    theme: str
    scale_mode: str


class CacheBuildWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, settings: IVAnalysisSettings) -> None:
        super().__init__()
        self.settings = settings

    def run(self) -> None:
        try:
            result = build_iv_database(self.settings, progress_callback=self._emit_progress)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)

    def _emit_progress(self, value: int, message: str) -> None:
        self.progress.emit(value, message)


class IVBatchWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, settings: IVAnalysisSettings) -> None:
        super().__init__()
        self.settings = settings

    def run(self) -> None:
        try:
            result = run_iv_batch(self.settings, progress_callback=self._emit_progress)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)

    def _emit_progress(self, value: int, message: str) -> None:
        self.progress.emit(value, message)


class HeatmapCanvas(QWidget):
    selection_changed = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.figure = Figure(figsize=(6, 5), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.ax = self.figure.add_subplot(111)
        self.cbar = None
        self.image = None
        self.selector: RectangleSelector | None = None
        self.device_map: dict[tuple[int, int], IVDeviceAnalysis] = {}
        self.selected_coords: set[tuple[int, int]] = set()
        self.rows = 0
        self.cols = 0
        self.theme = "light"
        self.box_select_mode = False
        self.current_state: HeatmapRenderState | None = None
        self.canvas.mpl_connect("button_press_event", self._on_click)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)
        self._ensure_selector()

    def set_box_select_mode(self, enabled: bool) -> None:
        self.box_select_mode = enabled
        if self.selector:
            self.selector.set_active(enabled)

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        if self.current_state:
            self._style_axes()
            self.canvas.draw_idle()

    def render_batch(self, batch: IVBatchResult, state: HeatmapRenderState) -> None:
        self.current_state = state
        self.device_map = batch.device_map()
        self.rows = batch.summary.rows
        self.cols = batch.summary.cols
        values = np.full((self.rows, self.cols), np.nan, dtype=float)
        dummy_coords: list[tuple[int, int]] = []
        for (row, col), device in self.device_map.items():
            if device.is_dummy:
                dummy_coords.append((row, col))
            else:
                metric_value = device.metric_value(state.metric)
                if metric_value is not None and np.isfinite(metric_value):
                    values[row, col] = metric_value

        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        self.cbar = None
        self.image = None
        self.selector = None
        self._style_axes()
        cmap = matplotlib.colormaps[state.cmap].copy()
        cmap.set_bad(color=THEMES[state.theme]["axes"])
        vmin = state.level_min
        vmax = state.level_max
        if vmin is None or vmax is None:
            finite = values[np.isfinite(values)]
            if finite.size:
                if vmin is None:
                    vmin = float(np.nanmin(finite))
                if vmax is None:
                    vmax = float(np.nanmax(finite))
        norm = None
        if state.scale_mode == "log":
            positive = values[np.isfinite(values) & (values > 0)]
            if positive.size:
                if vmin is None:
                    vmin = float(np.nanmin(positive))
                if vmax is None:
                    vmax = float(np.nanmax(positive))
                vmin = max(vmin, np.finfo(float).tiny)
                vmax = max(vmax, vmin * 10.0)
                norm = LogNorm(vmin=vmin, vmax=vmax)
        else:
            norm = Normalize(vmin=vmin, vmax=vmax)
        self.image = self.ax.imshow(values, cmap=cmap, origin="lower", norm=norm, interpolation="nearest", aspect="equal")
        self.ax.set_xlabel("Column")
        self.ax.set_ylabel("Row")
        self.ax.set_title("Device Array Heatmap")
        self.ax.set_xlim(-0.5, self.cols - 0.5)
        self.ax.set_ylim(-0.5, self.rows - 0.5)
        self.ax.grid(color=THEMES[state.theme]["grid"], linewidth=0.3, alpha=0.5)

        for row, col in dummy_coords:
            rect = Rectangle((col - 0.5, row - 0.5), 1, 1, facecolor=THEMES[state.theme]["dummy_fill"], edgecolor="none", alpha=0.85)
            self.ax.add_patch(rect)
            cross_alpha = 0.72 if state.theme == "dark" else 1.0
            self.ax.plot(
                [col - 0.45, col + 0.45],
                [row - 0.45, row + 0.45],
                color=THEMES[state.theme]["dummy_cross"],
                linewidth=1.0,
                alpha=cross_alpha,
            )
            self.ax.plot(
                [col - 0.45, col + 0.45],
                [row + 0.45, row - 0.45],
                color=THEMES[state.theme]["dummy_cross"],
                linewidth=1.0,
                alpha=cross_alpha,
            )

        if self.image is not None:
            self.cbar = self.figure.colorbar(self.image, ax=self.ax)
            self.cbar.ax.tick_params(colors=THEMES[state.theme]["text"])
            self.cbar.outline.set_edgecolor(THEMES[state.theme]["text"])
            self.cbar.set_label(state.metric, color=THEMES[state.theme]["text"])

        self._draw_selection_overlay()
        self._ensure_selector()
        self.canvas.draw_idle()

    def clear_selection(self) -> None:
        self.selected_coords.clear()
        self._draw_selection_overlay()
        self.selection_changed.emit(self.selected_devices())

    def selected_devices(self) -> list[IVDeviceAnalysis]:
        return [self.device_map[coord] for coord in sorted(self.selected_coords) if coord in self.device_map]

    def _style_axes(self) -> None:
        theme_cfg = THEMES[self.current_state.theme if self.current_state else self.theme]
        self.figure.patch.set_facecolor(theme_cfg["figure"])
        self.ax.set_facecolor(theme_cfg["axes"])
        self.ax.tick_params(colors=theme_cfg["text"])
        for spine in self.ax.spines.values():
            spine.set_color(theme_cfg["text"])
        self.ax.xaxis.label.set_color(theme_cfg["text"])
        self.ax.yaxis.label.set_color(theme_cfg["text"])
        self.ax.title.set_color(theme_cfg["text"])

    def _ensure_selector(self) -> None:
        if self.selector is None:
            self.selector = RectangleSelector(
                self.ax,
                self._on_rectangle_select,
                useblit=False,
                button=[1],
                minspanx=0.5,
                minspany=0.5,
                spancoords="data",
                interactive=False,
            )
        self.selector.set_active(self.box_select_mode)

    def _toolbar_busy(self) -> bool:
        return bool(getattr(self.toolbar, "mode", ""))

    def _on_click(self, event) -> None:
        if self._toolbar_busy() or self.box_select_mode:
            return
        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            return
        coord = (int(round(event.ydata)), int(round(event.xdata)))
        if coord not in self.device_map:
            return
        additive = event.key == "control"
        if additive:
            if coord in self.selected_coords:
                self.selected_coords.remove(coord)
            else:
                self.selected_coords.add(coord)
        else:
            self.selected_coords = {coord}
        self._draw_selection_overlay()
        self.selection_changed.emit(self.selected_devices())

    def _on_rectangle_select(self, eclick, erelease) -> None:
        if self._toolbar_busy() or not self.box_select_mode:
            return
        if None in (eclick.xdata, eclick.ydata, erelease.xdata, erelease.ydata):
            return
        x0, x1 = sorted([eclick.xdata, erelease.xdata])
        y0, y1 = sorted([eclick.ydata, erelease.ydata])
        coords: list[tuple[int, int]] = []
        for row in range(int(np.floor(y0)), int(np.ceil(y1)) + 1):
            for col in range(int(np.floor(x0)), int(np.ceil(x1)) + 1):
                coord = (row, col)
                if coord in self.device_map:
                    coords.append(coord)
        additive = eclick.key == "control" or erelease.key == "control"
        if not additive:
            self.selected_coords = set(coords)
        else:
            for coord in coords:
                if coord in self.selected_coords:
                    self.selected_coords.remove(coord)
                else:
                    self.selected_coords.add(coord)
        self._draw_selection_overlay()
        self.selection_changed.emit(self.selected_devices())

    def _draw_selection_overlay(self) -> None:
        for patch in list(self.ax.patches):
            if getattr(patch, "_selection_overlay", False):
                patch.remove()
        for coord in sorted(self.selected_coords):
            row, col = coord
            rect = Rectangle(
                (col - 0.5, row - 0.5),
                1,
                1,
                fill=False,
                linewidth=2.0,
                edgecolor=THEMES[self.current_state.theme if self.current_state else self.theme]["selection"],
            )
            rect._selection_overlay = True
            self.ax.add_patch(rect)
        self.canvas.draw_idle()


class IVCurveCanvas(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.figure = Figure(figsize=(6, 5), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.ax = self.figure.add_subplot(111)
        self.theme = "light"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)
        self._style_axes()

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        self._style_axes()
        self.canvas.draw_idle()

    def render_devices(self, devices: list[IVDeviceAnalysis], fit_mode: str) -> None:
        self.ax.clear()
        self._style_axes()
        colors = THEMES[self.theme]["curve_colors"]
        for idx, device in enumerate(devices):
            x = [point.voltage_v for point in device.points]
            y = [point.current_a for point in device.points]
            color = colors[idx % len(colors)]
            self.ax.plot(x, y, color=color, linewidth=1.6, marker=("o" if len(devices) <= 3 else None), markersize=3.5, label=device.device_name)
            if fit_mode == "Linear Fit" and device.fit_slope_a_per_v is not None and device.fit_intercept_a is not None:
                fit_x = np.array([device.fit_voltage_min, device.fit_voltage_max], dtype=float)
                fit_y = device.fit_slope_a_per_v * fit_x + device.fit_intercept_a
                self.ax.plot(fit_x, fit_y, color=color, linewidth=1.3, linestyle="--", alpha=0.9, label=f"{device.device_name} fit")
        self.ax.set_xlabel("Voltage (V)")
        self.ax.set_ylabel("Current (A)")
        self.ax.set_title("Selected IV Curves")
        self.ax.grid(color=THEMES[self.theme]["grid"], linewidth=0.5, alpha=0.5)
        self.ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: _format_engineering(value, "A")))
        self.ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _pos: _format_engineering(value, "V")))
        if devices:
            self.ax.legend(loc="best", fontsize=8)
        self.canvas.draw_idle()

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


class IVAnalysisPanel(QWidget):
    title = "IV"
    status_changed = Signal(str)
    progress_changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.batch_result: IVBatchResult | None = None
        self.worker_thread: QThread | None = None
        self.worker: IVBatchWorker | None = None
        self.cache_thread: QThread | None = None
        self.cache_worker: CacheBuildWorker | None = None
        self._auto_fit_range: tuple[float | None, float | None] = (None, None)
        self._auto_dummy_range: tuple[float | None, float | None] = (None, None)
        self.source_edit = QLineEdit(str(Path.cwd() / "rawdata" / "iv"))
        self.output_edit = QLineEdit(str(Path.cwd() / "output"))
        self.fit_min_edit = QLineEdit("")
        self.fit_max_edit = QLineEdit("")
        self.dummy_r_min_edit = QLineEdit("")
        self.dummy_r_max_edit = QLineEdit("")
        self.dummy_r2_edit = QLineEdit("0.0")
        self.level_min_edit = QLineEdit("")
        self.level_max_edit = QLineEdit("")
        self.metric_combo = QComboBox()
        self.metric_combo.addItems(["abs_fit_resistance_ohm", "fit_resistance_ohm", "fit_r2", "max_abs_current_a"])
        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(["viridis", "plasma", "inferno", "magma", "cividis", "gray"])
        self.scale_mode_combo = QComboBox()
        self.scale_mode_combo.addItems(["linear", "log"])
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["light", "dark"])
        self.theme_combo.setCurrentText("dark")
        self.fit_curve_combo = QComboBox()
        self.fit_curve_combo.addItems(["Linear Fit", "Raw Only"])
        self.box_select_check = QCheckBox("Box Select Mode")
        self.cache_label = QLabel("Cache DB: pending")
        self.selected_list = QListWidget()
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.summary_table = QTableWidget(2, 4)
        self.summary_table.setVerticalHeaderLabels(["Count", "Mean"])
        self.summary_table.setHorizontalHeaderLabels(["Devices", "Dummy", "Fit |R| (Ohm)", "Fit R^2"])
        self.summary_table.verticalHeader().setVisible(True)
        self.summary_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.summary_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.summary_table.setFixedHeight(110)
        self.heatmap = HeatmapCanvas()
        self.iv_plot = IVCurveCanvas()
        self._build_ui()
        self.set_theme("dark")

    def build_toolbar_actions(self) -> list[QAction]:
        build_database = QAction("Load and Build Database", self)
        build_database.triggered.connect(self.load_and_build_database)
        analyze = QAction("Analyze IV", self)
        analyze.triggered.connect(self.run_analysis)
        clear = QAction("Clear Selection", self)
        clear.triggered.connect(self.clear_selection)
        return [build_database, analyze, clear]

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self._build_source_box())
        left_layout.addWidget(self._build_fit_box())
        left_layout.addWidget(self._build_heatmap_box())
        left_layout.addWidget(self._build_selection_box(), 1)

        right_splitter = QSplitter(Qt.Horizontal)
        right_splitter.addWidget(self._build_heatmap_panel())
        right_splitter.addWidget(self._build_curve_panel())
        right_splitter.setSizes([620, 620])

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right_splitter)
        splitter.setSizes([340, 980])
        root.addWidget(splitter)

        self.metric_combo.currentTextChanged.connect(self.refresh_heatmap)
        self.cmap_combo.currentTextChanged.connect(self.refresh_heatmap)
        self.scale_mode_combo.currentTextChanged.connect(self.refresh_heatmap)
        self.theme_combo.currentTextChanged.connect(self.set_theme)
        self.fit_curve_combo.currentTextChanged.connect(self._refresh_curve_panel)
        self.box_select_check.toggled.connect(self.heatmap.set_box_select_mode)
        self.heatmap.selection_changed.connect(self._on_heatmap_selection_changed)

    def _build_source_box(self) -> QWidget:
        box = QGroupBox("IV Source")
        layout = QFormLayout(box)
        layout.addRow("Source Folder", self._browse_row(self.source_edit, self._choose_source))
        layout.addRow("Output Folder", self._browse_row(self.output_edit, self._choose_output))
        build_button = QPushButton("Load and Build Database")
        build_button.clicked.connect(self.load_and_build_database)
        layout.addRow(build_button)
        return box

    def _build_fit_box(self) -> QWidget:
        box = QGroupBox("Linear Fit Settings")
        layout = QFormLayout(box)
        layout.addRow("Fit Voltage Min", self.fit_min_edit)
        layout.addRow("Fit Voltage Max", self.fit_max_edit)
        layout.addRow("Dummy Min |R|", self.dummy_r_min_edit)
        layout.addRow("Dummy Max |R|", self.dummy_r_max_edit)
        layout.addRow("Dummy Min R^2", self.dummy_r2_edit)
        analyze_button = QPushButton("Analyze Data")
        analyze_button.clicked.connect(self.run_analysis)
        layout.addRow(analyze_button)
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
        layout.addRow(self.box_select_check)
        apply_button = QPushButton("Apply Heatmap Settings")
        apply_button.clicked.connect(self.refresh_heatmap)
        layout.addRow(apply_button)
        return box

    def _build_selection_box(self) -> QWidget:
        box = QGroupBox("Selected Devices")
        layout = QVBoxLayout(box)
        layout.addWidget(self.selected_list, 2)
        layout.addWidget(self.cache_label)
        clear_button = QPushButton("Clear Selection")
        clear_button.clicked.connect(self.clear_selection)
        layout.addWidget(clear_button)
        layout.addWidget(self.detail_text, 3)
        return box

    def _build_heatmap_panel(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(self.summary_table)
        layout.addWidget(self.heatmap, 1)
        return container

    def _build_curve_panel(self) -> QWidget:
        box = QGroupBox("IV Curves")
        layout = QVBoxLayout(box)
        top = QHBoxLayout()
        top.addWidget(QLabel("Fit Curve"))
        top.addWidget(self.fit_curve_combo)
        top.addStretch(1)
        layout.addLayout(top)
        layout.addWidget(self.iv_plot)
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
        folder = QFileDialog.getExistingDirectory(self, "Select IV Source Folder", self.source_edit.text())
        if folder:
            self.source_edit.setText(folder)

    def _choose_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)

    def _current_database_settings(self) -> IVAnalysisSettings:
        cache_db_path = Path(self.output_edit.text().strip()) / ".cache" / "iv_cache.sqlite3"
        return IVAnalysisSettings(
            source_dir=Path(self.source_edit.text().strip()),
            output_dir=Path(self.output_edit.text().strip()),
            cache_db_path=cache_db_path,
        )

    def _current_settings(self) -> IVAnalysisSettings:
        cache_db_path = Path(self.output_edit.text().strip()) / ".cache" / "iv_cache.sqlite3"
        fit_min = _required_float(self.fit_min_edit.text(), self._auto_fit_range[0], "Fit Voltage Min")
        fit_max = _required_float(self.fit_max_edit.text(), self._auto_fit_range[1], "Fit Voltage Max")
        return IVAnalysisSettings(
            source_dir=Path(self.source_edit.text().strip()),
            output_dir=Path(self.output_edit.text().strip()),
            cache_db_path=cache_db_path,
            fit_voltage_min=fit_min,
            fit_voltage_max=fit_max,
            dummy_min_resistance_ohm=_optional_float(self.dummy_r_min_edit.text()),
            dummy_max_resistance_ohm=_optional_float(self.dummy_r_max_edit.text()),
            dummy_min_r2=parse_si_number(self.dummy_r2_edit.text().strip()),
            heatmap_metric=self.metric_combo.currentText(),
        )

    def load_and_build_database(self) -> None:
        try:
            settings = self._current_database_settings()
        except ValueError as exc:
            log_error(f"Invalid IV database build input: {exc}")
            QMessageBox.critical(self, "Invalid Input", str(exc))
            return
        log_info(f"GUI IV database build requested. source={settings.source_dir} output={settings.output_dir} cache={settings.cache_db_path}")
        self.status_changed.emit("Loading...")
        self.progress_changed.emit(0)
        self.cache_thread = QThread(self)
        self.cache_worker = CacheBuildWorker(settings)
        self.cache_worker.moveToThread(self.cache_thread)
        self.cache_thread.started.connect(self.cache_worker.run)
        self.cache_worker.finished.connect(self._database_build_finished)
        self.cache_worker.failed.connect(self._database_build_failed)
        self.cache_worker.progress.connect(self._on_worker_progress)
        self.cache_worker.finished.connect(self.cache_thread.quit)
        self.cache_worker.failed.connect(self.cache_thread.quit)
        self.cache_thread.finished.connect(self.cache_worker.deleteLater)
        self.cache_thread.finished.connect(self.cache_thread.deleteLater)
        self.cache_thread.start()

    def run_analysis(self) -> None:
        try:
            settings = self._current_settings()
        except ValueError as exc:
            log_error(f"Invalid IV analysis input: {exc}")
            QMessageBox.critical(self, "Invalid Input", str(exc))
            return
        log_info(f"GUI IV analysis requested. source={settings.source_dir} output={settings.output_dir} cache={settings.cache_db_path}")
        self.status_changed.emit("Running...")
        self.progress_changed.emit(0)
        self.worker_thread = QThread(self)
        self.worker = IVBatchWorker(settings)
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

    def _database_build_finished(self, result: object) -> None:
        cache_path, count, voltage_min, voltage_max = result
        self.cache_label.setText(f"Cache DB: {cache_path}")
        old_fit_min, old_fit_max = self._auto_fit_range
        self._set_if_auto(self.fit_min_edit, voltage_min, old_fit_min)
        self._set_if_auto(self.fit_max_edit, voltage_max, old_fit_max)
        self._auto_fit_range = (voltage_min, voltage_max)
        log_info(f"GUI IV database build completed. cached_measurements={count}")
        self.status_changed.emit("Finish")
        self.progress_changed.emit(100)

    def _database_build_failed(self, message: str) -> None:
        log_error(f"GUI IV database build failed: {message}")
        QMessageBox.critical(self, "IV Database Build Failed", message)
        self.status_changed.emit("Busy...")
        self.progress_changed.emit(0)

    def _analysis_finished(self, result: object) -> None:
        if not isinstance(result, IVBatchResult):
            self._analysis_failed("Unexpected result payload.")
            return
        self.batch_result = result
        self._populate_summary(result)
        cache_path = result.settings.cache_db_path or (result.settings.output_dir / ".cache" / "iv_cache.sqlite3")
        self.cache_label.setText(f"Cache DB: {cache_path}")
        finite_resistances = [
            device.abs_fit_resistance_ohm
            for device in result.devices
            if device.abs_fit_resistance_ohm is not None and np.isfinite(device.abs_fit_resistance_ohm)
        ]
        if finite_resistances:
            dummy_min = min(finite_resistances)
            dummy_max = max(finite_resistances)
            old_dummy_min, old_dummy_max = self._auto_dummy_range
            self._set_if_auto(self.dummy_r_min_edit, dummy_min, old_dummy_min, scientific=True)
            self._set_if_auto(self.dummy_r_max_edit, dummy_max, old_dummy_max, scientific=True)
            self._auto_dummy_range = (dummy_min, dummy_max)
        log_info(f"GUI IV analysis completed. total_devices={result.summary.total_devices} valid_devices={result.summary.valid_devices} dummy_devices={result.summary.dummy_devices}")
        self.status_changed.emit("Finish")
        self.progress_changed.emit(100)
        self.refresh_heatmap()

    def _analysis_failed(self, message: str) -> None:
        log_error(f"GUI IV analysis failed: {message}")
        QMessageBox.critical(self, "IV Analysis Failed", message)
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
        if not self.batch_result:
            log_warn("Heatmap refresh requested before analysis result was available.")
            return
        self.status_changed.emit("Busy...")
        self.progress_changed.emit(5)
        state = HeatmapRenderState(
            metric=self.metric_combo.currentText(),
            cmap=self.cmap_combo.currentText(),
            level_min=_optional_float(self.level_min_edit.text()),
            level_max=_optional_float(self.level_max_edit.text()),
            theme=self.theme_combo.currentText(),
            scale_mode=self.scale_mode_combo.currentText(),
        )
        self.heatmap.render_batch(self.batch_result, state)
        self.status_changed.emit("Finish")
        self.progress_changed.emit(100)

    def clear_selection(self) -> None:
        self.heatmap.clear_selection()

    def _on_heatmap_selection_changed(self, devices: list[IVDeviceAnalysis]) -> None:
        self.selected_list.clear()
        for device in devices:
            text = f"{device.device_name}  (r={device.metadata.row}, c={device.metadata.col})"
            self.selected_list.addItem(QListWidgetItem(text))
        self.iv_plot.render_devices(devices, self.fit_curve_combo.currentText())
        if devices:
            focus = devices[-1]
            self.detail_text.setPlainText(
                "\n".join(
                    [
                        f"Device: {focus.device_name}",
                        f"Row/Col: {focus.metadata.row}, {focus.metadata.col}",
                        f"Fit Window: {focus.fit_voltage_min} to {focus.fit_voltage_max} V",
                        f"Fit Points: {focus.fit_point_count}",
                        f"Fit Resistance: {focus.fit_resistance_ohm}",
                        f"Abs Fit Resistance: {focus.abs_fit_resistance_ohm}",
                        f"Fit R^2: {focus.fit_r2}",
                        f"Dummy: {focus.is_dummy}",
                        f"Dummy Reason: {focus.dummy_reason}",
                        "",
                        f"CSV: {focus.csv_path}",
                        f"JSON: {focus.json_path}",
                    ]
                )
            )
        else:
            self.detail_text.clear()

    def _populate_summary(self, batch: IVBatchResult) -> None:
        entries = [
            (0, 0, str(batch.summary.total_devices)),
            (0, 1, str(batch.summary.dummy_devices)),
            (0, 2, "--" if batch.summary.mean_abs_fit_resistance_ohm is None else f"{batch.summary.mean_abs_fit_resistance_ohm:.4e}"),
            (0, 3, "--" if batch.summary.mean_fit_r2 is None else f"{batch.summary.mean_fit_r2:.4f}"),
            (1, 0, f"{batch.summary.rows} x {batch.summary.cols}"),
            (1, 1, str(batch.summary.valid_devices)),
            (1, 2, f"{batch.settings.fit_voltage_min} to {batch.settings.fit_voltage_max}"),
            (1, 3, self.metric_combo.currentText()),
        ]
        for row, col, value in entries:
            self.summary_table.setItem(row, col, QTableWidgetItem(value))

    def set_theme(self, theme: str) -> None:
        self.heatmap.set_theme(theme)
        self.iv_plot.set_theme(theme)
        self.setStyleSheet(THEMES[theme]["qt_stylesheet"])
        self.status_changed.emit("Busy...")
        if self.batch_result:
            self.refresh_heatmap()
            self.iv_plot.render_devices(self.heatmap.selected_devices(), self.fit_curve_combo.currentText())

    def fit_setting_descriptions(self) -> dict[str, str]:
        return {
            "Fit Voltage Min": "Lower voltage bound of the linear fitting window.",
            "Fit Voltage Max": "Upper voltage bound of the linear fitting window.",
            "Dummy Min |R|": "Marks devices as dummy when absolute fitted resistance is below this threshold.",
            "Dummy Max |R|": "Marks devices as dummy when absolute fitted resistance is above this threshold.",
            "Dummy Min R^2": "Marks devices as dummy when linear fit R^2 is below this threshold.",
        }

    def metric_descriptions(self) -> dict[str, str]:
        return {
            "abs_fit_resistance_ohm": "Absolute value of the fitted linear resistance.",
            "fit_resistance_ohm": "Signed fitted linear resistance.",
            "fit_r2": "Goodness of fit for the selected fitting window.",
            "max_abs_current_a": "Maximum absolute current across the raw IV sweep.",
        }

    def _refresh_curve_panel(self) -> None:
        self.iv_plot.render_devices(self.heatmap.selected_devices(), self.fit_curve_combo.currentText())

    def _set_if_auto(self, field: QLineEdit, value: float | None, previous_auto: float | None, scientific: bool = False) -> None:
        if value is None:
            return
        current = field.text().strip()
        if not current or (previous_auto is not None and current in {f"{previous_auto}", f"{previous_auto:.6e}", _format_number(previous_auto)}):
            field.setText(f"{value:.6e}" if scientific else _format_number(value))


def _optional_float(value: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    return parse_si_number(text)


def _required_float(value: str, fallback: float | None, label: str) -> float:
    text = value.strip()
    if text:
        return parse_si_number(text)
    if fallback is not None:
        return fallback
    raise ValueError(f"{label} is empty. Please build the database first or enter a value manually.")


def _format_number(value: float) -> str:
    text = f"{value:.6g}"
    return text


def _format_engineering(value: float, suffix: str) -> str:
    if value == 0:
        return f"0 {suffix}".strip()
    abs_value = abs(value)
    prefixes = [
        (1e-12, "p"),
        (1e-9, "n"),
        (1e-6, "u"),
        (1e-3, "m"),
        (1, ""),
        (1e3, "k"),
        (1e6, "M"),
        (1e9, "G"),
    ]
    chosen_scale = 1
    chosen_prefix = ""
    for scale, prefix in prefixes:
        if abs_value < scale * 1000:
            chosen_scale = scale
            chosen_prefix = prefix
            break
    scaled = value / chosen_scale
    if abs(scaled) >= 100:
        text = f"{scaled:.0f}"
    elif abs(scaled) >= 10:
        text = f"{scaled:.1f}"
    else:
        text = f"{scaled:.2f}"
    text = text.rstrip("0").rstrip(".")
    return f"{text}{chosen_prefix}{suffix}"
