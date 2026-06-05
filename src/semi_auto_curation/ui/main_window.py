from __future__ import annotations

import os
from importlib import metadata

import psutil
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QCursor, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMdiArea,
    QMdiSubWindow,
    QMenu,
    QProgressBar,
    QScrollArea,
    QStackedWidget,
    QStatusBar,
    QTextBrowser,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from semi_auto_curation.analyzers.registry import ANALYZER_REGISTRY


# ---------------------------------------------------------------------------
# Helper: VS-style auto-hide side tab shown when dock is collapsed
# ---------------------------------------------------------------------------

class _SideTab(QWidget):
    """Narrow vertical tab used as the collapsed dock strip.

    Renders the workspace label rotated 90° (reads bottom-to-top), with a
    subtle right-edge separator line, matching the Visual Studio auto-hide
    tab aesthetic.
    """

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{text} (hover to expand)")

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(26, fm.horizontalAdvance(self._text) + 32)

    def minimumSizeHint(self) -> QSize:
        return QSize(26, 80)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Background — match the dock/panel background
        p.fillRect(self.rect(), self.palette().window())

        # Right-edge separator (faces the MDI area)
        sep_col = self.palette().mid().color()
        p.setPen(QPen(sep_col, 1))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())

        # Rotated label text (bottom-to-top, centred)
        p.save()
        p.setPen(self.palette().text().color())
        p.setFont(self.font())
        # Translate to centre, rotate so text reads bottom→top
        p.translate(self.width() / 2.0, self.height() / 2.0)
        p.rotate(-90)
        p.drawText(
            QRect(-self.height() // 2, -self.width() // 2,
                  self.height(), self.width()),
            Qt.AlignCenter | Qt.TextDontClip,
            self._text,
        )
        p.restore()


# ---------------------------------------------------------------------------
# Pin-icon factory (clean line-art thumbtack, no emoji)
# ---------------------------------------------------------------------------

