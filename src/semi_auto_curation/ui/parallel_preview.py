from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from semi_auto_curation.services.cloud_api import (
    CloudServiceConfig,
    IMAGE_SUFFIXES,
    TEXT_SUFFIXES,
    ensure_remote_preview_file,
    preview_text_for_entry,
    related_session_files,
)


class ParallelPreviewPanel(QGroupBox):
    def __init__(self, title: str = "Parallel Preview") -> None:
        super().__init__(title)
        self.state_label = QLabel("Select one device to preview parallel files.")
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.cards_layout = QHBoxLayout(self.content)
        self.cards_layout.setContentsMargins(8, 8, 8, 8)
        self.cards_layout.setSpacing(12)
        self.cards_layout.addStretch(1)
        self.scroll_area.setWidget(self.content)

        layout = QVBoxLayout(self)
        layout.addWidget(self.state_label)
        layout.addWidget(self.scroll_area, 1)

    def clear_preview(self, message: str = "Select one device to preview parallel files.") -> None:
        self.state_label.setText(message)
        self._clear_cards()

    def update_preview(self, source_dir: Path, device_name: str, primary_files: list[str]) -> None:
        session_root = source_dir.parent if source_dir.name.lower() in {"iv", "b1500", "images", "wobb"} else source_dir
        primary_paths = {str(Path(path).resolve()) for path in primary_files if path}
        related = []
        for path in session_root.rglob(f"{device_name}*"):
            if not path.is_file():
                continue
            resolved = str(path.resolve())
            if resolved in primary_paths:
                continue
            related.append(path)
        related = sorted(related)[:12]
        self._clear_cards()
        if not related:
            self.state_label.setText(f"{device_name}: no additional parallel files found in {session_root.name}.")
            return
        self.state_label.setText(f"{device_name}: {len(related)} local parallel file(s) found in {session_root.name}.")
        for path in related:
            self.cards_layout.addWidget(_build_local_card(path))
        self.cards_layout.addStretch(1)

    def update_cloud_preview(
        self,
        config: CloudServiceConfig,
        session_id: str,
        detail: dict,
        cache_root: Path,
        device_name: str,
        primary_files: list[str],
    ) -> None:
        related = related_session_files(detail, device_name, primary_files)
        self._clear_cards()
        if not related:
            self.state_label.setText(f"{device_name}: no additional cloud files found in {session_id}.")
            return
        self.state_label.setText(f"{device_name}: {len(related)} cloud parallel file(s) found in {session_id}.")
        for entry in related:
            self.cards_layout.addWidget(_build_remote_card(config, session_id, cache_root, entry))
        self.cards_layout.addStretch(1)

    def _clear_cards(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()


def _build_local_card(path: Path) -> QWidget:
    frame = _card_frame(path.name)
    layout = frame.layout()
    assert isinstance(layout, QVBoxLayout)
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        layout.addWidget(_image_label(path), 1)
    else:
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setMinimumHeight(150)
        preview.setPlainText(_local_preview_text(path, suffix))
        layout.addWidget(preview, 1)
    layout.addWidget(_footer_label(str(path.parent.name)))
    return frame


def _build_remote_card(config: CloudServiceConfig, session_id: str, cache_root: Path, entry: dict) -> QWidget:
    remote_path = str(entry.get("path", ""))
    frame = _card_frame(str(entry.get("name", remote_path)))
    layout = frame.layout()
    assert isinstance(layout, QVBoxLayout)
    suffix = Path(remote_path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        try:
            preview_path = ensure_remote_preview_file(config, session_id, cache_root, remote_path)
            layout.addWidget(_image_label(preview_path), 1)
        except Exception:
            fallback = QLabel("Cloud image preview unavailable")
            fallback.setAlignment(Qt.AlignCenter)
            layout.addWidget(fallback, 1)
    else:
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setMinimumHeight(150)
        preview.setPlainText(_clip_preview(preview_text_for_entry(config, session_id, entry), suffix))
        layout.addWidget(preview, 1)
    layout.addWidget(_footer_label(remote_path))
    return frame


def _card_frame(title_text: str) -> QFrame:
    frame = QFrame()
    frame.setFrameShape(QFrame.StyledPanel)
    frame.setMinimumWidth(240)
    frame.setMaximumWidth(240)
    frame.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
    layout = QVBoxLayout(frame)
    title = QLabel(title_text)
    title.setWordWrap(True)
    title.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    layout.addWidget(title)
    return frame


def _image_label(path: Path) -> QLabel:
    pixmap = QPixmap(str(path))
    image = QLabel()
    image.setAlignment(Qt.AlignCenter)
    if not pixmap.isNull():
        image.setPixmap(pixmap.scaled(200, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    else:
        image.setText("Image preview unavailable")
    return image


def _footer_label(text: str) -> QLabel:
    footer = QLabel(text)
    footer.setWordWrap(True)
    footer.setAlignment(Qt.AlignBottom | Qt.AlignLeft)
    return footer


def _local_preview_text(path: Path, suffix: str) -> str:
    if suffix in TEXT_SUFFIXES:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = ["Preview unavailable."]
        snippet = "\n".join(lines[:8]).strip()
        return snippet or "(empty file)"
    size = path.stat().st_size if path.exists() else 0
    return f"Path: {path}\nSize: {size} bytes"


def _clip_preview(text: str, suffix: str) -> str:
    if suffix in TEXT_SUFFIXES:
        return "\n".join(text.splitlines()[:8]).strip() or "(empty file)"
    return text[:600]
