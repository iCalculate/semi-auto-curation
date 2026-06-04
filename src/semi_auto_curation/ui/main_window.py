from __future__ import annotations

import os

import psutil
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QMainWindow, QProgressBar, QStackedWidget, QStatusBar, QToolBar

from semi_auto_curation.analyzers.registry import ANALYZER_REGISTRY


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Semi-Auto Curation Studio")
        self.resize(1680, 980)
        self.panel_stack = QStackedWidget()
        self.analyzer_combo = QComboBox()
        self.analyzer_toolbar = QToolBar("Analyzer")
        self.panels = []
        self.panel_actions: list[list] = []
        self.status_label = QLabel("Loading...")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(220)
        self.memory_label = QLabel("Memory: --")
        self.setCentralWidget(self.panel_stack)
        self._build_shell()
        self._build_status_bar()
        self._memory_timer = QTimer(self)
        self._memory_timer.timeout.connect(self._refresh_memory_usage)
        self._memory_timer.start(1000)
        self._refresh_memory_usage()
        self.status_label.setText("Finish")

    def _build_shell(self) -> None:
        self.analyzer_toolbar.setMovable(False)
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
        self.analyzer_combo.currentIndexChanged.connect(self.panel_stack.setCurrentIndex)
        self.analyzer_combo.currentIndexChanged.connect(self._refresh_toolbar_actions)
        self._refresh_toolbar_actions(0)

    def _refresh_toolbar_actions(self, index: int) -> None:
        self.analyzer_toolbar.clear()
        self.analyzer_toolbar.addWidget(self.analyzer_combo)
        if 0 <= index < len(self.panel_actions):
            for action in self.panel_actions[index]:
                self.analyzer_toolbar.addAction(action)

    def _build_status_bar(self) -> None:
        status_bar = QStatusBar()
        status_bar.addWidget(self.status_label, 1)
        status_bar.addPermanentWidget(self.progress_bar)
        status_bar.addPermanentWidget(self.memory_label)
        self.setStatusBar(status_bar)

    def _refresh_memory_usage(self) -> None:
        process = psutil.Process(os.getpid())
        rss_mb = process.memory_info().rss / (1024 * 1024)
        self.memory_label.setText(f"Memory: {rss_mb:.1f} MB")


def launch() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.showMaximized()
    app.exec()
