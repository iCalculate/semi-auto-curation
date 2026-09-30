from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable


SESSION_NAME_RE = re.compile(r"^(\d{8})_(\d{6})(?:_\d+)?$")
FAILED_STATES = {"failed", "failure", "error", "aborted"}


@dataclass(frozen=True, slots=True)
class CategorySummary:
    name: str
    file_count: int = 0
    size_bytes: int = 0
    csv_count: int = 0
    json_count: int = 0
    image_count: int = 0
    device_count: int = 0
    failed_count: int = 0


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    path: Path
    started_at: datetime
    ended_at: datetime | None
    status: str
    planned_count: int
    completed_count: int
    file_count: int
    size_bytes: int
    devices: tuple[str, ...]
    categories: tuple[CategorySummary, ...]
    warnings: tuple[str, ...] = ()

    @property
    def progress_text(self) -> str:
        if self.planned_count:
            return f"{self.completed_count}/{self.planned_count}"
        return "—"


def parse_session_timestamp(name: str) -> datetime | None:
    match = SESSION_NAME_RE.fullmatch(name)
    if not match:
        return None
    try:
        return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def scan_session(session_dir: Path) -> SessionRecord | None:
    started_at = parse_session_timestamp(session_dir.name)
    if started_at is None or not session_dir.is_dir():
        return None

    category_data: dict[str, dict[str, object]] = {}
    devices: set[str] = set()
    warnings: list[str] = []
    planned_count = 0
    completed_count = 0
    total_files = 0
    total_size = 0
    latest_mtime: float | None = None
    failed_count = 0

    for directory, _subdirs, filenames in os.walk(session_dir):
        directory_path = Path(directory)
        relative = directory_path.relative_to(session_dir)
        category = relative.parts[0].lower() if relative.parts else "root"
        bucket = category_data.setdefault(
            category,
            {
                "file_count": 0,
                "size_bytes": 0,
                "csv_count": 0,
                "json_count": 0,
                "image_count": 0,
                "devices": set(),
                "failed_count": 0,
            },
        )
        for filename in filenames:
            path = directory_path / filename
            try:
                stat = path.stat()
            except OSError as exc:
                warnings.append(f"Could not inspect {path.name}: {exc}")
                continue
            total_files += 1
            total_size += stat.st_size
            latest_mtime = stat.st_mtime if latest_mtime is None else max(latest_mtime, stat.st_mtime)
            bucket["file_count"] = int(bucket["file_count"]) + 1
            bucket["size_bytes"] = int(bucket["size_bytes"]) + stat.st_size
            suffix = path.suffix.lower()
            if suffix == ".csv":
                bucket["csv_count"] = int(bucket["csv_count"]) + 1
            elif suffix == ".json":
                bucket["json_count"] = int(bucket["json_count"]) + 1
            elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
                bucket["image_count"] = int(bucket["image_count"]) + 1

            if path.name == "autotest_resume.json":
                try:
                    payload = _read_json(path)
                    planned_count = len(payload.get("points") or [])
                    completed_count = len(payload.get("completed") or [])
                    for point in payload.get("points") or []:
                        if isinstance(point, dict) and point.get("name"):
                            devices.add(str(point["name"]))
                except (OSError, ValueError, TypeError) as exc:
                    warnings.append(f"Invalid autotest_resume.json: {exc}")
                continue

            # Plot histories and point caches dominate large sessions but do not
            # carry authoritative result status.  Count them without parsing;
            # the manifest already supplies the planned device names.
            if suffix == ".json" and category not in {"workflow_plots", "points", "root"}:
                try:
                    payload = _read_json(path)
                except (OSError, ValueError, TypeError) as exc:
                    warnings.append(f"Invalid JSON {path.relative_to(session_dir)}: {exc}")
                    continue
                device = payload.get("device")
                device_name = device.get("name") if isinstance(device, dict) else None
                if device_name:
                    normalized = str(device_name)
                    devices.add(normalized)
                    cast_devices = bucket["devices"]
                    assert isinstance(cast_devices, set)
                    cast_devices.add(normalized)
                status = str(payload.get("status", "")).strip().lower()
                if status in FAILED_STATES:
                    failed_count += 1
                    bucket["failed_count"] = int(bucket["failed_count"]) + 1

    data_file_count = total_files - int(category_data.get("root", {}).get("file_count", 0))
    if failed_count:
        status = "Failed"
    elif planned_count and completed_count >= planned_count:
        status = "Complete"
    elif planned_count:
        status = "Stopped"
    elif data_file_count <= 0:
        status = "Unknown"
    else:
        status = "Unknown"

    categories = tuple(
        CategorySummary(
            name=name,
            file_count=int(values["file_count"]),
            size_bytes=int(values["size_bytes"]),
            csv_count=int(values["csv_count"]),
            json_count=int(values["json_count"]),
            image_count=int(values["image_count"]),
            device_count=len(values["devices"]),
            failed_count=int(values["failed_count"]),
        )
        for name, values in sorted(category_data.items())
        if name != "root" and int(values["file_count"])
    )
    ended_at = datetime.fromtimestamp(latest_mtime) if latest_mtime is not None else None
    return SessionRecord(
        session_id=session_dir.name,
        path=session_dir.resolve(),
        started_at=started_at,
        ended_at=ended_at,
        status=status,
        planned_count=planned_count,
        completed_count=completed_count,
        file_count=total_files,
        size_bytes=total_size,
        devices=tuple(sorted(devices, key=str.casefold)),
        categories=categories,
        warnings=tuple(warnings),
    )


