from __future__ import annotations

import os
from importlib import metadata

import psutil
from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMainWindow,
    QProgressBar,
    QStackedWidget,
    QStatusBar,
    QTextBrowser,
    QToolBar,
    QVBoxLayout,
)

from semi_auto_curation.analyzers.registry import ANALYZER_REGISTRY


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Semi-Auto Curation Studio")
        self.resize(1680, 980)
        self.panel_stack = QStackedWidget()
        self.analyzer_combo = QComboBox()
        self.analyzer_toolbar = QToolBar("Quick Access")
        self.analyzer_label = QLabel("Workspace")
        self.panels = []
        self.panel_actions: list[list[QAction]] = []
        self.analyzer_actions: list[QAction] = []
        self.status_label = QLabel("Loading...")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(220)
        self.memory_label = QLabel("Memory: --")
        self.show_statusbar_action: QAction | None = None
        self.build_database_action: QAction | None = None
        self.run_analysis_action: QAction | None = None
        self.refresh_heatmap_action: QAction | None = None
        self.clear_selection_action: QAction | None = None
        self.open_source_action: QAction | None = None
        self.open_output_action: QAction | None = None
        self.open_cloud_action: QAction | None = None
        self.copy_heatmap_image_action: QAction | None = None
        self.copy_heatmap_raw_action: QAction | None = None
        self.copy_curve_image_action: QAction | None = None
        self.copy_curve_raw_action: QAction | None = None
        self.light_theme_action: QAction | None = None
        self.dark_theme_action: QAction | None = None
        self.setCentralWidget(self.panel_stack)
        self._build_shell()
        self._build_menu_bar()
        self._build_status_bar()
        self._memory_timer = QTimer(self)
        self._memory_timer.timeout.connect(self._refresh_memory_usage)
        self._memory_timer.start(1000)
        self._refresh_memory_usage()
        self.status_label.setText("Finish")
        self._refresh_menu_state()

    def _build_shell(self) -> None:
        self.analyzer_toolbar.setMovable(False)
        self.analyzer_toolbar.setObjectName("QuickAccessToolbar")
        self.addToolBar(self.analyzer_toolbar)
        for descriptor in ANALYZER_REGISTRY:
            panel = descriptor.panel_factory()
            self.panels.append(panel)
            self.panel_stack.addWidget(panel)
            self.analyzer_combo.addItem(descriptor.label, descriptor.key)
            actions = panel.build_toolbar_actions() if hasattr(panel, "build_toolbar_actions") else []
            self.panel_actions.append(actions)
            if hasattr(panel, "status_changed"):
                panel.status_changed.connect(self.status_label.setText)
            if hasattr(panel, "progress_changed"):
                panel.progress_changed.connect(self.progress_bar.setValue)
        self.analyzer_combo.currentIndexChanged.connect(self._on_analyzer_changed)
        self._refresh_toolbar_actions(0)

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        self.open_source_action = self._make_action(
            "Select Source Folder...",
            self._choose_source_for_current_panel,
            shortcut="Ctrl+Shift+O",
            tip="Choose the source folder for the current workspace.",
        )
        self.open_output_action = self._make_action(
            "Select Output Folder...",
            self._choose_output_for_current_panel,
            shortcut="Ctrl+Shift+S",
            tip="Choose the export folder for the current workspace.",
        )
        self.open_cloud_action = self._make_action(
            "Open Cloud Sessions...",
            self._open_cloud_for_current_panel,
            tip="Browse and sync cloud autotest sessions.",
        )
        exit_action = self._make_action("Exit", self.close, shortcut=QKeySequence.Quit)
        file_menu.addAction(self.open_source_action)
        file_menu.addAction(self.open_output_action)
        file_menu.addAction(self.open_cloud_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

        edit_menu = menu_bar.addMenu("&Edit")
        self.clear_selection_action = self._make_action(
            "Clear Selection",
            self._clear_selection_for_current_panel,
            shortcut="Esc",
            tip="Clear the current heatmap/device selection.",
        )
        self.copy_heatmap_image_action = self._make_action("Copy Heatmap Image", self._copy_heatmap_image_for_current_panel)
        self.copy_heatmap_raw_action = self._make_action("Copy Heatmap Raw Data", self._copy_heatmap_raw_for_current_panel)
        self.copy_curve_image_action = self._make_action("Copy Curve Image", self._copy_curve_image_for_current_panel)
        self.copy_curve_raw_action = self._make_action("Copy Curve Raw Data", self._copy_curve_raw_for_current_panel)
        edit_menu.addAction(self.clear_selection_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.copy_heatmap_image_action)
        edit_menu.addAction(self.copy_heatmap_raw_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.copy_curve_image_action)
        edit_menu.addAction(self.copy_curve_raw_action)

        view_menu = menu_bar.addMenu("&View")
        self.refresh_heatmap_action = self._make_action(
            "Refresh Heatmap",
            self._refresh_heatmap_for_current_panel,
            shortcut="F5",
            tip="Redraw the heatmap using the current settings.",
        )
        view_menu.addAction(self.refresh_heatmap_action)
        view_menu.addSeparator()
        analyzer_menu = view_menu.addMenu("Switch Workspace")
        analyzer_group = QActionGroup(self)
        analyzer_group.setExclusive(True)
        for index, descriptor in enumerate(ANALYZER_REGISTRY):
            action = self._make_action(descriptor.label, lambda checked=False, idx=index: self._set_analyzer_index(idx))
            action.setCheckable(True)
            analyzer_group.addAction(action)
            analyzer_menu.addAction(action)
            self.analyzer_actions.append(action)
        theme_menu = view_menu.addMenu("Theme")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        self.light_theme_action = self._make_action("Light", lambda checked=False: self._set_theme_for_current_panel("light"))
        self.light_theme_action.setCheckable(True)
        self.dark_theme_action = self._make_action("Dark", lambda checked=False: self._set_theme_for_current_panel("dark"))
        self.dark_theme_action.setCheckable(True)
        theme_group.addAction(self.light_theme_action)
        theme_group.addAction(self.dark_theme_action)
        theme_menu.addAction(self.light_theme_action)
        theme_menu.addAction(self.dark_theme_action)
        view_menu.addSeparator()
        self.show_statusbar_action = self._make_action("Show Status Bar", self._toggle_status_bar)
        self.show_statusbar_action.setCheckable(True)
        self.show_statusbar_action.setChecked(True)
        view_menu.addAction(self.show_statusbar_action)

        analysis_menu = menu_bar.addMenu("&Analysis")
        self.build_database_action = self._make_action(
            "Load Data and Build Database",
            self._build_database_for_current_panel,
            shortcut="Ctrl+Shift+B",
            tip="Preload raw data and build the working cache when supported.",
        )
        self.run_analysis_action = self._make_action(
            "Run Analysis",
            self._run_analysis_for_current_panel,
            shortcut="Ctrl+R",
            tip="Run the main analysis for the current workspace.",
        )
        analysis_menu.addAction(self.build_database_action)
        analysis_menu.addAction(self.run_analysis_action)
        analysis_menu.addSeparator()
        analysis_menu.addAction(self.refresh_heatmap_action)

        help_menu = menu_bar.addMenu("&Help")
        help_menu.addAction(self._make_action("Quick Start", self._show_quick_start))
        help_menu.addAction(self._make_action("Operation Guide", self._show_operation_guide))
        help_menu.addAction(self._make_action("Help Topics", self._show_help_topics))

        about_menu = menu_bar.addMenu("&About")
        about_menu.addAction(self._make_action("About This App", self._show_about_dialog))
        about_menu.addAction(self._make_action("Software Information", self._show_software_information))
        about_menu.addAction(self._make_action("Copyright and License", self._show_copyright_information))

    def _build_status_bar(self) -> None:
        status_bar = QStatusBar()
        status_bar.addWidget(self.status_label, 1)
        status_bar.addPermanentWidget(self.progress_bar)
        status_bar.addPermanentWidget(self.memory_label)
        self.setStatusBar(status_bar)

    def _refresh_toolbar_actions(self, index: int) -> None:
        self.analyzer_toolbar.clear()
        self.analyzer_toolbar.addWidget(self.analyzer_label)
        self.analyzer_toolbar.addWidget(self.analyzer_combo)
        self.analyzer_toolbar.addSeparator()
        if 0 <= index < len(self.panel_actions):
            for action in self.panel_actions[index]:
                self.analyzer_toolbar.addAction(action)

    def _on_analyzer_changed(self, index: int) -> None:
        self.panel_stack.setCurrentIndex(index)
        self._refresh_toolbar_actions(index)
        self._refresh_menu_state()

    def _refresh_menu_state(self) -> None:
        panel = self._current_panel()
        has_build = hasattr(panel, "load_and_build_database")
        if self.build_database_action is not None:
            self.build_database_action.setEnabled(has_build)
        if self.run_analysis_action is not None:
            self.run_analysis_action.setEnabled(hasattr(panel, "run_analysis"))
        if self.refresh_heatmap_action is not None:
            self.refresh_heatmap_action.setEnabled(hasattr(panel, "refresh_heatmap"))
        if self.clear_selection_action is not None:
            self.clear_selection_action.setEnabled(hasattr(panel, "clear_selection"))
        if self.open_source_action is not None:
            self.open_source_action.setEnabled(hasattr(panel, "_choose_source"))
        if self.open_output_action is not None:
            self.open_output_action.setEnabled(hasattr(panel, "_choose_output"))
        if self.open_cloud_action is not None:
            self.open_cloud_action.setEnabled(hasattr(panel, "_open_cloud_sessions"))
        if self.copy_heatmap_image_action is not None:
            self.copy_heatmap_image_action.setEnabled(hasattr(panel, "_copy_heatmap_image"))
        if self.copy_heatmap_raw_action is not None:
            self.copy_heatmap_raw_action.setEnabled(hasattr(panel, "_copy_heatmap_rawdata"))
        if self.copy_curve_image_action is not None:
            self.copy_curve_image_action.setEnabled(hasattr(panel, "_copy_curve_image"))
        if self.copy_curve_raw_action is not None:
            self.copy_curve_raw_action.setEnabled(hasattr(panel, "_copy_curve_rawdata"))
        for index, action in enumerate(self.analyzer_actions):
            action.setChecked(index == self.analyzer_combo.currentIndex())
        current_theme = self._current_theme_name()
        if self.light_theme_action is not None:
            self.light_theme_action.setChecked(current_theme == "light")
        if self.dark_theme_action is not None:
            self.dark_theme_action.setChecked(current_theme == "dark")

    def _current_panel(self):
        return self.panels[self.analyzer_combo.currentIndex()]

    def _set_analyzer_index(self, index: int) -> None:
        self.analyzer_combo.setCurrentIndex(index)

    def _run_analysis_for_current_panel(self) -> None:
        panel = self._current_panel()
        if hasattr(panel, "run_analysis"):
            panel.run_analysis()

    def _build_database_for_current_panel(self) -> None:
        panel = self._current_panel()
        if hasattr(panel, "load_and_build_database"):
            panel.load_and_build_database()

    def _refresh_heatmap_for_current_panel(self) -> None:
        panel = self._current_panel()
        if hasattr(panel, "refresh_heatmap"):
            panel.refresh_heatmap()

    def _clear_selection_for_current_panel(self) -> None:
        panel = self._current_panel()
        if hasattr(panel, "clear_selection"):
            panel.clear_selection()

    def _choose_source_for_current_panel(self) -> None:
        panel = self._current_panel()
        if hasattr(panel, "_choose_source"):
            panel._choose_source()

    def _choose_output_for_current_panel(self) -> None:
        panel = self._current_panel()
        if hasattr(panel, "_choose_output"):
            panel._choose_output()

    def _open_cloud_for_current_panel(self) -> None:
        panel = self._current_panel()
        if hasattr(panel, "_open_cloud_sessions"):
            panel._open_cloud_sessions()

    def _copy_heatmap_image_for_current_panel(self) -> None:
        panel = self._current_panel()
        if hasattr(panel, "_copy_heatmap_image"):
            panel._copy_heatmap_image()

    def _copy_heatmap_raw_for_current_panel(self) -> None:
        panel = self._current_panel()
        if hasattr(panel, "_copy_heatmap_rawdata"):
            panel._copy_heatmap_rawdata()

    def _copy_curve_image_for_current_panel(self) -> None:
        panel = self._current_panel()
        if hasattr(panel, "_copy_curve_image"):
            panel._copy_curve_image()

    def _copy_curve_raw_for_current_panel(self) -> None:
        panel = self._current_panel()
        if hasattr(panel, "_copy_curve_rawdata"):
            panel._copy_curve_rawdata()

    def _set_theme_for_current_panel(self, theme: str) -> None:
        panel = self._current_panel()
        if hasattr(panel, "theme_combo"):
            panel.theme_combo.setCurrentText(theme)
        elif hasattr(panel, "set_theme"):
            panel.set_theme(theme)
        self._refresh_menu_state()

    def _current_theme_name(self) -> str:
        panel = self._current_panel()
        if hasattr(panel, "theme_combo"):
            return panel.theme_combo.currentText()
        return "dark"

    def _toggle_status_bar(self) -> None:
        if self.show_statusbar_action is None:
            return
        self.statusBar().setVisible(self.show_statusbar_action.isChecked())

    def _show_about_dialog(self) -> None:
        version = _app_version()
        html = f"""
        <h2>Semi-Auto Curation Studio</h2>
        <p><b>Version:</b> {version}</p>
        <p>Desktop workbench for high-throughput electrical test data analysis, visualization, and device-array review.</p>
        <p><b>Workspaces:</b> K2450-IV, B1500-Trans, B1500-Output</p>
        <p>This GUI is organized around fast analyzer switching, professional menu navigation, quick heatmap review, and linked curve inspection.</p>
        """
        self._show_rich_text_dialog("About This App", html)

    def _show_software_information(self) -> None:
        analyzer_list = "".join(f"<li>{descriptor.label}</li>" for descriptor in ANALYZER_REGISTRY)
        html = f"""
        <h2>Software Information</h2>
        <p><b>Application:</b> Semi-Auto Curation Studio</p>
        <p><b>Version:</b> {_app_version()}</p>
        <p><b>Python:</b> 3.12+</p>
        <p><b>Main GUI Stack:</b> PySide6, Matplotlib, NumPy, pyqtgraph</p>
        <p><b>Available analyzers:</b></p>
        <ul>{analyzer_list}</ul>
        <p>The current build focuses on array heatmaps, raw-curve inspection, exportable summaries, and cloud-session assisted data loading.</p>
        """
        self._show_rich_text_dialog("Software Information", html)

    def _show_copyright_information(self) -> None:
        html = """
        <h2>Copyright and License</h2>
        <p>Copyright status should follow your team's internal project ownership policy.</p>
        <p>No standalone <code>LICENSE</code> file was found in the current workspace at the time of this GUI build, so redistribution and external reuse should be reviewed before release.</p>
        <p>Third-party packages used by this application, including PySide6, Matplotlib, NumPy, psutil, and pyqtgraph, retain their own upstream licenses.</p>
        """
        self._show_rich_text_dialog("Copyright and License", html)

    def _show_quick_start(self) -> None:
        html = """
        <h2>Quick Start</h2>
        <ol>
          <li>Select the target workspace from the top quick-access bar or the <b>View &gt; Switch Workspace</b> menu.</li>
          <li>Use <b>File</b> to choose the source folder, output folder, or open cloud sessions.</li>
          <li>For K2450-IV, run <b>Analysis &gt; Load Data and Build Database</b> first when you want the cached database flow.</li>
          <li>Run <b>Analysis &gt; Run Analysis</b> to compute device metrics and exports.</li>
          <li>Review the heatmap, select devices, and inspect linked raw curves in the right panel.</li>
          <li>Use <b>Edit</b> to copy images or raw data for reports and troubleshooting.</li>
        </ol>
        """
        self._show_rich_text_dialog("Quick Start", html)

    def _show_operation_guide(self) -> None:
        html = """
        <h2>Operation Guide</h2>
        <h3>File</h3>
        <ul>
          <li><b>Select Source Folder</b>: choose the raw-data root for the active analyzer.</li>
          <li><b>Select Output Folder</b>: choose where summaries, caches, and detail JSON files are written.</li>
          <li><b>Open Cloud Sessions</b>: browse autotest sessions and sync them into the local workspace.</li>
        </ul>
        <h3>View</h3>
        <ul>
          <li><b>Switch Workspace</b>: move between K2450-IV, B1500-Trans, and B1500-Output.</li>
          <li><b>Theme</b>: switch between light and dark review styles.</li>
          <li><b>Refresh Heatmap</b>: re-render the heatmap after changing metric, colormap, scale, or theme.</li>
        </ul>
        <h3>Analysis</h3>
        <ul>
          <li><b>Load Data and Build Database</b>: available for K2450-IV cache preparation.</li>
          <li><b>Run Analysis</b>: compute per-device metrics and export summary/detail files.</li>
        </ul>
        <h3>Selection and Export</h3>
        <ul>
          <li>Click a heatmap cell to inspect a device.</li>
          <li>Use multi-select controls inside the panel for comparing multiple devices.</li>
          <li>Use the <b>Edit</b> menu to copy heatmap images, curve images, or raw tabular data.</li>
        </ul>
        """
        self._show_rich_text_dialog("Operation Guide", html)

    def _show_help_topics(self) -> None:
        html = """
        <h2>Help Topics</h2>
        <h3>When analysis fails</h3>
        <ul>
          <li>Confirm the selected source folder matches the active workspace format.</li>
          <li>Check that the output folder is writable.</li>
          <li>For B1500 data, confirm transfer and output files follow the expected naming patterns.</li>
        </ul>
        <h3>When plots look wrong</h3>
        <ul>
          <li>Refresh the heatmap after changing metric or scale settings.</li>
          <li>Switch the curve Y-scale between linear and log for better inspection.</li>
          <li>Use the theme menu if contrast is poor for the current environment.</li>
        </ul>
        <h3>When reviewing multiple devices</h3>
        <ul>
          <li>Use the panel's click multi-select mode to compare several devices at once.</li>
          <li>Export copied raw data from the <b>Edit</b> menu for external plotting or reports.</li>
        </ul>
        """
        self._show_rich_text_dialog("Help Topics", html)

    def _show_rich_text_dialog(self, title: str, html: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(760, 560)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser(dialog)
        browser.setOpenExternalLinks(True)
        browser.setHtml(html)
        layout.addWidget(browser, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _make_action(
        self,
        text: str,
        callback,
        *,
        shortcut: str | QKeySequence | None = None,
        tip: str | None = None,
    ) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        if tip:
            action.setStatusTip(tip)
            action.setToolTip(tip)
        action.triggered.connect(callback)
        return action

    def _refresh_memory_usage(self) -> None:
        process = psutil.Process(os.getpid())
        rss_mb = process.memory_info().rss / (1024 * 1024)
        self.memory_label.setText(f"Memory: {rss_mb:.1f} MB")


def _app_version() -> str:
    try:
        return metadata.version("semi-auto-curation")
    except metadata.PackageNotFoundError:
        return "0.1.0"


def launch() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.showMaximized()
    app.exec()