def _pin_icon(pinned: bool) -> QIcon:
    """Draw a minimal thumbtack icon.

    Pinned  → vertical pin (stuck in place, always visible).
    Unpinned → horizontal pin (free to slide / auto-hide).
    """
    sz = 14
    pm = QPixmap(sz, sz)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    if pinned:
        col = QColor(80, 80, 80)          # darker when "active/locked"
        p.setPen(QPen(col, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(col)
        # Head: small circle at top
        p.drawEllipse(3, 0, 8, 8)
        # Shaft: line pointing down
        p.setPen(QPen(col, 2.0, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(7, 8, 7, 13)
    else:
        col = QColor(150, 150, 150)       # lighter when "inactive/free"
        p.setPen(QPen(col, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(col)
        # Head: small circle at the right
        p.drawEllipse(6, 3, 8, 8)
        # Shaft: line pointing left
        p.setPen(QPen(col, 2.0, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(1, 7, 6, 7)

    p.end()
    return QIcon(pm)


def _close_icon() -> QIcon:
    """Crisp × icon."""
    sz = 14
    pm = QPixmap(sz, sz)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    col = QColor(120, 120, 120)
    p.setPen(QPen(col, 1.5, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(3, 3, sz - 3, sz - 3)
    p.drawLine(sz - 3, 3, 3, sz - 3)
    p.end()
    return QIcon(pm)


def _expand_icon() -> QIcon:
    """'Bring to front' arrow icon (↗)."""
    sz = 14
    pm = QPixmap(sz, sz)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    col = QColor(110, 110, 110)
    p.setPen(QPen(col, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    # Arrow shaft diagonal
    p.drawLine(3, 11, 10, 4)
    # Arrowhead
    p.drawLine(5, 3, 11, 3)
    p.drawLine(11, 3, 11, 9)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------------------
# Workspace row — one entry in the workspace list inside the dock
# ---------------------------------------------------------------------------

class _WorkspaceRow(QWidget):
    """Workspace list entry with inline rename (F2 or slow double-click)."""

    _RENAME_DELAY_MS = 550

    def __init__(self, title: str, sub_window: QMdiSubWindow, parent=None) -> None:
        super().__init__(parent)
        self._sub_window = sub_window
        self._active = False
        self._editing = False
        self._original_name = title
        self.setAutoFillBackground(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setFixedHeight(28)

        self._rename_timer = QTimer(self)
        self._rename_timer.setSingleShot(True)
        self._rename_timer.setInterval(self._RENAME_DELAY_MS)
        self._rename_timer.timeout.connect(self.start_edit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 3, 3)
        layout.setSpacing(2)

        # Name: label or inline editor
        self._name_stack = QStackedWidget()
        self._label = QLabel(title)
        self._editor = QLineEdit(title)
        self._editor.setFrame(False)
        self._editor.installEventFilter(self)
        self._editor.returnPressed.connect(self._commit_edit)
        self._editor.editingFinished.connect(self._on_editing_finished)
        self._name_stack.addWidget(self._label)   # 0 = display
        self._name_stack.addWidget(self._editor)  # 1 = edit
        self._name_stack.setCurrentIndex(0)
        layout.addWidget(self._name_stack, 1)

        restore_btn = QToolButton()
        restore_btn.setFixedSize(18, 18)
        restore_btn.setIcon(_expand_icon())
        restore_btn.setToolTip("Restore / bring to front")
        restore_btn.clicked.connect(self._activate)
        layout.addWidget(restore_btn)

        close_btn = QToolButton()
        close_btn.setFixedSize(18, 18)
        close_btn.setIcon(_close_icon())
        close_btn.setToolTip("Close workspace")
        close_btn.clicked.connect(sub_window.close)
        layout.addWidget(close_btn)

    # -- appearance --------------------------------------------------------

    def set_active(self, active: bool) -> None:
        self._active = active
        pal = self.palette()
        if active:
            # Muted blue-gray instead of full system highlight (too vivid)
            pal.setColor(self.backgroundRole(), QColor(55, 85, 120))
            pal.setColor(self.foregroundRole(), QColor(220, 225, 235))
        else:
            pal.setColor(self.backgroundRole(), pal.window().color())
            pal.setColor(self.foregroundRole(), pal.windowText().color())
        self.setPalette(pal)
        self._label.setPalette(pal)
        if not active:
            self._rename_timer.stop()

    # -- activation --------------------------------------------------------

    def _activate(self) -> None:
        try:
            if self._sub_window.isMinimized():
                self._sub_window.showNormal()
            mdi = self._sub_window.mdiArea()
            if mdi:
                mdi.setActiveSubWindow(self._sub_window)
        except RuntimeError:
            pass

    # -- inline rename -----------------------------------------------------

    def start_edit(self) -> None:
        self._rename_timer.stop()
        self._original_name = self._label.text()
        self._editor.setText(self._original_name)
        self._editor.selectAll()
        self._editing = True
        self._name_stack.setCurrentIndex(1)
        self._editor.setFocus()

    def _commit_edit(self) -> None:
        if not self._editing:
            return
        self._editing = False
        new_title = self._editor.text().strip() or self._original_name
        self._label.setText(new_title)
        try:
            self._sub_window.setWindowTitle(new_title)
        except RuntimeError:
            pass
        self._name_stack.setCurrentIndex(0)

    def _on_editing_finished(self) -> None:
        self._commit_edit()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._editor and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self._editing = False
                self._editor.setText(self._original_name)
                self._name_stack.setCurrentIndex(0)
                return True
        return super().eventFilter(obj, event)

    # -- mouse / keyboard --------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if self._active and self._name_stack.currentIndex() == 0:
                self._rename_timer.start()
            self._activate()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self._rename_timer.stop()
        self._activate()
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_F2 and self._name_stack.currentIndex() == 0:
            self.start_edit()
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Workspace dock with pin / auto-hide behaviour
# ---------------------------------------------------------------------------

class _WorkspaceDock(QDockWidget):
    """Left-side dock panel showing open workspaces.

    Pin state  → always expanded, hover has no effect.
    Unpinned   → collapses to a 28-px strip on mouse-out; expands on hover.
    """

    _EXPANDED_W: int = 210
    _COLLAPSED_W: int = 28
    _POLL_MS: int = 120        # hover-poll interval
    _COLLAPSE_DELAY_MS: int = 500  # time after mouse-out before collapsing

    def __init__(self, parent: QMainWindow) -> None:
        super().__init__(parent)
        self._main_win = parent
        self._pinned = True
        self._expanded = True

        # -- hover timers -----------------------------------------------
        self._poll = QTimer(self)
        self._poll.setInterval(self._POLL_MS)
        self._poll.timeout.connect(self._on_poll)

        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.setInterval(self._COLLAPSE_DELAY_MS)
        self._delay.timeout.connect(self._maybe_collapse)

        # -- custom title bar -------------------------------------------
        tb = QWidget()
        tb_layout = QHBoxLayout(tb)
        tb_layout.setContentsMargins(6, 2, 2, 2)
        tb_layout.setSpacing(2)

        lbl = QLabel("Workspaces")
        lbl.setStyleSheet("font-weight: bold; font-size: 11px;")

        self._pin_btn = QToolButton()
        self._pin_btn.setCheckable(True)
        self._pin_btn.setChecked(True)
        self._pin_btn.setIcon(_pin_icon(True))
        self._pin_btn.setFixedSize(22, 22)
        self._pin_btn.setToolTip("Pinned — click to enable auto-hide")
        self._pin_btn.toggled.connect(self._on_pin_toggled)

        close_btn = QToolButton()
        close_btn.setFixedSize(22, 22)
        close_btn.setToolTip("Close workspace panel  (View › Show Workspace Panel to reopen)")
        close_btn.clicked.connect(self.close)
        # Draw a small × as an icon so it's crisp at any DPI
        close_btn.setIcon(_close_icon())

        tb_layout.addWidget(lbl)
        tb_layout.addStretch()
        tb_layout.addWidget(self._pin_btn)
        tb_layout.addWidget(close_btn)
        self._title_bar = tb            # keep reference for restore
        self._blank_title = QWidget()   # zero-height placeholder used when collapsed
        self._blank_title.setMaximumHeight(0)
        self.setTitleBarWidget(tb)

        # -- body: stacked (expanded / collapsed) -----------------------
        self._stack = QStackedWidget()
        self.setWidget(self._stack)

        # Page 0 — expanded content
        page_exp = QWidget()
        exp_layout = QVBoxLayout(page_exp)
        exp_layout.setContentsMargins(4, 4, 4, 4)
        exp_layout.setSpacing(4)

        self._new_btn = QToolButton()
        self._new_btn.setText("＋ New Workspace")
        self._new_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._new_btn.setPopupMode(QToolButton.InstantPopup)
        self._new_btn.setMinimumWidth(150)
        self._new_menu = QMenu(self._new_btn)
        for descriptor in ANALYZER_REGISTRY:
            act = self._new_menu.addAction(descriptor.label)
            act.setData(descriptor.key)
        self._new_btn.setMenu(self._new_menu)
        exp_layout.addWidget(self._new_btn)

        # Scrollable list of _WorkspaceRow widgets
        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(1)
        self._rows_layout.addStretch()

        self._scroll = QScrollArea()
        self._scroll.setWidget(self._rows_container)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        exp_layout.addWidget(self._scroll, 1)
        self._stack.addWidget(page_exp)

        # Page 1 — collapsed strip (VS-style auto-hide tab)
        page_col = QWidget()
        col_layout = QVBoxLayout(page_col)
        col_layout.setContentsMargins(0, 0, 0, 0)
        col_layout.setSpacing(0)
        side_tab = _SideTab("Workspaces")
        col_layout.addWidget(side_tab, 1)
        self._stack.addWidget(page_col)

        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

    # -- public accessors -----------------------------------------------

    @property
    def new_workspace_menu(self) -> QMenu:
        return self._new_menu

    def add_row(self, title: str, sub_window: QMdiSubWindow) -> _WorkspaceRow:
        """Create and insert a new workspace row above the trailing stretch."""
        row = _WorkspaceRow(title, sub_window, self._rows_container)
        # insert before the trailing addStretch item
        self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
        return row

    def remove_row(self, row: _WorkspaceRow) -> None:
        row.setParent(None)

    def set_active_row(self, row: _WorkspaceRow | None) -> None:
        for i in range(self._rows_layout.count()):
            item = self._rows_layout.itemAt(i)
            if item:
                w = item.widget()
                if isinstance(w, _WorkspaceRow):
                    w.set_active(w is row)

    # -- pin / expand / collapse ----------------------------------------

    def _on_pin_toggled(self, pinned: bool) -> None:
        self._pinned = pinned
        self._pin_btn.setIcon(_pin_icon(pinned))
        if pinned:
            self._pin_btn.setToolTip("Pinned — click to enable auto-hide")
            self._poll.stop()
            self._delay.stop()
            self._do_expand()
        else:
            self._pin_btn.setToolTip("Auto-hide — click to pin open")
            self._do_collapse()
            self._poll.start()

    def _is_mouse_over(self) -> bool:
        tl = self.mapToGlobal(QPoint(0, 0))
        return QRect(tl, self.size()).contains(QCursor.pos())

    def _on_poll(self) -> None:
        if self._pinned:
            return
        over = self._is_mouse_over()
        if over:
            self._delay.stop()
            if not self._expanded:
                self._do_expand()
        else:
            if self._expanded and not self._delay.isActive():
                self._delay.start()

    def _maybe_collapse(self) -> None:
        if not self._pinned and not self._is_mouse_over():
            self._do_collapse()

    def _do_expand(self) -> None:
        if self._expanded:
            return
        self._expanded = True
        self.setTitleBarWidget(self._title_bar)   # restore full title bar
        self._stack.setCurrentIndex(0)
        self.setMinimumWidth(0)
        self.setMaximumWidth(16_777_215)
        try:
            self._main_win.resizeDocks([self], [self._EXPANDED_W], Qt.Horizontal)
        except Exception:
            pass

    def _do_collapse(self) -> None:
        if not self._expanded:
            return
        self._expanded = False
        self.setTitleBarWidget(self._blank_title)  # hide title bar; side tab fills the space
        self._stack.setCurrentIndex(1)
        self.setFixedWidth(self._COLLAPSED_W)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Semi-Auto Curation Studio")
        self.resize(1680, 980)

        self.mdi_area = QMdiArea()
        self.mdi_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.mdi_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.analyzer_toolbar = QToolBar("Quick Access")
        self._subwindow_actions: dict[QMdiSubWindow, list[QAction]] = {}
        self._workspace_counter: dict[str, int] = {}
        self._row_for_subwindow: dict[QMdiSubWindow, _WorkspaceRow] = {}
        self.workspace_dock: _WorkspaceDock | None = None
        self.toggle_workspace_panel_action: QAction | None = None

        self.status_label = QLabel("Ready — File › New Workspace to get started")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(220)
        self.memory_label = QLabel("Memory: --")

        self.show_statusbar_action: QAction | None = None
        self.build_database_action: QAction | None = None
        self.run_analysis_action: QAction | None = None
        self.refresh_heatmap_action: QAction | None = None
        self.clear_selection_action: QAction | None = None
        self.open_source_action: QAction | None = None
        self.open_output_action: QAction | None = None
        self.open_cloud_action: QAction | None = None
        self.copy_heatmap_image_action: QAction | None = None
        self.copy_heatmap_raw_action: QAction | None = None
        self.copy_curve_image_action: QAction | None = None
        self.copy_curve_raw_action: QAction | None = None
        self.light_theme_action: QAction | None = None
        self.dark_theme_action: QAction | None = None
        self.tile_action: QAction | None = None
        self.cascade_action: QAction | None = None
        self.close_all_action: QAction | None = None

        self.setCentralWidget(self.mdi_area)
        self._build_shell()
        self._build_workspace_dock()
        self._build_menu_bar()
        self._build_status_bar()

        self.mdi_area.subWindowActivated.connect(self._on_subwindow_activated)

        self._memory_timer = QTimer(self)
        self._memory_timer.timeout.connect(self._refresh_memory_usage)
        self._memory_timer.start(1000)
        self._refresh_memory_usage()
        self._refresh_menu_state()

    # ------------------------------------------------------------------
    # Shell / toolbar  (stable — never cleared, only show/hide actions)
    # ------------------------------------------------------------------

    def _build_shell(self) -> None:
        self.analyzer_toolbar.setMovable(False)
        self.analyzer_toolbar.setObjectName("QuickAccessToolbar")
        self.addToolBar(self.analyzer_toolbar)

    def _rebuild_toolbar(self) -> None:
        """Show only the active sub-window's pre-registered actions."""
        for actions in self._subwindow_actions.values():
            for act in actions:
                act.setVisible(False)
        active = self.mdi_area.activeSubWindow()
        if active and active in self._subwindow_actions:
            for act in self._subwindow_actions[active]:
                act.setVisible(True)

    # ------------------------------------------------------------------
    # Workspace dock
    # ------------------------------------------------------------------

    def _build_workspace_dock(self) -> None:
        dock = _WorkspaceDock(self)
        dock.setObjectName("WorkspaceDock")
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.workspace_dock = dock
        # Set initial width after the dock is registered with the main window
        self.resizeDocks([dock], [_WorkspaceDock._EXPANDED_W], Qt.Horizontal)

        # connect the dock's "New Workspace" menu actions
        for act in dock.new_workspace_menu.actions():
            key = act.data()
            act.triggered.connect(lambda checked=False, k=key: self._new_workspace(k))

        dock.visibilityChanged.connect(self._on_workspace_dock_visibility_changed)

    def _on_workspace_dock_visibility_changed(self, visible: bool) -> None:
        if self.toggle_workspace_panel_action is not None:
            self.toggle_workspace_panel_action.setChecked(visible)

    def _sync_workspace_list_selection(self, sub_window: QMdiSubWindow | None) -> None:
        if self.workspace_dock is None:
            return
        row = self._row_for_subwindow.get(sub_window) if sub_window else None
        self.workspace_dock.set_active_row(row)

    # ------------------------------------------------------------------
    # Workspace lifecycle
    # ------------------------------------------------------------------

    def _new_workspace(self, key: str) -> None:
        descriptor = next((d for d in ANALYZER_REGISTRY if d.key == key), None)
        if descriptor is None:
            return

        count = self._workspace_counter.get(key, 0) + 1
        self._workspace_counter[key] = count
        title = descriptor.label if count == 1 else f"{descriptor.label} #{count}"

        panel = descriptor.panel_factory()

        # Build toolbar actions once; add to toolbar hidden; show on activation
        actions = panel.build_toolbar_actions() if hasattr(panel, "build_toolbar_actions") else []
        for act in actions:
            self.analyzer_toolbar.addAction(act)
            act.setVisible(False)

        sub_window = QMdiSubWindow()
        sub_window.setWidget(panel)
        sub_window.setWindowTitle(title)
        sub_window.setAttribute(Qt.WA_DeleteOnClose)

        self._subwindow_actions[sub_window] = actions

        # Register in the workspace list dock
        if self.workspace_dock is not None:
            row = self.workspace_dock.add_row(title, sub_window)
            self._row_for_subwindow[sub_window] = row

        sub_window.destroyed.connect(
            lambda _obj=None, sw=sub_window: self._on_subwindow_destroyed(sw)
        )

        if hasattr(panel, "status_changed"):
            panel.status_changed.connect(self.status_label.setText)
        if hasattr(panel, "progress_changed"):
            panel.progress_changed.connect(self.progress_bar.setValue)

        # addSubWindow first, then resize — QMdiArea ignores sizes set before insertion
        self.mdi_area.addSubWindow(sub_window)
        vp = self.mdi_area.viewport()
        sw_w = max(900, vp.width() - 30)
        sw_h = max(650, vp.height() - 30)
        sub_window.resize(sw_w, sw_h)
        sub_window.move(0, 0)
        sub_window.showNormal()
        self.mdi_area.setActiveSubWindow(sub_window)

    def _on_subwindow_destroyed(self, sub_window: QMdiSubWindow) -> None:
        # Remove toolbar actions for this window
        try:
            for act in self._subwindow_actions.pop(sub_window, []):
                self.analyzer_toolbar.removeAction(act)
        except RuntimeError:
            pass

        # Remove from workspace list
        row = self._row_for_subwindow.pop(sub_window, None)
        if row is not None and self.workspace_dock is not None:
            self.workspace_dock.remove_row(row)
        try:
            self._refresh_menu_state()
        except RuntimeError:
            pass  # main window is shutting down

    def _on_subwindow_activated(self, sub_window: QMdiSubWindow | None) -> None:
        self._sync_workspace_list_selection(sub_window)
        self._rebuild_toolbar()
        self._refresh_menu_state()

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        # File
        file_menu = menu_bar.addMenu("&File")
        new_ws_menu = file_menu.addMenu("New Workspace")
        for descriptor in ANALYZER_REGISTRY:
            action = self._make_action(
                descriptor.label,
                lambda checked=False, key=descriptor.key: self._new_workspace(key),
            )
            new_ws_menu.addAction(action)

        file_menu.addSeparator()
        self.open_source_action = self._make_action(
            "Select Source Folder...",
            self._choose_source_for_current_panel,
            shortcut="Ctrl+Shift+O",
            tip="Choose the source folder for the active workspace.",
        )
        self.open_output_action = self._make_action(
            "Select Output Folder...",
            self._choose_output_for_current_panel,
            shortcut="Ctrl+Shift+S",
            tip="Choose the export folder for the active workspace.",
        )
        self.open_cloud_action = self._make_action(
            "Open Cloud Sessions...",
            self._open_cloud_for_current_panel,
            tip="Browse and sync cloud autotest sessions.",
        )
        file_menu.addAction(self.open_source_action)
        file_menu.addAction(self.open_output_action)
        file_menu.addAction(self.open_cloud_action)
        file_menu.addSeparator()
        file_menu.addAction(self._make_action("Exit", self.close, shortcut=QKeySequence.Quit))

        # Edit
        edit_menu = menu_bar.addMenu("&Edit")
        self.clear_selection_action = self._make_action(
            "Clear Selection",
            self._clear_selection_for_current_panel,
            shortcut="Esc",
            tip="Clear the current heatmap/device selection.",
        )
        self.copy_heatmap_image_action = self._make_action(
            "Copy Heatmap Image", self._copy_heatmap_image_for_current_panel
        )
        self.copy_heatmap_raw_action = self._make_action(
            "Copy Heatmap Raw Data", self._copy_heatmap_raw_for_current_panel
        )
        self.copy_curve_image_action = self._make_action(
            "Copy Curve Image", self._copy_curve_image_for_current_panel
        )
        self.copy_curve_raw_action = self._make_action(
            "Copy Curve Raw Data", self._copy_curve_raw_for_current_panel
        )
        edit_menu.addAction(self.clear_selection_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.copy_heatmap_image_action)
        edit_menu.addAction(self.copy_heatmap_raw_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.copy_curve_image_action)
        edit_menu.addAction(self.copy_curve_raw_action)

        # View
        view_menu = menu_bar.addMenu("&View")
        self.toggle_workspace_panel_action = self._make_action(
            "Show Workspace Panel",
            self._toggle_workspace_panel,
            shortcut="Ctrl+Shift+W",
            tip="Show or hide the Workspaces dock panel.",
        )
        self.toggle_workspace_panel_action.setCheckable(True)
        self.toggle_workspace_panel_action.setChecked(True)
        view_menu.addAction(self.toggle_workspace_panel_action)
        view_menu.addSeparator()

        self.refresh_heatmap_action = self._make_action(
            "Refresh Heatmap",
            self._refresh_heatmap_for_current_panel,
            shortcut="F5",
            tip="Redraw the heatmap using the current settings.",
        )
        view_menu.addAction(self.refresh_heatmap_action)
        view_menu.addSeparator()

        theme_menu = view_menu.addMenu("Theme")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        self.light_theme_action = self._make_action(
            "Light", lambda checked=False: self._set_theme_for_current_panel("light")
        )
        self.light_theme_action.setCheckable(True)
        self.dark_theme_action = self._make_action(
            "Dark", lambda checked=False: self._set_theme_for_current_panel("dark")
        )
        self.dark_theme_action.setCheckable(True)
        theme_group.addAction(self.light_theme_action)
        theme_group.addAction(self.dark_theme_action)
        theme_menu.addAction(self.light_theme_action)
        theme_menu.addAction(self.dark_theme_action)

        view_menu.addSeparator()
        self.show_statusbar_action = self._make_action("Show Status Bar", self._toggle_status_bar)
        self.show_statusbar_action.setCheckable(True)
        self.show_statusbar_action.setChecked(True)
        view_menu.addAction(self.show_statusbar_action)

        # Analysis
        analysis_menu = menu_bar.addMenu("&Analysis")
        self.build_database_action = self._make_action(
            "Load Data and Build Database",
            self._build_database_for_current_panel,
            shortcut="Ctrl+Shift+B",
            tip="Preload raw data and build the working cache when supported.",
        )
        self.run_analysis_action = self._make_action(
            "Run Analysis",
            self._run_analysis_for_current_panel,
            shortcut="Ctrl+R",
            tip="Run the main analysis for the active workspace.",
        )
        analysis_menu.addAction(self.build_database_action)
        analysis_menu.addAction(self.run_analysis_action)
        analysis_menu.addSeparator()
        analysis_menu.addAction(self.refresh_heatmap_action)

        # Window
        window_menu = menu_bar.addMenu("&Window")
        self.tile_action = self._make_action(
            "Tile Windows", self.mdi_area.tileSubWindows,
            tip="Arrange all workspace windows in a tile.",
        )
        self.cascade_action = self._make_action(
            "Cascade Windows", self.mdi_area.cascadeSubWindows,
            tip="Cascade all workspace windows.",
        )
        self.close_all_action = self._make_action(
            "Close All Workspaces", self.mdi_area.closeAllSubWindows,
            tip="Close every open workspace window.",
        )
        window_menu.addAction(self.tile_action)
        window_menu.addAction(self.cascade_action)
        window_menu.addSeparator()
        window_menu.addAction(self.close_all_action)

        # Help / About
        help_menu = menu_bar.addMenu("&Help")
        help_menu.addAction(self._make_action("Quick Start", self._show_quick_start))
        help_menu.addAction(self._make_action("Operation Guide", self._show_operation_guide))
        help_menu.addAction(self._make_action("Help Topics", self._show_help_topics))

        about_menu = menu_bar.addMenu("&About")
        about_menu.addAction(self._make_action("About This App", self._show_about_dialog))
        about_menu.addAction(self._make_action("Software Information", self._show_software_information))
        about_menu.addAction(self._make_action("Copyright and License", self._show_copyright_information))

    def _build_status_bar(self) -> None:
        status_bar = QStatusBar()
        status_bar.addWidget(self.status_label, 1)
        status_bar.addPermanentWidget(self.progress_bar)
        status_bar.addPermanentWidget(self.memory_label)
        self.setStatusBar(status_bar)

    # ------------------------------------------------------------------
    # Menu state
    # ------------------------------------------------------------------

    def _refresh_menu_state(self) -> None:
        panel = self._current_panel()
        has = panel is not None

        if self.build_database_action is not None:
            self.build_database_action.setEnabled(has and hasattr(panel, "load_and_build_database"))
        if self.run_analysis_action is not None:
            self.run_analysis_action.setEnabled(has and hasattr(panel, "run_analysis"))
        if self.refresh_heatmap_action is not None:
            self.refresh_heatmap_action.setEnabled(has and hasattr(panel, "refresh_heatmap"))
        if self.clear_selection_action is not None:
            self.clear_selection_action.setEnabled(has and hasattr(panel, "clear_selection"))
        if self.open_source_action is not None:
            self.open_source_action.setEnabled(has and hasattr(panel, "_choose_source"))
        if self.open_output_action is not None:
            self.open_output_action.setEnabled(has and hasattr(panel, "_choose_output"))
        if self.open_cloud_action is not None:
            self.open_cloud_action.setEnabled(has and hasattr(panel, "_open_cloud_sessions"))
        if self.copy_heatmap_image_action is not None:
            self.copy_heatmap_image_action.setEnabled(has and hasattr(panel, "_copy_heatmap_image"))
        if self.copy_heatmap_raw_action is not None:
            self.copy_heatmap_raw_action.setEnabled(has and hasattr(panel, "_copy_heatmap_rawdata"))
        if self.copy_curve_image_action is not None:
            self.copy_curve_image_action.setEnabled(has and hasattr(panel, "_copy_curve_image"))
        if self.copy_curve_raw_action is not None:
            self.copy_curve_raw_action.setEnabled(has and hasattr(panel, "_copy_curve_rawdata"))

        has_windows = bool(self.mdi_area.subWindowList())
        if self.tile_action is not None:
            self.tile_action.setEnabled(has_windows)
        if self.cascade_action is not None:
            self.cascade_action.setEnabled(has_windows)
        if self.close_all_action is not None:
            self.close_all_action.setEnabled(has_windows)

        current_theme = self._current_theme_name()
        if self.light_theme_action is not None:
            self.light_theme_action.setChecked(current_theme == "light")
        if self.dark_theme_action is not None:
            self.dark_theme_action.setChecked(current_theme == "dark")

    # ------------------------------------------------------------------
    # Panel access
    # ------------------------------------------------------------------

    def _current_panel(self):
        try:
            active = self.mdi_area.activeSubWindow()
            return active.widget() if active else None
        except RuntimeError:
            return None

    # ------------------------------------------------------------------
    # Panel command delegation
    # ------------------------------------------------------------------

    def _run_analysis_for_current_panel(self) -> None:
        panel = self._current_panel()
        if panel and hasattr(panel, "run_analysis"):
            panel.run_analysis()

    def _build_database_for_current_panel(self) -> None:
        panel = self._current_panel()
        if panel and hasattr(panel, "load_and_build_database"):
            panel.load_and_build_database()

    def _refresh_heatmap_for_current_panel(self) -> None:
        panel = self._current_panel()
        if panel and hasattr(panel, "refresh_heatmap"):
            panel.refresh_heatmap()

    def _clear_selection_for_current_panel(self) -> None:
        panel = self._current_panel()
        if panel and hasattr(panel, "clear_selection"):
            panel.clear_selection()

    def _choose_source_for_current_panel(self) -> None:
        panel = self._current_panel()
        if panel and hasattr(panel, "_choose_source"):
            panel._choose_source()

    def _choose_output_for_current_panel(self) -> None:
        panel = self._current_panel()
        if panel and hasattr(panel, "_choose_output"):
            panel._choose_output()

    def _open_cloud_for_current_panel(self) -> None:
        panel = self._current_panel()
        if panel and hasattr(panel, "_open_cloud_sessions"):
            panel._open_cloud_sessions()

    def _copy_heatmap_image_for_current_panel(self) -> None:
        panel = self._current_panel()
        if panel and hasattr(panel, "_copy_heatmap_image"):
            panel._copy_heatmap_image()

    def _copy_heatmap_raw_for_current_panel(self) -> None:
        panel = self._current_panel()
        if panel and hasattr(panel, "_copy_heatmap_rawdata"):
            panel._copy_heatmap_rawdata()

    def _copy_curve_image_for_current_panel(self) -> None:
        panel = self._current_panel()
        if panel and hasattr(panel, "_copy_curve_image"):
            panel._copy_curve_image()

    def _copy_curve_raw_for_current_panel(self) -> None:
        panel = self._current_panel()
        if panel and hasattr(panel, "_copy_curve_rawdata"):
            panel._copy_curve_rawdata()

    def _set_theme_for_current_panel(self, theme: str) -> None:
        panel = self._current_panel()
        if panel is None:
            return
        if hasattr(panel, "theme_combo"):
            panel.theme_combo.setCurrentText(theme)
        elif hasattr(panel, "set_theme"):
            panel.set_theme(theme)
        self._refresh_menu_state()

    def _current_theme_name(self) -> str:
        panel = self._current_panel()
        if panel and hasattr(panel, "theme_combo"):
            return panel.theme_combo.currentText()
        return "dark"

    def _toggle_workspace_panel(self) -> None:
        if self.workspace_dock is None:
            return
        visible = (
            self.toggle_workspace_panel_action is not None
            and self.toggle_workspace_panel_action.isChecked()
        )
        self.workspace_dock.setVisible(visible)

    def _toggle_status_bar(self) -> None:
        if self.show_statusbar_action is None:
            return
        self.statusBar().setVisible(self.show_statusbar_action.isChecked())

    # ------------------------------------------------------------------
    # Help / About dialogs
    # ------------------------------------------------------------------

    def _show_about_dialog(self) -> None:
        version = _app_version()
        html = f"""
        <h2>Semi-Auto Curation Studio</h2>
        <p><b>Version:</b> {version}</p>
        <p>Desktop workbench for high-throughput electrical test data analysis.</p>
        <p><b>Workspaces:</b> K2450-IV, B1500-Trans, B1500-Output</p>
        <p>Open multiple workspace windows via <b>File › New Workspace</b> or the Workspaces panel.</p>
        """
        self._show_rich_text_dialog("About This App", html)

    def _show_software_information(self) -> None:
        analyzer_list = "".join(f"<li>{d.label}</li>" for d in ANALYZER_REGISTRY)
        html = f"""
        <h2>Software Information</h2>
        <p><b>Application:</b> Semi-Auto Curation Studio</p>
        <p><b>Version:</b> {_app_version()}</p>
        <p><b>Python:</b> 3.12+  |  <b>GUI:</b> PySide6, Matplotlib, NumPy</p>
        <p><b>Available analyzers:</b></p><ul>{analyzer_list}</ul>
        <p>Use <b>Window</b> menu to tile or cascade multiple open workspaces.</p>
        """
        self._show_rich_text_dialog("Software Information", html)

    def _show_copyright_information(self) -> None:
        html = """
        <h2>Copyright and License</h2>
        <p>Copyright follows your team's internal project ownership policy.</p>
        <p>Third-party packages (PySide6, Matplotlib, NumPy, psutil, pyqtgraph) retain their own licenses.</p>
        """
        self._show_rich_text_dialog("Copyright and License", html)

    def _show_quick_start(self) -> None:
        html = """
        <h2>Quick Start</h2>
        <ol>
          <li>Click <b>＋ New Workspace</b> in the left Workspaces panel, or use <b>File › New Workspace</b>.</li>
          <li>Multiple workspaces can be open simultaneously — each is fully independent.</li>
          <li>Use <b>File</b> to pick source/output folders or open cloud sessions for the active window.</li>
          <li>For K2450-IV: run <b>Analysis › Load Data and Build Database</b> first (cached flow).</li>
          <li>Run <b>Analysis › Run Analysis</b> to compute metrics and export results.</li>
          <li>Click a heatmap cell to select a device; inspect curves in the right panel.</li>
          <li>Use <b>Edit</b> to copy images or raw data for reports.</li>
          <li>Use <b>Window</b> to tile or cascade open workspace windows.</li>
        </ol>
        <h3>Workspaces panel</h3>
        <ul>
          <li><b>📌 Pin</b>: keep panel always visible. When unpinned, the panel auto-hides on mouse-out.</li>
          <li>Click a workspace name to bring it to front; double-click to restore from minimized.</li>
          <li><b>✕</b> closes the panel; reopen via <b>View › Show Workspace Panel</b> (Ctrl+Shift+W).</li>
        </ul>
        """
        self._show_rich_text_dialog("Quick Start", html)

    def _show_operation_guide(self) -> None:
        html = """
        <h2>Operation Guide</h2>
        <h3>File</h3>
        <ul>
          <li><b>New Workspace</b>: open a new independent workspace window.</li>
          <li><b>Select Source / Output Folder</b>: for the currently active window.</li>
          <li><b>Open Cloud Sessions</b>: browse and sync remote autotest sessions.</li>
        </ul>
        <h3>View</h3>
        <ul>
          <li><b>Show Workspace Panel</b>: toggle the left dock (Ctrl+Shift+W).</li>
          <li><b>Refresh Heatmap</b>: re-render after metric/scale/theme changes (F5).</li>
          <li><b>Theme</b>: Light or Dark for the active workspace.</li>
        </ul>
        <h3>Analysis</h3>
        <ul>
          <li><b>Load Data and Build Database</b>: K2450-IV cache preparation.</li>
          <li><b>Run Analysis</b>: compute per-device metrics and export.</li>
        </ul>
        <h3>Window</h3>
        <ul>
          <li><b>Tile / Cascade Windows</b>: arrange open workspaces.</li>
          <li><b>Close All Workspaces</b>: close every open window.</li>
        </ul>
        """
        self._show_rich_text_dialog("Operation Guide", html)

    def _show_help_topics(self) -> None:
        html = """
        <h2>Help Topics</h2>
        <h3>Analysis fails</h3>
        <ul>
          <li>Confirm source folder matches the workspace format.</li>
          <li>Ensure output folder is writable.</li>
          <li>For B1500, confirm files follow the expected naming patterns.</li>
        </ul>
        <h3>Plots look wrong</h3>
        <ul>
          <li>Refresh heatmap after changing metric or scale.</li>
          <li>Switch Y-axis between linear and log.</li>
          <li>Try the other theme if contrast is poor.</li>
        </ul>
        <h3>Comparing multiple datasets</h3>
        <ul>
          <li>Open a second workspace window of the same type.</li>
          <li>Use <b>Window › Tile Windows</b> for a side-by-side layout.</li>
          <li>Copy raw data via <b>Edit</b> for external plotting.</li>
        </ul>
        """
        self._show_rich_text_dialog("Help Topics", html)

    def _show_rich_text_dialog(self, title: str, html: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(760, 560)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser(dialog)
        browser.setOpenExternalLinks(True)
        browser.setHtml(html)
        layout.addWidget(browser, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _make_action(
        self,
        text: str,
        callback,
        *,
        shortcut: str | QKeySequence | None = None,
        tip: str | None = None,
    ) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        if tip:
            action.setStatusTip(tip)
            action.setToolTip(tip)
        action.triggered.connect(callback)
        return action

    def _refresh_memory_usage(self) -> None:
        process = psutil.Process(os.getpid())
        rss_mb = process.memory_info().rss / (1024 * 1024)
        self.memory_label.setText(f"Memory: {rss_mb:.1f} MB")

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_F2:
            try:
                active = self.mdi_area.activeSubWindow()
            except RuntimeError:
                active = None
            if active:
                row = self._row_for_subwindow.get(active)
                if row:
                    row.start_edit()
                    return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _app_version() -> str:
    try:
        return metadata.version("semi-auto-curation")
    except metadata.PackageNotFoundError:
        return "0.1.0"


def launch() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.showMaximized()
    app.exec()