def refresh_index(
    session_root: Path,
    database_path: Path,
    progress_callback: Callable[[int, str], None] | None = None,
) -> tuple[list[SessionRecord], list[str]]:
    if not session_root.is_dir():
        raise FileNotFoundError(f"Session root does not exist: {session_root}")
    candidates = sorted(
        (path for path in session_root.iterdir() if path.is_dir() and parse_session_timestamp(path.name)),
        key=lambda path: path.name,
    )
    records: list[SessionRecord] = []
    warnings: list[str] = []
    total = max(1, len(candidates))
    for index, path in enumerate(candidates, 1):
        try:
            record = scan_session(path)
            if record is not None:
                records.append(record)
                warnings.extend(f"{record.session_id}: {warning}" for warning in record.warnings)
        except OSError as exc:
            warnings.append(f"{path.name}: {exc}")
        if progress_callback:
            progress_callback(round(index * 90 / total), f"Scanning {path.name}")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        _create_schema(connection)
        with connection:
            connection.execute("DELETE FROM sessions")
            connection.executemany(
                """
                INSERT INTO sessions (
                    session_id, path, started_at, ended_at, status,
                    planned_count, completed_count, file_count, size_bytes,
                    devices_json, categories_json, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [_record_row(record) for record in records],
            )
    finally:
        connection.close()
    if progress_callback:
        progress_callback(100, f"Indexed {len(records)} sessions")
    return records, warnings


def load_index(database_path: Path) -> list[SessionRecord]:
    if not database_path.is_file():
        return []
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        _create_schema(connection)
        rows = connection.execute("SELECT * FROM sessions ORDER BY started_at").fetchall()
    finally:
        connection.close()
    return [_row_record(row) for row in rows]


def filter_sessions(
    records: Iterable[SessionRecord],
    *,
    query: str = "",
    category: str = "All categories",
    status: str = "All statuses",
) -> list[SessionRecord]:
    needle = query.strip().casefold()
    result: list[SessionRecord] = []
    for record in records:
        category_names = {item.name for item in record.categories}
        if category != "All categories" and category not in category_names:
            continue
        if status != "All statuses" and status != record.status:
            continue
        haystack = " ".join(
            (
                record.session_id,
                record.status,
                record.started_at.strftime("%Y-%m-%d %H:%M:%S"),
                *category_names,
                *record.devices,
            )
        ).casefold()
        if needle and needle not in haystack:
            continue
        result.append(record)
    return result


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("expected a JSON object")
    return payload


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            status TEXT NOT NULL,
            planned_count INTEGER NOT NULL,
            completed_count INTEGER NOT NULL,
            file_count INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL,
            devices_json TEXT NOT NULL,
            categories_json TEXT NOT NULL,
            warnings_json TEXT NOT NULL
        )
        """
    )


def _record_row(record: SessionRecord) -> tuple:
    return (
        record.session_id,
        str(record.path),
        record.started_at.isoformat(),
        record.ended_at.isoformat() if record.ended_at else None,
        record.status,
        record.planned_count,
        record.completed_count,
        record.file_count,
        record.size_bytes,
        json.dumps(record.devices, ensure_ascii=False),
        json.dumps([asdict(item) for item in record.categories], ensure_ascii=False),
        json.dumps(record.warnings, ensure_ascii=False),
    )


def _row_record(row: sqlite3.Row) -> SessionRecord:
    categories = tuple(CategorySummary(**item) for item in json.loads(row["categories_json"]))
    status = {"Partial": "Stopped", "Empty": "Unknown"}.get(row["status"], row["status"])
    return SessionRecord(
        session_id=row["session_id"],
        path=Path(row["path"]),
        started_at=datetime.fromisoformat(row["started_at"]),
        ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
        status=status,
        planned_count=row["planned_count"],
        completed_count=row["completed_count"],
        file_count=row["file_count"],
        size_bytes=row["size_bytes"],
        devices=tuple(json.loads(row["devices_json"])),
        categories=categories,
        warnings=tuple(json.loads(row["warnings_json"])),
    )
