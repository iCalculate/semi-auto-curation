from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from semi_auto_curation.ui.iv_panel import IVAnalysisPanel


@dataclass(frozen=True, slots=True)
class AnalyzerDescriptor:
    key: str
    label: str
    panel_factory: Callable[[], object]


ANALYZER_REGISTRY: list[AnalyzerDescriptor] = [
    AnalyzerDescriptor(key="iv", label="IV", panel_factory=IVAnalysisPanel),
]
