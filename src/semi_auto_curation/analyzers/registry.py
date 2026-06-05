"""Analyzer registry — the single place to add new workspace types.

To add a new workspace:
  1. Create a panel class in ``ui/`` that implements the panel interface
     (see ``ui/base_panel.py`` for the expected duck-typed methods).
  2. Import it here and append an ``AnalyzerDescriptor`` to
     ``ANALYZER_REGISTRY``.  No other file needs to change.

Panel interface (duck-typed, enforced by MainWindow):
  - ``build_toolbar_actions() → list[QAction]``   (toolbar buttons)
  - ``run_analysis() → None``                     (optional)
  - ``refresh_heatmap() → None``                  (optional)
  - ``clear_selection() → None``                  (optional)
  - ``set_theme(theme: str) → None``              (optional)
  - ``theme_combo``  QComboBox attribute          (optional – used by menu)
  - ``status_changed``  Signal[str]               (optional)
  - ``progress_changed``  Signal[int]             (optional)
  - ``_choose_source() → None``                   (optional – File menu)
  - ``_choose_output() → None``                   (optional – File menu)
  - ``_open_cloud_sessions() → None``             (optional – File menu)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from semi_auto_curation.ui.b1500_panel import B1500AnalysisPanel
from semi_auto_curation.ui.image_panel import ImageAnalysisPanel
from semi_auto_curation.ui.iv_panel import IVAnalysisPanel
from semi_auto_curation.ui.script_panel import ScriptPanel


@dataclass(frozen=True, slots=True)
class AnalyzerDescriptor:
    key: str
    label: str
    panel_factory: Callable[[], object]


ANALYZER_REGISTRY: list[AnalyzerDescriptor] = [
    # --- Electrical characterisation ---
    AnalyzerDescriptor(
        key="iv",
        label="K2450-IV",
        panel_factory=IVAnalysisPanel,
    ),
    AnalyzerDescriptor(
        key="b1500_transfer",
        label="B1500-Trans",
        panel_factory=lambda: B1500AnalysisPanel("transfer"),
    ),
    AnalyzerDescriptor(
        key="b1500_output",
        label="B1500-Output",
        panel_factory=lambda: B1500AnalysisPanel("output"),
    ),
    # --- Image analysis ---
    AnalyzerDescriptor(
        key="image",
        label="Image Analysis",
        panel_factory=ImageAnalysisPanel,
    ),
    # --- Custom scripting ---
    AnalyzerDescriptor(
        key="script",
        label="Custom Script",
        panel_factory=ScriptPanel,
    ),
]
