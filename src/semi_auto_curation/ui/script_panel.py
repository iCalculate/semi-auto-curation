"""Custom Script workspace panel.

Lets the user write Python code that computes per-device scalar values, then
renders them as a heatmap.  No analysis plug-in API or restart required.

Script execution context
------------------------
The following names are injected into the script's global namespace:

    source_dir    pathlib.Path   – selected source folder
    device_files  dict[str, list[Path]]
                                 – {device_name: [file_paths…]};
                                   populated by auto-discovery (configurable)
    np            numpy          – numpy module
    Path          pathlib.Path
    json          json module
    re            re module
    results       dict           – WRITE HERE: {device_name: float}
    layout        dict           – OPTIONAL: {device_name: (row, col)};
                                   overrides auto-detection from names

Example script
--------------
    import numpy as np
    from pathlib import Path

    for name, paths in device_files.items():
        csv = [p for p in paths if p.suffix == ".csv"]
        if csv:
            data = np.loadtxt(str(csv[0]), delimiter=",", skiprows=1)
            results[name] = float(np.mean(data[:, 4]))   # column 4 = current

Adding a custom file-discovery pattern
---------------------------------------
Override ``_discover`` in a subclass and register the subclass in the
analyzer registry.
"""
from __future__ import annotations

import json
import math
import re
import statistics
import traceback
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from semi_auto_curation.data.image_loader import infer_positions
from semi_auto_curation.ui.iv_panel import (
    THEMES,
    HeatmapCanvas,
    HeatmapRenderState,
    _optional_float,
)
from semi_auto_curation.utils.logging import log_error

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
_DATA_SUFFIXES = {".csv", ".tsv", ".txt", ".json"}
_ALL_SUFFIXES = _IMAGE_SUFFIXES | _DATA_SUFFIXES

_DEFAULT_SCRIPT = '''\
# Script context variables:
#   source_dir    – pathlib.Path to the source folder
#   device_files  – {device_name: [Path, …]} discovered files
#   np            – numpy
#   Path, json, re, math, statistics
#   results       – WRITE results here: {device_name: float}
#   layout        – OPTIONAL override: {device_name: (row, col)}

for name, paths in device_files.items():
    # Example: count the number of files per device
    results[name] = float(len(paths))
'''


# ---------------------------------------------------------------------------
# Discovery helper
# ---------------------------------------------------------------------------

