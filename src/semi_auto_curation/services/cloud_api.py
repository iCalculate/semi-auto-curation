from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.error import URLError
from urllib.request import Request, urlopen


DEFAULT_CLOUD_BASE_URL = "https://probe.icalculate.website"
DEFAULT_CLOUD_ACCESS_TOKEN = "GEMsE70403"
DEFAULT_CLOUD_TIMEOUT_S = 20
DEFAULT_FILE_TIMEOUT_S = 60
REMOTE_PREVIEW_LIMIT = 12

TEXT_SUFFIXES = {".csv", ".json", ".txt", ".log", ".md"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff"}


@dataclass(slots=True)
class CloudServiceConfig:
    base_url: str = DEFAULT_CLOUD_BASE_URL
    access_token: str = DEFAULT_CLOUD_ACCESS_TOKEN
    timeout_s: int = DEFAULT_CLOUD_TIMEOUT_S


@dataclass(slots=True)
class CloudSessionSelection:
    config: CloudServiceConfig
    session_id: str
    required_category: str
    summary: dict[str, Any]
    detail: dict[str, Any] | None = None


@dataclass(slots=True)
class CloudSyncResult:
    selection: CloudSessionSelection
    cache_root: Path
    source_dir: Path
    detail: dict[str, Any]


def list_sessions(config: CloudServiceConfig, limit: int = 100) -> dict[str, Any]:
    return _request_json(config, f"/api/autotest/sessions?limit={limit}")


def get_status(config: CloudServiceConfig) -> dict[str, Any]:
    return _request_json(config, "/api/status")


def get_session_detail(config: CloudServiceConfig, session_id: str) -> dict[str, Any]:
    return _request_json(config, f"/api/autotest/sessions/{quote(session_id, safe='')}")


def sync_session_category(
    selection: CloudSessionSelection,
    output_dir: Path,
    progress_callback=None,
) -> CloudSyncResult:
    detail = selection.detail or get_session_detail(selection.config, selection.session_id)
    selection.detail = detail
    summary = detail.get("summary", selection.summary)
    files = [entry for entry in detail.get("files", []) if entry.get("category") == selection.required_category]
    if not files:
        raise FileNotFoundError(f"No {selection.required_category} files found in cloud session {selection.session_id}")

    cache_root = output_dir / ".cloud_cache" / selection.session_id
    cache_root.mkdir(parents=True, exist_ok=True)
    _write_json(cache_root / ".cloud_session_detail.json", detail)

    total = len(files)
    for index, entry in enumerate(files, start=1):
        remote_path = str(entry["path"])
        destination = cache_root / remote_path
        if not _file_matches(destination, entry):
            download_session_file(selection.config, selection.session_id, remote_path, destination)
        if progress_callback is not None:
            progress_callback(index, total, f"Syncing {remote_path}")

    source_dir = cache_root / selection.required_category
    if not source_dir.exists():
        raise FileNotFoundError(f"Cloud session synced but source directory is missing: {source_dir}")

    _write_json(
        cache_root / ".cloud_selection.json",
        {
            "session_id": selection.session_id,
            "required_category": selection.required_category,
            "summary": summary,
        },
    )
    return CloudSyncResult(selection=selection, cache_root=cache_root, source_dir=source_dir, detail=detail)


def download_session_file(config: CloudServiceConfig, session_id: str, remote_path: str, destination: Path) -> Path:
    encoded_path = quote(remote_path, safe="/")
    url = f"{config.base_url.rstrip('/')}/api/autotest/sessions/{quote(session_id, safe='')}/files/{encoded_path}"
    request = _build_request(url, config.access_token)
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = _download_with_retry(request, timeout=max(DEFAULT_FILE_TIMEOUT_S, config.timeout_s + 10))
    destination.write_bytes(content)
    return destination


def read_session_json(config: CloudServiceConfig, session_id: str, remote_path: str) -> Any:
    encoded_path = quote(remote_path, safe="/")
    return _request_json(config, f"/api/autotest/sessions/{quote(session_id, safe='')}/json/{encoded_path}")


def read_session_text(config: CloudServiceConfig, session_id: str, remote_path: str) -> str:
    encoded_path = quote(remote_path, safe="/")
    text = _request_text(config, f"/api/autotest/sessions/{quote(session_id, safe='')}/text/{encoded_path}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(payload, dict) and "content" in payload:
        return str(payload.get("content", ""))
    return text


def related_session_files(detail: dict[str, Any], device_name: str, primary_paths: list[str], limit: int = REMOTE_PREVIEW_LIMIT) -> list[dict[str, Any]]:
    primary = {path.replace("\\", "/") for path in primary_paths if path}
    candidates: list[dict[str, Any]] = []
    pattern = re.compile(rf"^{re.escape(device_name.lower())}(?:$|[^a-z0-9])")
    for entry in detail.get("files", []):
        remote_path = str(entry.get("path", ""))
        if remote_path in primary:
            continue
        file_name = str(entry.get("name", "")).lower()
        if pattern.match(file_name):
            candidates.append(entry)
    candidates.sort(key=_preview_sort_key)
    return candidates[:limit]


def cached_preview_path(cache_root: Path, remote_path: str) -> Path:
    return cache_root / ".preview_cache" / remote_path


def ensure_remote_preview_file(
    config: CloudServiceConfig,
    session_id: str,
    cache_root: Path,
    remote_path: str,
) -> Path:
    destination = cached_preview_path(cache_root, remote_path)
    if destination.exists():
        return destination
    return download_session_file(config, session_id, remote_path, destination)


def preview_text_for_entry(config: CloudServiceConfig, session_id: str, entry: dict[str, Any]) -> str:
    remote_path = str(entry.get("path", ""))
    suffix = Path(remote_path).suffix.lower()
    if suffix == ".json":
        try:
            payload = read_session_json(config, session_id, remote_path)
            return json.dumps(payload, indent=2, ensure_ascii=False)[:2000]
        except Exception:
            return "JSON preview unavailable."
    if suffix in TEXT_SUFFIXES:
        try:
            return read_session_text(config, session_id, remote_path)[:2000]
        except Exception:
            return "Text preview unavailable."
    return f"Remote file: {remote_path}\nSize: {entry.get('size_bytes', 0)} bytes"


def _preview_sort_key(entry: dict[str, Any]) -> tuple[int, str]:
    category = str(entry.get("category", "other"))
    order = {"images": 0, "other": 1, "iv": 2, "b1500": 3, "wobb": 4}
    return (order.get(category, 9), str(entry.get("path", "")))


def _request_json(config: CloudServiceConfig, api_path: str) -> dict[str, Any]:
    text = _request_text(config, api_path)
    return json.loads(text)


def _request_text(config: CloudServiceConfig, api_path: str) -> str:
    url = f"{config.base_url.rstrip('/')}{api_path}"
    request = _build_request(url, config.access_token)
    with urlopen(request, timeout=config.timeout_s) as response:
        return response.read().decode("utf-8")


def _build_request(url: str, access_token: str) -> Request:
    return Request(
        url,
        headers={
            "X-Access-Token": access_token,
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
        },
    )


def _file_matches(destination: Path, entry: dict[str, Any]) -> bool:
    if not destination.exists():
        return False
    expected_size = int(entry.get("size_bytes", 0) or 0)
    return destination.stat().st_size == expected_size


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _download_with_retry(request: Request, timeout: int, attempts: int = 3) -> bytes:
    last_error: Exception | None = None
    for _attempt in range(attempts):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (TimeoutError, URLError, OSError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise TimeoutError("Unknown download error.")
