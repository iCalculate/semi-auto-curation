from __future__ import annotations

import math
from pathlib import Path

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from semi_auto_curation.data.preview_loader import (
    PreviewDataset,
    curve_groups,
    heatmap_values,
    load_preview_dataset,
)
from semi_auto_curation.settings import default_source_browse_directory
from semi_auto_curation.ui.iv_panel import THEMES, HeatmapCanvas, HeatmapRenderState, _optional_float


_FEATURES = {
    "first": "First point",
    "last": "Last point",
    "min": "Minimum",
    "max": "Maximum",
    "max_abs": "Maximum |value|",
    "mean": "Mean",
    "median": "Median",
}

_PLOT_LAYOUTS = {
    "Side by side": Qt.Horizontal,
    "Stacked": Qt.Vertical,
}


class _PreviewLoaderWorker(QObject):
    finished = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, request_id: int, source_dir: Path, hp6614c_only: bool) -> None:
        super().__init__()
        self.request_id = request_id
        self.source_dir = source_dir
        self.hp6614c_only = hp6614c_only

    def run(self) -> None:
        try:
            dataset = load_preview_dataset(self.source_dir, hp6614c_only=self.hp6614c_only)
            self.finished.emit(self.request_id, dataset)
        except Exception as exc:
            self.failed.emit(self.request_id, str(exc))


