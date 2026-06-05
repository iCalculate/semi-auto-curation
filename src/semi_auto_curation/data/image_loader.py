"""Image file discovery and per-device feature extraction.

Device images are expected to live in a source folder with filenames that
embed the device name, e.g.::

    A1_top.png          → device "A1"
    B3_dark.tif         → device "B3"
    C5.jpg              → device "C5"

The first '_'-delimited token (or the full stem if no '_' present) is taken
as the device name.  Multiple images for the same device are all discovered
and the first one is used for metric computation.

Computed metrics (all optional – return None when computation fails):
  - mean_brightness:     mean pixel intensity of the grayscale image [0, 1]
  - contrast_std:        standard deviation of pixel intensities [0, 1]
  - sharpness_laplacian: variance of the gradient magnitude (Laplacian focus)
  - histogram_entropy:   Shannon entropy of the 8-bit intensity histogram [bits]
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
_DEVICE_NAME_RE = re.compile(r"^([A-Za-z]+\d+|[A-Za-z]\d+)")


def discover_image_files(source_dir: Path) -> dict[str, list[Path]]:
    """Return {device_name: [image_paths …]} for every image in *source_dir*."""
    groups: dict[str, list[Path]] = {}
    for path in sorted(source_dir.iterdir()):
        if path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        stem = path.stem
        device_name = stem.split("_")[0] if "_" in stem else stem
        groups.setdefault(device_name, []).append(path)
    return groups


def parse_device_position(device_name: str) -> tuple[int, int] | None:
    """Parse a lab-convention device name into a (row, col) tuple.

    Supports:
      - "A1"  → (0, 0)   (letter = row A→0, B→1 …; digit(s) = col, 1-indexed)
      - "B12" → (1, 11)
      - Names that don't match return None.
    """
    m = _DEVICE_NAME_RE.match(device_name.strip())
    if not m:
        return None
    token = m.group(1)
    # Split into leading letters and trailing digits
    letter_part = "".join(c for c in token if c.isalpha()).upper()
    digit_part = "".join(c for c in token if c.isdigit())
    if not letter_part or not digit_part:
        return None
    # Convert letter sequence to row index (A→0, B→1, …, Z→25, AA→26 …)
    row = 0
    for ch in letter_part:
        row = row * 26 + (ord(ch) - ord("A") + 1)
    row -= 1  # make 0-indexed
    col = int(digit_part) - 1  # make 0-indexed
    return (row, col)


def infer_positions(device_names: list[str]) -> dict[str, tuple[int, int]]:
    """Build a positions dict for as many names as can be parsed."""
    result: dict[str, tuple[int, int]] = {}
    for name in device_names:
        pos = parse_device_position(name)
        if pos is not None:
            result[name] = pos
    return result


# ---------------------------------------------------------------------------
# Image loading (uses matplotlib which is already a dependency)
# ---------------------------------------------------------------------------

def _load_grayscale(path: Path) -> np.ndarray | None:
    """Load *path* and return a float32 grayscale array in [0, 1].

    Falls back gracefully when the image cannot be read.
    """
    try:
        import matplotlib.image as mpimg  # lazy – matplotlib is required
        arr = mpimg.imread(str(path))
    except Exception:
        return None

    if arr is None:
        return None

    arr = arr.astype(np.float32)

    # Normalise uint8 images (values 0-255 → 0-1)
    if arr.max() > 1.0:
        arr = arr / 255.0

    # Collapse colour channels to grayscale
    if arr.ndim == 3:
        arr = np.mean(arr[..., :3], axis=2)

    return arr


# ---------------------------------------------------------------------------
# Per-device metric computation
# ---------------------------------------------------------------------------

_METRIC_KEYS = [
    "mean_brightness",
    "contrast_std",
    "sharpness_laplacian",
    "histogram_entropy",
]


def compute_image_metrics(image_path: Path) -> dict[str, float | None]:
    """Return a dict with the four standard image metrics for *image_path*."""
    metrics: dict[str, float | None] = {k: None for k in _METRIC_KEYS}
    arr = _load_grayscale(image_path)
    if arr is None:
        return metrics

    metrics["mean_brightness"] = float(np.mean(arr))
    metrics["contrast_std"] = float(np.std(arr))

    # Laplacian focus measure: variance of gradient magnitude
    gy, gx = np.gradient(arr.astype(np.float64))
    metrics["sharpness_laplacian"] = float(np.var(np.hypot(gx, gy)))

    # Shannon entropy of 8-bit histogram
    arr8 = (arr * 255).clip(0, 255).astype(np.uint8)
    hist, _ = np.histogram(arr8.ravel(), bins=256, range=(0, 256))
    total = hist.sum()
    if total > 0:
        probs = hist[hist > 0] / total
        metrics["histogram_entropy"] = float(-np.sum(probs * np.log2(probs)))

    return metrics


def run_image_batch(
    source_dir: Path,
    metric: str = "mean_brightness",
    progress_callback=None,
) -> tuple[
    dict[str, float | None],           # {device_name: selected_metric_value}
    dict[str, tuple[int, int]],         # {device_name: (row, col)}
    dict[str, dict[str, float | None]], # {device_name: all_metrics}
    dict[str, list[Path]],              # {device_name: [image_paths]}
]:
    """Discover images in *source_dir* and compute all metrics for every device.

    All heavy I/O is done here so callers (workers) can run this off the
    main thread.  Returns four dicts so the UI can switch metrics without
    re-loading images.
    """
    device_files = discover_image_files(source_dir)
    positions = infer_positions(list(device_files.keys()))
    values: dict[str, float | None] = {}
    all_metrics: dict[str, dict[str, float | None]] = {}

    total = len(device_files)
    for idx, (name, paths) in enumerate(device_files.items()):
        m = compute_image_metrics(paths[0]) if paths else {k: None for k in _METRIC_KEYS}
        all_metrics[name] = m
        values[name] = m.get(metric)
        if progress_callback:
            progress_callback(int((idx + 1) * 100 / max(total, 1)), name)

    return values, positions, all_metrics, device_files
