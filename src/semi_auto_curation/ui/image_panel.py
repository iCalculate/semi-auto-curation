"""Image Analysis workspace panel.

Loads optical/SEM images from a source folder, computes per-device scalar
metrics (brightness, contrast, sharpness, histogram entropy), and renders
a device-array heatmap.  Clicking a heatmap cell previews the corresponding
device image.

Extension points
----------------
* Add new metric keys to ``_METRIC_LABELS`` and implement computation in
  ``data/image_loader.py:compute_image_metrics``.
* Subclass ``ImageAnalysisPanel`` and override ``_extra_metrics`` to inject
  metrics from external sources (e.g. a custom ML model).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from semi_auto_curation.data.image_loader import (
    _METRIC_KEYS,
    run_image_batch,
)
from semi_auto_curation.ui.iv_panel import (
    THEMES,
    HeatmapCanvas,
    HeatmapRenderState,
    _optional_float,
)
from semi_auto_curation.ui.parallel_preview import ParallelPreviewPanel
from semi_auto_curation.utils.logging import log_error, log_warn

_METRIC_LABELS = {
    "mean_brightness": "Mean Brightness",
    "contrast_std": "Contrast (σ)",
    "sharpness_laplacian": "Sharpness (Laplacian)",
    "histogram_entropy": "Histogram Entropy (bits)",
}


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class _ImageWorker(QObject):
    # All heavy I/O (discovery + image loading + metric computation) runs here,
    # never on the main thread.  The four dicts are returned so the UI can
    # switch metrics without re-loading any images.
    finished = Signal(dict, dict, dict, dict)  # values, positions, all_metrics, device_files
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, source_dir: Path, metric: str) -> None:
        super().__init__()
        self._source_dir = source_dir
        self._metric = metric

    def run(self) -> None:
        try:
            values, positions, all_metrics, device_files = run_image_batch(
                self._source_dir,
                metric=self._metric,
                progress_callback=self._on_progress,
            )
            self.finished.emit(values, positions, all_metrics, device_files)
        except Exception as exc:
            import traceback
            self.failed.emit(traceback.format_exc())

    def _on_progress(self, value: int, message: str) -> None:
        self.progress.emit(value, message)


# ---------------------------------------------------------------------------
# Image preview widget
# ---------------------------------------------------------------------------

class _ImagePreview(QWidget):
    """Shows the image for the most recently selected device."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._title = QLabel("No device selected")
        self._title.setAlignment(Qt.AlignCenter)
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setMinimumHeight(120)
        self._metrics_text = QTextEdit()
        self._metrics_text.setReadOnly(True)
        self._metrics_text.setMaximumHeight(120)
        layout.addWidget(self._title)
        layout.addWidget(self._image_label, 1)
        layout.addWidget(self._metrics_text)

    def show_device(self, device_name: str, image_path: Path | None, metrics: dict) -> None:
        self._title.setText(device_name)
        if image_path and image_path.exists():
            pix = QPixmap(str(image_path))
            if not pix.isNull():
                self._image_label.setPixmap(
                    pix.scaled(400, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            else:
                self._image_label.setText("(cannot load image)")
        else:
            self._image_label.setText("(no image)")

        lines = [f"{_METRIC_LABELS.get(k, k)}: {v:.4g}" if v is not None else f"{k}: N/A"
                 for k, v in metrics.items()]
        self._metrics_text.setPlainText("\n".join(lines))

    def clear(self) -> None:
        self._title.setText("No device selected")
        self._image_label.clear()
        self._metrics_text.clear()


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

class ImageAnalysisPanel(QWidget):
    """Workspace for device image analysis and heatmap display.

    Adding a new metric:
      1. Add its key and label to ``_METRIC_LABELS`` at the top of this file.
      2. Implement computation in ``data/image_loader.compute_image_metrics``.
      3. Rebuild the database (re-run analysis).  No other changes needed.
    """

    status_changed = Signal(str)
    progress_changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._source_dir: Path | None = None
        self._device_files: dict[str, list[Path]] = {}
        self._all_metrics: dict[str, dict[str, float | None]] = {}   # device → metrics
        self._positions: dict[str, tuple[int, int]] = {}
        self._worker: _ImageWorker | None = None
        self._thread: QThread | None = None

        self.source_edit = QLineEdit()
        self.output_edit = QLineEdit()
        self.metric_combo = QComboBox()
        self.metric_combo.addItems(list(_METRIC_LABELS.keys()))
        self.metric_combo.setItemData(0, _METRIC_LABELS, Qt.UserRole)
        for i, (key, label) in enumerate(_METRIC_LABELS.items()):
            self.metric_combo.setItemText(i, label)
            self.metric_combo.setItemData(i, key, Qt.UserRole)

        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(["viridis", "plasma", "inferno", "magma", "gray", "hot"])
        self.scale_mode_combo = QComboBox()
        self.scale_mode_combo.addItems(["linear", "log"])
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["light", "dark"])
        self.theme_combo.setCurrentText("dark")
        self.level_min_edit = QLineEdit()
        self.level_max_edit = QLineEdit()

        self.heatmap = HeatmapCanvas()
        self.image_preview = _ImagePreview()

        self._build_ui()
        self.set_theme("dark")
        self.heatmap.selection_changed.connect(self._on_selection_changed)
        self.metric_combo.currentIndexChanged.connect(self._on_metric_changed)
        self.cmap_combo.currentIndexChanged.connect(self._refresh_heatmap_if_ready)
        self.scale_mode_combo.currentIndexChanged.connect(self._refresh_heatmap_if_ready)
        self.theme_combo.currentTextChanged.connect(self.set_theme)

    # ------------------------------------------------------------------
    # Public interface (matches MainWindow duck-typed panel interface)
    # ------------------------------------------------------------------

    def build_toolbar_actions(self) -> list[QAction]:
        run_action = QAction("Analyze Images", self)
        run_action.triggered.connect(self.run_analysis)
        return [run_action]

    def run_analysis(self) -> None:
        source_dir = Path(self.source_edit.text().strip())
        if not source_dir.is_dir():
            QMessageBox.warning(self, "Missing Source", "Please select a valid image source folder.")
            return
        self._source_dir = source_dir

        metric_key = self.metric_combo.currentData(Qt.UserRole) or _METRIC_KEYS[0]
        self.status_changed.emit("Scanning images…")
        self.progress_changed.emit(0)

        # All discovery and computation happens in the worker — never block the main thread.
        self._thread = QThread(self)
        self._worker = _ImageWorker(source_dir, metric_key)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def refresh_heatmap(self) -> None:
        self._refresh_heatmap_if_ready()

    def clear_selection(self) -> None:
        self.heatmap.clear_selection()
        self.image_preview.clear()

    def set_theme(self, theme: str) -> None:
        if theme in THEMES:
            self.setStyleSheet(THEMES[theme]["qt_stylesheet"])
        self._refresh_heatmap_if_ready()

    def _choose_source(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Image Source Folder", self.source_edit.text())
        if folder:
            self.source_edit.setText(folder)

    def _choose_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Left settings panel
        left_inner = QWidget()
        left_layout = QVBoxLayout(left_inner)
        left_layout.setContentsMargins(0, 0, 4, 0)

        src_box = QGroupBox("Image Source")
        src_form = QFormLayout(src_box)
        src_row = self._browse_row(self.source_edit, self._choose_source)
        src_form.addRow("Source Folder", src_row)
        out_row = self._browse_row(self.output_edit, self._choose_output)
        src_form.addRow("Output Folder", out_row)
        run_btn = QPushButton("Analyze Images")
        run_btn.clicked.connect(self.run_analysis)
        src_form.addRow(run_btn)
        left_layout.addWidget(src_box)

        display_box = QGroupBox("Display Settings")
        df = QFormLayout(display_box)
        df.addRow("Metric", self.metric_combo)
        df.addRow("Colormap", self.cmap_combo)
        df.addRow("Scale", self.scale_mode_combo)
        df.addRow("Scale Min", self.level_min_edit)
        df.addRow("Scale Max", self.level_max_edit)
        df.addRow("Theme", self.theme_combo)
        apply_btn = QPushButton("Apply Settings")
        apply_btn.clicked.connect(self._refresh_heatmap_if_ready)
        df.addRow(apply_btn)
        left_layout.addWidget(display_box)

        preview_box = QGroupBox("Selected Device")
        pv = QVBoxLayout(preview_box)
        pv.addWidget(self.image_preview)
        left_layout.addWidget(preview_box, 1)
        left_layout.addStretch()

        left_scroll = QScrollArea()
        left_scroll.setWidget(left_inner)
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_scroll)
        splitter.addWidget(self.heatmap)
        splitter.setSizes([300, 1000])
        root.addWidget(splitter)

    @staticmethod
    def _browse_row(edit: QLineEdit, callback) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        btn = QPushButton("Browse…")
        btn.clicked.connect(callback)
        layout.addWidget(btn)
        return row

    def _current_state(self) -> HeatmapRenderState:
        metric_key = self.metric_combo.currentData(Qt.UserRole) or _METRIC_KEYS[0]
        return HeatmapRenderState(
            metric=metric_key,
            cmap=self.cmap_combo.currentText(),
            level_min=_optional_float(self.level_min_edit.text()),
            level_max=_optional_float(self.level_max_edit.text()),
            theme=self.theme_combo.currentText(),
            scale_mode=self.scale_mode_combo.currentText(),
        )

    def _on_progress(self, value: int, device_name: str) -> None:
        self.progress_changed.emit(value)
        self.status_changed.emit(f"Processing {device_name}… ({value}%)")

    def _on_worker_finished(
        self,
        values: dict,
        positions: dict,
        all_metrics: dict,
        device_files: dict,
    ) -> None:
        # Store pre-computed results — NO heavy computation on the main thread.
        self._device_files = device_files
        self._all_metrics = all_metrics
        self._positions = positions
        self._current_values = values

        count = len(values)
        ok = sum(1 for v in values.values() if v is not None)
        self.status_changed.emit(f"Image analysis complete — {ok}/{count} devices with data")
        self.progress_changed.emit(100)
        self._refresh_heatmap_if_ready()

    def _on_worker_failed(self, message: str) -> None:
        log_error(f"Image analysis failed: {message}")
        QMessageBox.critical(self, "Analysis Error", message)
        self.status_changed.emit("Error")
        self.progress_changed.emit(0)

    def _refresh_heatmap_if_ready(self) -> None:
        if not hasattr(self, "_current_values") or not self._current_values:
            return
        if not self._positions:
            log_warn("Image heatmap: no position data, cannot render grid.")
            return
        state = self._current_state()
        metric_label = _METRIC_LABELS.get(state.metric, state.metric)
        state = HeatmapRenderState(
            metric=metric_label,
            cmap=state.cmap,
            level_min=state.level_min,
            level_max=state.level_max,
            theme=state.theme,
            scale_mode=state.scale_mode,
        )
        self.heatmap.render_value_grid(
            values=self._current_values,
            positions=self._positions,
            state=state,
            title=f"Image Heatmap — {metric_label}",
        )

    def _on_metric_changed(self) -> None:
        # If images were already loaded, extract the new metric from cached results
        # without re-loading any images from disk.
        if self._all_metrics:
            metric_key = self.metric_combo.currentData(Qt.UserRole) or _METRIC_KEYS[0]
            self._current_values = {
                name: m.get(metric_key)
                for name, m in self._all_metrics.items()
            }
            self._refresh_heatmap_if_ready()
        elif self._source_dir:
            self.run_analysis()

    def _on_selection_changed(self, devices: list) -> None:
        if not devices:
            self.image_preview.clear()
            return
        device = devices[-1]
        name = device.device_name
        paths = self._device_files.get(name, [])
        img_path = paths[0] if paths else None
        metrics = self._all_metrics.get(name, {})
        self.image_preview.show_device(name, img_path, metrics)
