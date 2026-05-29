from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from semi_auto_curation.data.loader import discover_iv_files, load_measurement
from semi_auto_curation.models import DeviceMeasurement, DeviceMetadata, IVPoint


class IVCacheDatabase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.connection.close()

    def load_measurements(self, source_dir: Path, progress_callback=None) -> list[DeviceMeasurement]:
        csv_files = discover_iv_files(source_dir)
        if not csv_files:
            raise FileNotFoundError(f"No *_iv.csv files found in {source_dir}")
        total = len(csv_files)
        for index, csv_path in enumerate(csv_files, start=1):
            self._ensure_cached(csv_path)
            if progress_callback is not None:
                progress_callback(index, total, f"Caching {csv_path.name}")
        return [self._read_measurement(csv_path) for csv_path in csv_files]

    def build_cache(self, source_dir: Path, progress_callback=None) -> int:
        csv_files = discover_iv_files(source_dir)
        if not csv_files:
            raise FileNotFoundError(f"No *_iv.csv files found in {source_dir}")
        total = len(csv_files)
        for index, csv_path in enumerate(csv_files, start=1):
            self._ensure_cached(csv_path)
            if progress_callback is not None:
                progress_callback(index, total, f"Building cache for {csv_path.name}")
        return total

    def _init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS measurements (
                csv_path TEXT PRIMARY KEY,
                json_path TEXT,
                csv_mtime_ns INTEGER NOT NULL,
                json_mtime_ns INTEGER,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS points (
                csv_path TEXT NOT NULL,
                point_index INTEGER NOT NULL,
                elapsed_s REAL NOT NULL,
                source_value REAL NOT NULL,
                voltage_v REAL NOT NULL,
                current_a REAL NOT NULL,
                resistance_ohm REAL NOT NULL,
                PRIMARY KEY (csv_path, point_index)
            );
            CREATE INDEX IF NOT EXISTS idx_points_csv_path ON points (csv_path);
            """
        )
        self.connection.commit()

    def _ensure_cached(self, csv_path: Path) -> None:
        json_path = csv_path.with_suffix(".json")
        csv_mtime_ns = csv_path.stat().st_mtime_ns
        json_mtime_ns = json_path.stat().st_mtime_ns if json_path.exists() else None
        row = self.connection.execute(
            "SELECT csv_mtime_ns, json_mtime_ns FROM measurements WHERE csv_path = ?",
            (str(csv_path),),
        ).fetchone()
        if row and row["csv_mtime_ns"] == csv_mtime_ns and row["json_mtime_ns"] == json_mtime_ns:
            return
        measurement = load_measurement(csv_path)
        metadata_payload = _metadata_to_dict(measurement.metadata)
        with self.connection:
            self.connection.execute("DELETE FROM points WHERE csv_path = ?", (str(csv_path),))
            self.connection.execute("DELETE FROM measurements WHERE csv_path = ?", (str(csv_path),))
            self.connection.execute(
                """
                INSERT INTO measurements (csv_path, json_path, csv_mtime_ns, json_mtime_ns, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(csv_path),
                    str(measurement.json_path) if measurement.json_path else None,
                    csv_mtime_ns,
                    json_mtime_ns,
                    json.dumps(metadata_payload),
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO points (csv_path, point_index, elapsed_s, source_value, voltage_v, current_a, resistance_ohm)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(csv_path),
                        point.index,
                        point.elapsed_s,
                        point.source_value,
                        point.voltage_v,
                        point.current_a,
                        point.resistance_ohm,
                    )
                    for point in measurement.points
                ],
            )

    def _read_measurement(self, csv_path: Path) -> DeviceMeasurement:
        header = self.connection.execute(
            "SELECT json_path, metadata_json FROM measurements WHERE csv_path = ?",
            (str(csv_path),),
        ).fetchone()
        if header is None:
            raise FileNotFoundError(f"Cached measurement missing for {csv_path}")
        metadata = _metadata_from_dict(json.loads(header["metadata_json"]))
        point_rows = self.connection.execute(
            """
            SELECT point_index, elapsed_s, source_value, voltage_v, current_a, resistance_ohm
            FROM points
            WHERE csv_path = ?
            ORDER BY point_index
            """,
            (str(csv_path),),
        ).fetchall()
        points = [
            IVPoint(
                index=row["point_index"],
                elapsed_s=row["elapsed_s"],
                source_value=row["source_value"],
                voltage_v=row["voltage_v"],
                current_a=row["current_a"],
                resistance_ohm=row["resistance_ohm"],
            )
            for row in point_rows
        ]
        return DeviceMeasurement(
            csv_path=csv_path,
            json_path=Path(header["json_path"]) if header["json_path"] else None,
            metadata=metadata,
            points=points,
        )


def _metadata_to_dict(metadata: DeviceMetadata) -> dict[str, object]:
    return {
        "device_name": metadata.device_name,
        "row": metadata.row,
        "col": metadata.col,
        "order": metadata.order,
        "created_at": metadata.created_at,
        "stage_x_um": metadata.stage_x_um,
        "stage_y_um": metadata.stage_y_um,
        "start_v": metadata.start_v,
        "stop_v": metadata.stop_v,
        "step_v": metadata.step_v,
        "current_limit_a": metadata.current_limit_a,
        "extra": metadata.extra,
    }


def _metadata_from_dict(payload: dict[str, object]) -> DeviceMetadata:
    return DeviceMetadata(
        device_name=str(payload["device_name"]),
        row=payload.get("row"),
        col=payload.get("col"),
        order=payload.get("order"),
        created_at=payload.get("created_at"),
        stage_x_um=payload.get("stage_x_um"),
        stage_y_um=payload.get("stage_y_um"),
        start_v=payload.get("start_v"),
        stop_v=payload.get("stop_v"),
        step_v=payload.get("step_v"),
        current_limit_a=payload.get("current_limit_a"),
        extra=payload.get("extra", {}),
    )
