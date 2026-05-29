from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from semi_auto_curation.analysis.iv import run_iv_batch
from semi_auto_curation.models import IVAnalysisSettings
from semi_auto_curation.ui.main_window import launch
from semi_auto_curation.utils.logging import log_error, log_info, print_banner
from semi_auto_curation.utils.units import parse_si_number


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print_banner()
    log_info("Semi-Auto Curation Studio entrypoint started.")
    if args.command in (None, "gui"):
        log_info("Launching GUI workbench.")
        launch()
        return
    if args.command == "analyze" and args.data_type == "iv":
        settings = IVAnalysisSettings(
            source_dir=Path(args.source),
            output_dir=Path(args.output),
            fit_voltage_min=args.fit_min,
            fit_voltage_max=args.fit_max,
            dummy_min_resistance_ohm=args.dummy_r_min,
            dummy_max_resistance_ohm=args.dummy_r_max,
            dummy_min_r2=args.dummy_r2,
        )
        log_info(
            f"Running CLI IV analysis. source={settings.source_dir} output={settings.output_dir} fit_window=({settings.fit_voltage_min}, {settings.fit_voltage_max})"
        )
        batch = run_iv_batch(settings)
        log_info(
            f"CLI IV analysis completed. total_devices={batch.summary.total_devices} valid_devices={batch.summary.valid_devices} dummy_devices={batch.summary.dummy_devices}"
        )
        print(json.dumps({"summary": asdict(batch.summary)}, indent=2))
        return
    log_error("Unsupported command.")
    parser.error("Unsupported command.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="semi-auto-curation")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("gui", help="Launch the Qt GUI workbench.")

    analyze = subparsers.add_parser("analyze", help="Run CLI analysis.")
    analyze_sub = analyze.add_subparsers(dest="data_type")
    iv = analyze_sub.add_parser("iv", help="Analyze IV sweep data.")
    iv.add_argument("--source", default=str(Path.cwd() / "rawdata" / "iv"))
    iv.add_argument("--output", default=str(Path.cwd() / "output"))
    iv.add_argument("--fit-min", type=parse_si_number, default=0.0)
    iv.add_argument("--fit-max", type=parse_si_number, default=1.2)
    iv.add_argument("--dummy-r-min", type=parse_si_number, default=None)
    iv.add_argument("--dummy-r-max", type=parse_si_number, default=None)
    iv.add_argument("--dummy-r2", type=parse_si_number, default=0.0)
    return parser