class PreviewCurveCanvas(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.figure = Figure(figsize=(7, 4), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.ax = self.figure.add_subplot(111)
        self.theme = "dark"
        self.dataset: PreviewDataset | None = None
        self.device_names: list[str] = []
        self.x_channel: str | None = None
        self.y_channels: list[str] = []
        self.x_scale = "linear"
        self.y_scale = "linear"
        self.show_legend = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)

    def render(self) -> None:
        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        cfg = THEMES[self.theme]
        self.figure.patch.set_facecolor(cfg["figure"])
        self.ax.set_facecolor(cfg["axes"])
        plotted = 0
        if self.dataset is not None and self.x_channel and self.y_channels:
            for device_name in self.device_names:
                device = self.dataset.devices.get(device_name)
                if device is None:
                    continue
                for group_label, records in curve_groups(device).items():
                    for channel in self.y_channels:
                        points: list[tuple[float, float]] = []
                        for record in records:
                            try:
                                x = float(record.get(self.x_channel))
                                y = float(record.get(channel))
                            except (TypeError, ValueError):
                                continue
                            if not (math.isfinite(x) and math.isfinite(y)):
                                continue
                            if self.x_scale == "log" and x <= 0:
                                continue
                            if self.y_scale == "log":
                                y = abs(y)
                                if y <= 0:
                                    continue
                            points.append((x, y))
                        if not points:
                            continue
                        label = " · ".join(part for part in (device_name, channel, group_label) if part)
                        self.ax.plot([p[0] for p in points], [p[1] for p in points], marker=".", linewidth=1.1, markersize=3, label=label)
                        plotted += 1
        self.ax.set_xscale(self.x_scale)
        self.ax.set_yscale(self.y_scale)
        self.ax.set_xlabel(self.x_channel or "X")
        y_label = ", ".join(self.y_channels) or "Y"
        self.ax.set_ylabel(f"|{y_label}|" if self.y_scale == "log" else y_label)
        self.ax.set_title("Selected data" if plotted else "Select one or more heatmap cells")
        self.ax.grid(True, color=cfg["grid"], linewidth=0.5, linestyle="--", alpha=0.5)
        for spine in self.ax.spines.values():
            spine.set_color(cfg.get("spine", cfg["text"]))
        self.ax.tick_params(colors=cfg["text"], labelsize=7)
        self.ax.xaxis.label.set_color(cfg["text"])
        self.ax.yaxis.label.set_color(cfg["text"])
        self.ax.title.set_color(cfg["text"])
        if self.show_legend and plotted:
            legend = self.ax.legend(fontsize=7, loc="best")
            legend.get_frame().set_alpha(0.75)
        self.canvas.draw_idle()

    def rawdata_tsv(self) -> str:
        lines = ["device\tcurve\tx_channel\tx\ty_channel\ty"]
        if self.dataset is None or not self.x_channel:
            return "\n".join(lines)
        for name in self.device_names:
            device = self.dataset.devices.get(name)
            if device is None:
                continue
            for group_label, records in curve_groups(device).items():
                for channel in self.y_channels:
                    for record in records:
                        try:
                            x = float(record.get(self.x_channel))
                            y = float(record.get(channel))
                        except (TypeError, ValueError):
                            continue
                        lines.append(f"{name}\t{group_label}\t{self.x_channel}\t{x:.12g}\t{channel}\t{y:.12g}")
        return "\n".join(lines)


class DeviceInformationPanel(QWidget):
    """Tabular metadata plus image thumbnails for the heatmap selection."""

    _HEADERS = ["Device", "Row", "Col", "GDS U", "GDS V", "Stage X (µm)", "Stage Y (µm)", "Data", "Images"]

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        self.title = QLabel("Device Information — no selection")
        self.table = QTableWidget(0, len(self._HEADERS))
        self.table.setHorizontalHeaderLabels(self._HEADERS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(105)

        self.gallery_content = QWidget()
        self.gallery_layout = QHBoxLayout(self.gallery_content)
        self.gallery_layout.setContentsMargins(2, 2, 2, 2)
        self.gallery_layout.setSpacing(8)
        self.gallery_layout.addStretch()
        self.gallery = QScrollArea()
        self.gallery.setWidget(self.gallery_content)
        self.gallery.setWidgetResizable(True)
        self.gallery.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.gallery.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.gallery.setMinimumHeight(175)

        root.addWidget(self.title)
        root.addWidget(self.table)
        root.addWidget(self.gallery)

    def show_devices(self, dataset: PreviewDataset | None, device_names: list[str]) -> None:
        devices = [dataset.devices[name] for name in device_names if dataset is not None and name in dataset.devices]
        self.title.setText(f"Device Information — {len(devices)} selected" if devices else "Device Information — no selection")
        self.table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            values = [
                device.device_name,
                str(device.row),
                str(device.col),
                _format_optional(device.gds_u),
                _format_optional(device.gds_v),
                _format_optional(device.stage_x_um),
                _format_optional(device.stage_y_um),
                str(len(device.source_files)),
                str(len(device.image_files)),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 7:
                    item.setToolTip("\n".join(str(path) for path in device.source_files))
                elif column == 8:
                    item.setToolTip("\n".join(str(path) for path in device.image_files))
                self.table.setItem(row, column, item)
        self._show_images(devices)

    def _show_images(self, devices: list) -> None:
        while self.gallery_layout.count():
            item = self.gallery_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        images: list[tuple[str, Path]] = []
        if len(devices) == 1:
            images = [(devices[0].device_name, path) for path in devices[0].image_files[:8]]
        else:
            images = [(device.device_name, device.image_files[0]) for device in devices if device.image_files]
        if not images:
            empty = QLabel("No matching device images")
            empty.setAlignment(Qt.AlignCenter)
            self.gallery_layout.addWidget(empty, 1)
            return
        for device_name, path in images[:20]:
            tile = QWidget()
            tile.setFixedWidth(190)
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(2, 2, 2, 2)
            label = QLabel(f"{device_name} — {path.name}")
            label.setAlignment(Qt.AlignCenter)
            label.setToolTip(str(path))
            image = QLabel()
            image.setAlignment(Qt.AlignCenter)
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                image.setText("Cannot load image")
            else:
                image.setPixmap(pixmap.scaled(180, 135, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            tile_layout.addWidget(label)
            tile_layout.addWidget(image, 1)
            self.gallery_layout.addWidget(tile)
        self.gallery_layout.addStretch()


def _format_optional(value: float | None) -> str:
    return "—" if value is None or not math.isfinite(value) else f"{value:.8g}"


class DataPreviewPanel(QWidget):
    title = "Data Preview"
    status_changed = Signal(str)
    progress_changed = Signal(int)

    def __init__(self, *, hp6614c_only: bool = False) -> None:
        super().__init__()
        self.hp6614c_only = hp6614c_only
        self.panel_label = "HP6614C-Trans" if hp6614c_only else "Data Preview"
        self.dataset: PreviewDataset | None = None
        self._thread: QThread | None = None
        self._worker: _PreviewLoaderWorker | None = None
        self._load_generation = 0
        self._load_jobs: dict[int, tuple[QThread, _PreviewLoaderWorker]] = {}

        default_folder = Path.cwd() / "rawdata" / ("hp6614c" if hp6614c_only else "")
        self.source_edit = QLineEdit(str(default_folder))
        self.channel_combo = QComboBox()
        self.feature_combo = QComboBox()
        for key, label in _FEATURES.items():
            self.feature_combo.addItem(label, key)
        self.x_combo = QComboBox()
        self.y_list = QListWidget()
        self.y_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.x_scale_combo = QComboBox()
        self.x_scale_combo.addItems(["linear", "log", "symlog"])
        self.y_scale_combo = QComboBox()
        self.y_scale_combo.addItems(["linear", "log", "symlog"])
        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(["viridis", "plasma", "inferno", "magma", "cividis", "coolwarm", "gray"])
        self.heatmap_scale_combo = QComboBox()
        self.heatmap_scale_combo.addItems(["linear", "log"])
        self.level_min_edit = QLineEdit()
        self.level_max_edit = QLineEdit()
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["light", "dark"])
        self.theme_combo.setCurrentText("dark")
        self.plot_layout_combo = QComboBox()
        self.plot_layout_combo.addItems(list(_PLOT_LAYOUTS))
        self.multi_select_check = QCheckBox("Click toggles multiple devices")
        self.box_select_check = QCheckBox("Box select mode")
        self.legend_check = QCheckBox("Show curve legend")
        self.legend_check.setChecked(True)
        self.summary_label = QLabel("No data loaded")
        self.summary_label.setWordWrap(True)

        self.heatmap = HeatmapCanvas()
        self.curve_plot = PreviewCurveCanvas()
        self.device_information = DeviceInformationPanel()
        self._build_ui()
        self._connect_signals()
        self.set_theme("dark")

    def build_toolbar_actions(self) -> list[QAction]:
        load_action = QAction("Load Data", self)
        load_action.triggered.connect(self.run_analysis)
        clear_action = QAction("Clear Selection", self)
        clear_action.triggered.connect(self.clear_selection)
        return [load_action, clear_action]

    def run_analysis(self) -> None:
        source = Path(self.source_edit.text().strip())
        if not source.is_dir():
            QMessageBox.warning(self, "Missing Source", "Please select a valid data folder.")
            return
        self.status_changed.emit("Loading preview data…")
        self.progress_changed.emit(5)
        self._load_generation += 1
        request_id = self._load_generation
        thread = QThread(self)
        worker = _PreviewLoaderWorker(request_id, source, self.hp6614c_only)
        self._thread = thread
        self._worker = worker
        self._load_jobs[request_id] = (thread, worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._loaded)
        worker.failed.connect(self._failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda request_id=request_id: self._load_finished(request_id))
        thread.start()

    def closeEvent(self, event) -> None:
        running_jobs = [thread for thread, _worker in self._load_jobs.values() if thread.isRunning()]
        if running_jobs:
            event.ignore()
            self.status_changed.emit("Wait for preview data loading to finish before closing this workspace.")
            return
        super().closeEvent(event)

    def refresh_heatmap(self) -> None:
        if self.dataset is None or not self.channel_combo.currentText():
            return
        channel = self.channel_combo.currentText()
        feature = self.feature_combo.currentData() or "max_abs"
        state = HeatmapRenderState(
            metric=f"{channel} · {feature}",
            cmap=self.cmap_combo.currentText(),
            level_min=_optional_float(self.level_min_edit.text()),
            level_max=_optional_float(self.level_max_edit.text()),
            theme=self.theme_combo.currentText(),
            scale_mode=self.heatmap_scale_combo.currentText(),
        )
        self.heatmap.render_value_grid(
            heatmap_values(self.dataset, channel, feature),
            self.dataset.positions,
            state,
            title=f"{channel} — {_FEATURES.get(feature, feature)}",
        )

    def clear_selection(self) -> None:
        self.heatmap.clear_selection()

    def set_theme(self, theme: str) -> None:
        if theme not in THEMES:
            return
        self.setStyleSheet(THEMES[theme]["qt_stylesheet"])
        self.heatmap.set_theme(theme)
        self.curve_plot.theme = theme
        self.curve_plot.render()
        if self.dataset is not None:
            self.refresh_heatmap()

    def _choose_source(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select data source folder", default_source_browse_directory())
        if folder:
            self.source_edit.setText(folder)

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)

        source_box = QGroupBox("Data Source")
        source_form = QFormLayout(source_box)
        browse_row = QWidget()
        browse_layout = QHBoxLayout(browse_row)
        browse_layout.setContentsMargins(0, 0, 0, 0)
        browse_layout.addWidget(self.source_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._choose_source)
        browse_layout.addWidget(browse)
        source_form.addRow("Folder", browse_row)
        load = QPushButton("Load Data")
        load.clicked.connect(self.run_analysis)
        source_form.addRow(load)
        source_form.addRow(self.summary_label)
        left_layout.addWidget(source_box)

        heatmap_box = QGroupBox("Heatmap")
        heatmap_form = QFormLayout(heatmap_box)
        heatmap_form.addRow("Channel / parameter", self.channel_combo)
        heatmap_form.addRow("Feature", self.feature_combo)
        heatmap_form.addRow("Colormap", self.cmap_combo)
        heatmap_form.addRow("Color scale", self.heatmap_scale_combo)
        heatmap_form.addRow("Color min", self.level_min_edit)
        heatmap_form.addRow("Color max", self.level_max_edit)
        heatmap_form.addRow(self.multi_select_check)
        heatmap_form.addRow(self.box_select_check)
        apply_button = QPushButton("Apply Heatmap Settings")
        apply_button.clicked.connect(self.refresh_heatmap)
        heatmap_form.addRow(apply_button)
        left_layout.addWidget(heatmap_box)

        curve_box = QGroupBox("Curve Channels")
        curve_form = QFormLayout(curve_box)
        curve_form.addRow("X channel", self.x_combo)
        curve_form.addRow("Y channel(s)", self.y_list)
        curve_form.addRow("X axis", self.x_scale_combo)
        curve_form.addRow("Y axis", self.y_scale_combo)
        curve_form.addRow(self.legend_check)
        copy_data = QPushButton("Copy Selected Raw Data")
        copy_data.clicked.connect(lambda: QApplication.clipboard().setText(self.curve_plot.rawdata_tsv()))
        curve_form.addRow(copy_data)
        left_layout.addWidget(curve_box, 1)

        theme_box = QGroupBox("Appearance")
        theme_form = QFormLayout(theme_box)
        theme_form.addRow("Plot layout", self.plot_layout_combo)
        theme_form.addRow("Theme", self.theme_combo)
        left_layout.addWidget(theme_box)

        left_scroll = QScrollArea()
        left_scroll.setWidget(left)
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.right_splitter = QSplitter(Qt.Vertical)
        self.right_splitter.addWidget(self.curve_plot)
        self.right_splitter.addWidget(self.device_information)
        self.right_splitter.setSizes([560, 300])

        self.plots_splitter = QSplitter(Qt.Horizontal)
        self.plots_splitter.addWidget(self.heatmap)
        self.plots_splitter.addWidget(self.right_splitter)
        self.plots_splitter.setSizes([550, 550])
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_scroll)
        splitter.addWidget(self.plots_splitter)
        splitter.setSizes([320, 1100])
        root.addWidget(splitter)

    def _connect_signals(self) -> None:
        self.channel_combo.currentTextChanged.connect(self.refresh_heatmap)
        self.feature_combo.currentIndexChanged.connect(self.refresh_heatmap)
        self.cmap_combo.currentTextChanged.connect(self.refresh_heatmap)
        self.heatmap_scale_combo.currentTextChanged.connect(self.refresh_heatmap)
        self.theme_combo.currentTextChanged.connect(self.set_theme)
        self.plot_layout_combo.currentTextChanged.connect(self._set_plot_layout)
        self.multi_select_check.toggled.connect(self.heatmap.set_click_multi_select_mode)
        self.box_select_check.toggled.connect(self.heatmap.set_box_select_mode)
        self.heatmap.selection_changed.connect(self._selection_changed)
        self.x_combo.currentTextChanged.connect(self._curve_settings_changed)
        self.y_list.itemSelectionChanged.connect(self._curve_settings_changed)
        self.x_scale_combo.currentTextChanged.connect(self._curve_settings_changed)
        self.y_scale_combo.currentTextChanged.connect(self._curve_settings_changed)
        self.legend_check.toggled.connect(self._curve_settings_changed)

    def _set_plot_layout(self, layout_name: str) -> None:
        orientation = _PLOT_LAYOUTS.get(layout_name, Qt.Horizontal)
        self.plots_splitter.setOrientation(orientation)
        self.plots_splitter.setSizes([550, 550] if orientation == Qt.Horizontal else [500, 400])

    def _loaded(self, request_id: int, dataset: PreviewDataset) -> None:
        if request_id != self._load_generation:
            return
        self.dataset = dataset
        self.curve_plot.dataset = dataset
        self.heatmap.clear_selection()
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        self.channel_combo.addItems(dataset.channels)
        default_heatmap = "derived.vth_v" if self.hp6614c_only and "derived.vth_v" in dataset.channels else dataset.suggested_y
        if default_heatmap:
            self.channel_combo.setCurrentText(default_heatmap)
        self.channel_combo.blockSignals(False)
        self.feature_combo.setCurrentIndex(self.feature_combo.findData("first") if str(default_heatmap).startswith("derived.") else self.feature_combo.findData("max_abs"))

        self.x_combo.clear()
        self.x_combo.addItems(dataset.curve_channels)
        if dataset.suggested_x:
            self.x_combo.setCurrentText(dataset.suggested_x)
        self.y_list.clear()
        self.y_list.addItems(dataset.curve_channels)
        for index in range(self.y_list.count()):
            if self.y_list.item(index).text() == dataset.suggested_y:
                self.y_list.item(index).setSelected(True)
        self.summary_label.setText(f"{len(dataset.devices)} devices · {len(dataset.channels)} numeric channels")
        self.progress_changed.emit(100)
        self.status_changed.emit(f"Preview loaded: {len(dataset.devices)} devices")
        self.refresh_heatmap()
        self._curve_settings_changed()

    def _failed(self, request_id: int, message: str) -> None:
        if request_id != self._load_generation:
            return
        self.progress_changed.emit(0)
        self.status_changed.emit("Data preview load failed")
        QMessageBox.critical(self, "Data Preview", message)

    def _load_finished(self, request_id: int) -> None:
        job = self._load_jobs.pop(request_id, None)
        if request_id != self._load_generation or job is None:
            return
        thread, worker = job
        if self._thread is thread:
            self._thread = None
        if self._worker is worker:
            self._worker = None

    def _selection_changed(self, devices: list) -> None:
        names = [device.device_name for device in devices]
        self.curve_plot.device_names = names
        self.device_information.show_devices(self.dataset, names)
        self._curve_settings_changed()

    def _curve_settings_changed(self) -> None:
        self.curve_plot.x_channel = self.x_combo.currentText() or None
        self.curve_plot.y_channels = [item.text() for item in self.y_list.selectedItems()]
        self.curve_plot.x_scale = self.x_scale_combo.currentText()
        self.curve_plot.y_scale = self.y_scale_combo.currentText()
        self.curve_plot.show_legend = self.legend_check.isChecked()
        self.curve_plot.render()
