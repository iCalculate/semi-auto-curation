from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from semi_auto_curation.ui.b1500_panel import B1500AnalysisPanel
from semi_auto_curation.ui.iv_panel import IVAnalysisPanel


@dataclass(frozen=True, slots=True)
class AnalyzerDescriptor:
    key: str
    label: str
    panel_factory: Callable[[], object]


ANALYZER_REGISTRY: list[AnalyzerDescriptor] = [
    AnalyzerDescriptor(key="iv", label="K2450-IV", panel_factory=IVAnalysisPanel),
    AnalyzerDescriptor(key="b1500_transfer", label="B1500-Trans", panel_factory=lambda: B1500AnalysisPanel("transfer")),
    AnalyzerDescriptor(key="b1500_output", label="B1500-Output", panel_factory=lambda: B1500AnalysisPanel("output")),
]