def _discover_device_files(source_dir: Path) -> dict[str, list[Path]]:
    """Group all data/image files in *source_dir* by device name (flat folder).

    Device name = stem up to the first '_', or the full stem.
    """
    groups: dict[str, list[Path]] = {}
    for path in sorted(source_dir.iterdir()):
        if path.suffix.lower() not in _ALL_SUFFIXES:
            continue
        stem = path.stem
        name = stem.split("_")[0] if "_" in stem else stem
        groups.setdefault(name, []).append(path)
    return groups


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class _ScriptWorker(QObject):
    finished = Signal(dict, dict, str)  # (results, layout, metric_name)
    failed = Signal(str)

    def __init__(
        self,
        script: str,
        source_dir: Path,
        device_files: dict[str, list[Path]],
        metric_name: str,
    ) -> None:
        super().__init__()
        self._script = script
        self._source_dir = source_dir
        self._device_files = device_files
        self._metric_name = metric_name

    def run(self) -> None:
        results: dict[str, Any] = {}
        layout: dict[str, Any] = {}
        context = {
            "__builtins__": __builtins__,
            "np": np,
            "numpy": np,
            "Path": Path,
            "json": json,
            "re": re,
            "math": math,
            "statistics": statistics,
            "source_dir": self._source_dir,
            "device_files": self._device_files,
            "results": results,
            "layout": layout,
        }
        try:
            exec(self._script, context)  # noqa: S102
        except Exception:
            self.failed.emit(traceback.format_exc())
            return

        # Retrieve output containers (script may have replaced them)
        out_results = context.get("results", results)
        out_layout = context.get("layout", layout)

        # Coerce values to float
        coerced: dict[str, float | None] = {}
        for k, v in out_results.items():
            try:
                coerced[str(k)] = float(v) if v is not None else None
            except (TypeError, ValueError):
                coerced[str(k)] = None

        coerced_layout: dict[str, tuple[int, int]] = {}
        for k, v in out_layout.items():
            try:
                coerced_layout[str(k)] = (int(v[0]), int(v[1]))
            except Exception:
                pass

        self.finished.emit(coerced, coerced_layout, self._metric_name)


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class ScriptPanel(QWidget):
    """Custom-script workspace: write Python → get a heatmap.

    Extension pattern
    -----------------
    Override ``_discover`` to change how files are grouped by device name
    (e.g. recursive folder scan, filetype filtering, etc.).
    """

    status_changed = Signal(str)
    progress_changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._source_dir: Path | None = None
        self._device_files: dict[str, list[Path]] = {}
        self._last_results: dict[str, float | None] = {}
        self._last_positions: dict[str, tuple[int, int]] = {}
        self._worker: _ScriptWorker | None = None
        self._thread: QThread | None = None

        self.source_edit = QLineEdit()
        self.output_edit = QLineEdit()
        self.metric_name_edit = QLineEdit("custom_metric")
        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(["viridis", "plasma", "inferno", "magma", "gray"])
        self.scale_mode_combo = QComboBox()
        self.scale_mode_combo.addItems(["linear", "log"])
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["light", "dark"])
        self.theme_combo.setCurrentText("dark")
        self.level_min_edit = QLineEdit()
        self.level_max_edit = QLineEdit()
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setMaximumHeight(150)

        self.script_editor = QPlainTextEdit(_DEFAULT_SCRIPT)
        mono = QFont("Consolas", 10)
        mono.setStyleHint(QFont.Monospace)
        self.script_editor.setFont(mono)

        self.heatmap = HeatmapCanvas()

        self._build_ui()
        self.set_theme("dark")
        self.theme_combo.currentTextChanged.connect(self.set_theme)
        self.cmap_combo.currentIndexChanged.connect(self._refresh_heatmap_if_ready)
        self.scale_mode_combo.currentIndexChanged.connect(self._refresh_heatmap_if_ready)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def build_toolbar_actions(self) -> list[QAction]:
        run_action = QAction("Run Script", self)
        run_action.triggered.connect(self.run_analysis)
        return [run_action]

    def run_analysis(self) -> None:
        source_dir = Path(self.source_edit.text().strip())
        if not source_dir.is_dir():
            QMessageBox.warning(self, "Missing Source", "Please select a valid source folder.")
            return
        self._source_dir = source_dir
        self._device_files = self._discover(source_dir)

        script = self.script_editor.toPlainText()
        metric_name = self.metric_name_edit.text().strip() or "custom_metric"

        self.output_text.clear()
        self.output_text.append(f"Running script on {len(self._device_files)} devices…\n")
        self.status_changed.emit("Running script…")
        self.progress_changed.emit(0)

        self._thread = QThread(self)
        self._worker = _ScriptWorker(script, source_dir, self._device_files, metric_name)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def refresh_heatmap(self) -> None:
        self._refresh_heatmap_if_ready()

    def clear_selection(self) -> None:
        self.heatmap.clear_selection()

    def set_theme(self, theme: str) -> None:
        if theme in THEMES:
            self.setStyleSheet(THEMES[theme]["qt_stylesheet"])
        self._refresh_heatmap_if_ready()

    def _choose_source(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Source Folder", self.source_edit.text())
        if folder:
            self.source_edit.setText(folder)

    def _choose_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)

    # ------------------------------------------------------------------
    # Extension point
    # ------------------------------------------------------------------

    def _discover(self, source_dir: Path) -> dict[str, list[Path]]:
        """Override to customise how files are grouped by device name."""
        return _discover_device_files(source_dir)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # ---- Left: settings + output log ----
        left_inner = QWidget()
        ll = QVBoxLayout(left_inner)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.setSpacing(6)

        src_box = QGroupBox("Data Source")
        sf = QFormLayout(src_box)
        sf.addRow("Source Folder", self._browse_row(self.source_edit, self._choose_source))
        sf.addRow("Output Folder", self._browse_row(self.output_edit, self._choose_output))
        ll.addWidget(src_box)

        script_box = QGroupBox("Script Settings")
        scf = QFormLayout(script_box)
        scf.addRow("Output Metric Name", self.metric_name_edit)
        run_btn = QPushButton("▶  Run Script")
        run_btn.clicked.connect(self.run_analysis)
        scf.addRow(run_btn)
        ll.addWidget(script_box)

        display_box = QGroupBox("Heatmap Settings")
        df = QFormLayout(display_box)
        df.addRow("Colormap", self.cmap_combo)
        df.addRow("Scale", self.scale_mode_combo)
        df.addRow("Scale Min", self.level_min_edit)
        df.addRow("Scale Max", self.level_max_edit)
        df.addRow("Theme", self.theme_combo)
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._refresh_heatmap_if_ready)
        df.addRow(apply_btn)
        ll.addWidget(display_box)

        log_box = QGroupBox("Script Output / Errors")
        logv = QVBoxLayout(log_box)
        logv.addWidget(self.output_text)
        ll.addWidget(log_box, 1)

        help_lbl = QLabel(
            "<small><b>Context:</b> source_dir, device_files, np, Path, json, re<br>"
            "Write to <code>results[device_name] = float_value</code><br>"
            "Optionally set <code>layout[device_name] = (row, col)</code></small>"
        )
        help_lbl.setWordWrap(True)
        ll.addWidget(help_lbl)

        left_scroll = QScrollArea()
        left_scroll.setWidget(left_inner)
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(240)

        # ---- Centre: script editor ----
        editor_box = QGroupBox("Python Script")
        editor_layout = QVBoxLayout(editor_box)
        editor_layout.addWidget(self.script_editor)

        # ---- Right: heatmap ----
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_scroll)
        splitter.addWidget(editor_box)
        splitter.addWidget(self.heatmap)
        splitter.setSizes([260, 500, 600])
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

    def _on_finished(self, results: dict, user_layout: dict, metric_name: str) -> None:
        self._last_results = results
        ok = sum(1 for v in results.values() if v is not None)
        total = len(results)
        self.output_text.append(f"✓ Script finished — {ok}/{total} devices produced a value.\n")

        # Determine positions: prefer user-supplied layout, fall back to name parsing
        if user_layout:
            self._last_positions = user_layout
        else:
            self._last_positions = infer_positions(list(results.keys()))
            if not self._last_positions:
                self.output_text.append(
                    "⚠ Could not determine grid positions from device names.\n"
                    "  Set layout[device_name] = (row, col) in your script.\n"
                )

        self.status_changed.emit(f"Script complete — {ok}/{total} values")
        self.progress_changed.emit(100)
        self._refresh_heatmap_if_ready()

    def _on_failed(self, error: str) -> None:
        log_error(f"Script execution error: {error}")
        self.output_text.append(f"✗ Error:\n{error}")
        self.status_changed.emit("Script error")
        self.progress_changed.emit(0)

    def _refresh_heatmap_if_ready(self) -> None:
        if not self._last_results or not self._last_positions:
            return
        metric_name = self.metric_name_edit.text().strip() or "custom_metric"
        state = HeatmapRenderState(
            metric=metric_name,
            cmap=self.cmap_combo.currentText(),
            level_min=_optional_float(self.level_min_edit.text()),
            level_max=_optional_float(self.level_max_edit.text()),
            theme=self.theme_combo.currentText(),
            scale_mode=self.scale_mode_combo.currentText(),
        )
        self.heatmap.render_value_grid(
            values=self._last_results,
            positions=self._last_positions,
            state=state,
            title=f"Custom Script — {metric_name}",
        )
