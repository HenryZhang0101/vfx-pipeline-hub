"""Notion-Dark PySide6 Media Loader dialog & Read-node utilities for Foundry Nuke 16+."""

import importlib.util
import os
import re
from typing import Any, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from config import PIPELINE_ROOT, load_user_settings, open_path_in_os
from core.models import MediaItem, PROPERTY_COLOR_PALETTE, Project
from core.storage import ProjectRepository
from core.timecode import format_shot_id, split_shot_id
from dccs.nuke.context import PipelineContext, resolve_active_context


def _load_pipeline_theme_module():
    """
    Load h:/_pipeline/ui/theme.py directly by file path so it never collides
    with Nuke's or third-party plugins' top-level `ui` module on sys.path.
    """
    theme_path = os.path.join(PIPELINE_ROOT, "ui", "theme.py")
    if not os.path.isfile(theme_path):
        return None
    spec = importlib.util.spec_from_file_location("_vfx_pipeline_theme", theme_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_THEME_MOD = _load_pipeline_theme_module()


def get_active_stylesheet() -> str:
    if _THEME_MOD is not None and hasattr(_THEME_MOD, "get_active_stylesheet"):
        return _THEME_MOD.get_active_stylesheet()
    return ""


def get_app_icon() -> QtGui.QIcon:
    if _THEME_MOD is not None and hasattr(_THEME_MOD, "get_app_icon"):
        return _THEME_MOD.get_app_icon()
    return QtGui.QIcon()


_SORT_ROLE = QtCore.Qt.UserRole + 10
_ACTIVE_DIALOG: Optional["MediaLoaderDialog"] = None


class _SortableItem(QtWidgets.QTableWidgetItem):
    """QTableWidgetItem that sorts by an optional typed key in _SORT_ROLE."""

    def __init__(self, text: str = "", sort_value: Any = None):
        super().__init__(text)
        if sort_value is not None:
            self.setData(_SORT_ROLE, sort_value)

    def __lt__(self, other: QtWidgets.QTableWidgetItem) -> bool:
        a = self.data(_SORT_ROLE)
        b = other.data(_SORT_ROLE) if isinstance(other, QtWidgets.QTableWidgetItem) else None
        if a is not None and b is not None and type(a) is type(b):
            return a < b
        return (self.text() or "").lower() < (other.text() or "").lower()


def _get_nuke_main_window() -> Optional[QtWidgets.QWidget]:
    """Return Nuke's main QMainWindow so PySide6 dialogs parent cleanly."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    for widget in app.topLevelWidgets():
        if (
            widget.inherits("QMainWindow")
            and widget.metaObject().className() == "Foundry::UI::DockMainWindow"
        ):
            return widget
    return app.activeWindow()


def _make_color_dot_icon(color_hex: str, size: int = 9) -> QtGui.QIcon:
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.setBrush(QtGui.QColor(color_hex))
    painter.setPen(QtCore.Qt.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return QtGui.QIcon(pix)


def _render_thumb_icon(thumb_path: str, width: int = 80, height: int = 45) -> QtGui.QIcon:
    if not thumb_path or not os.path.isfile(thumb_path):
        return QtGui.QIcon()
    pix = QtGui.QPixmap(thumb_path)
    if pix.isNull():
        return QtGui.QIcon()
    scaled = pix.scaled(
        width,
        height,
        QtCore.Qt.KeepAspectRatio,
        QtCore.Qt.SmoothTransformation,
    )
    canvas = QtGui.QPixmap(width, height)
    canvas.fill(QtGui.QColor("#141414"))
    painter = QtGui.QPainter(canvas)
    x = (width - scaled.width()) // 2
    y = (height - scaled.height()) // 2
    painter.drawPixmap(x, y, scaled)
    painter.end()
    return QtGui.QIcon(canvas)


def _discover_project_roots(initial_root: str = "") -> List[str]:
    """Return deduplicated project roots from active context + user_settings.json."""
    recents: List[str] = []
    if initial_root and os.path.isdir(initial_root):
        resolved = ProjectRepository.resolve_vfx_root(initial_root)
        if os.path.isdir(resolved):
            recents.append(resolved)

    settings = load_user_settings()
    for p in settings.get("recent_projects", []):
        if os.path.isdir(p):
            resolved = ProjectRepository.resolve_vfx_root(p)
            if os.path.isdir(resolved) and resolved not in recents:
                recents.append(resolved)

    last_root = (settings.get("last_project_root") or "").strip()
    if last_root and os.path.isdir(last_root):
        resolved = ProjectRepository.resolve_vfx_root(last_root)
        if os.path.isdir(resolved) and resolved not in recents:
            recents.append(resolved)

    return recents


def stamp_read_pipeline_knobs(
    read_node,
    shot_id: str,
    category: str,
    identifier: str,
    version: str,
) -> None:
    """Attach lightweight Pipeline metadata knobs onto a Nuke Read node."""
    import nuke  # type: ignore

    if read_node is None:
        return

    if read_node.knob("pipeline_tab") is None:
        read_node.addKnob(nuke.Tab_Knob("pipeline_tab", "Pipeline"))

    fields = [
        ("vfx_shot_id", "Shot ID", shot_id),
        ("vfx_category", "Category", category),
        ("vfx_identifier", "Identifier", identifier),
        ("vfx_version", "Version", version),
    ]
    for kname, label, val in fields:
        knob = read_node.knob(kname)
        if knob is None:
            knob = nuke.String_Knob(kname, label)
            read_node.addKnob(knob)
        knob.setValue(str(val or ""))


def _apply_media_item_to_read(read_node, item: MediaItem) -> None:
    """Configure a Read node's file, frame range, label, and Pipeline knobs from a MediaItem."""
    pattern = (item.nuke_pattern or item.first_file_path or "").replace("\\", "/")
    read_node.knob("file").setValue(pattern)

    if item.is_sequence:
        sf = int(item.start_frame)
        ef = int(item.end_frame)
        read_node.knob("first").setValue(sf)
        read_node.knob("last").setValue(ef)
        read_node.knob("origfirst").setValue(sf)
        read_node.knob("origlast").setValue(ef)
        if read_node.knob("origset"):
            read_node.knob("origset").setValue(True)

    read_node.knob("label").setValue(
        f"{item.shot_id} | {item.identifier} ({item.version})\n[value first]-[value last]"
    )
    stamp_read_pipeline_knobs(
        read_node,
        shot_id=item.shot_id,
        category=item.category,
        identifier=item.identifier,
        version=item.version,
    )


def create_read_from_media_item(item: MediaItem, xpos: Optional[int] = None, ypos: Optional[int] = None):
    """Create a Nuke Read node for a MediaItem."""
    import nuke  # type: ignore

    read_node = nuke.createNode("Read", inpanel=False)
    _apply_media_item_to_read(read_node, item)
    if xpos is not None:
        read_node.setXpos(int(xpos))
    if ypos is not None:
        read_node.setYpos(int(ypos))
    return read_node


def replace_read_with_media_item(read_node, item: MediaItem) -> bool:
    """Update an existing Read node in-place to point to `item`."""
    if read_node is None or read_node.Class() != "Read":
        return False
    _apply_media_item_to_read(read_node, item)
    return True


def update_selected_reads_to_latest() -> int:
    """
    Update all selected Read nodes to the latest version available in the active
    project's `<vfx_root>/<SEQ>/<SHOT>/media/<identifier>/` directory.
    """
    import nuke  # type: ignore

    selected = [n for n in nuke.selectedNodes("Read")]
    if not selected:
        nuke.message("Please select one or more Read nodes to update.")
        return 0

    ctx = resolve_active_context()
    repo = ProjectRepository()
    project = repo.load(ctx.vfx_root) if ctx.is_valid_project else None

    updated_lines: List[str] = []
    skipped = 0

    undo = nuke.Undo()
    undo.begin("Update Selected Reads to Latest")
    try:
        for node in selected:
            shot_id = str(node.knob("vfx_shot_id").value() or "").strip() if node.knob("vfx_shot_id") else ""
            ident = str(node.knob("vfx_identifier").value() or "").strip() if node.knob("vfx_identifier") else ""
            cur_ver = str(node.knob("vfx_version").value() or "").strip() if node.knob("vfx_version") else ""
            file_val = str(node.knob("file").value() or "").replace("\\", "/")

            # Fallback: parse <vfx_root>/<SEQ>/<SHOT>/media/<identifier>/<v####>/...
            if not (shot_id and ident):
                m = re.search(r"/([^/]+)/([^/]+)/media/([^/]+)/(v\d{3,5})/", file_val, re.IGNORECASE)
                if m:
                    shot_id = format_shot_id(m.group(1), m.group(2))
                    ident = m.group(3)
                    cur_ver = m.group(4).lower()

            if not (project and shot_id and ident):
                skipped += 1
                continue

            media_items = [
                m_item
                for m_item in repo.scan_all_shot_media(project, shot_id)
                if m_item.category == "media" and m_item.identifier.lower() == ident.lower()
            ]
            if not media_items:
                skipped += 1
                continue

            latest = max(media_items, key=lambda x: (x.version_num, x.version.lower()))
            if latest.version.lower() == cur_ver.lower() and latest.nuke_pattern == file_val:
                continue

            _apply_media_item_to_read(node, latest)
            updated_lines.append(f"{node.name()}: {cur_ver or '?'} → {latest.version}")
    finally:
        undo.end()

    if updated_lines:
        summary = f"Updated {len(updated_lines)} Read node(s) to latest:\n\n" + "\n".join(updated_lines)
        nuke.tprint(f"[VFX Pipeline] {summary}")
        nuke.message(summary)
    else:
        msg = "All selected Read nodes are already on the latest version."
        if skipped:
            msg += f" ({skipped} node(s) could not be matched to project media.)"
        nuke.message(msg)
    return len(updated_lines)


class MediaLoaderDialog(QtWidgets.QDialog):
    """Project Manager Media Loader dialog for Foundry Nuke 16+."""

    COL_PREVIEW = 0
    COL_SHOT = 1
    COL_IDENTIFIER = 2
    COL_VERSION = 3
    COL_RANGE = 4
    COL_FORMAT = 5
    COL_MODIFIED = 6
    COL_FILE = 7

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("VFX Pipeline — Media Loader")
        self.setWindowIcon(get_app_icon())
        self.resize(960, 620)
        self.setStyleSheet(get_active_stylesheet())

        self._repo = ProjectRepository()
        self._ctx: PipelineContext = resolve_active_context()
        self._project: Optional[Project] = None
        self._all_media: List[MediaItem] = []
        self._filtered_media: List[MediaItem] = []

        self._build_ui()
        self._populate_projects()

    def _build_ui(self) -> None:
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(8)

        # Row 1: Project + Shot context selectors
        ctx_row = QtWidgets.QHBoxLayout()
        ctx_row.setSpacing(8)

        ctx_row.addWidget(QtWidgets.QLabel("Project:"))
        self.project_combo = QtWidgets.QComboBox()
        self.project_combo.setMinimumWidth(220)
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        ctx_row.addWidget(self.project_combo, 2)

        self.browse_btn = QtWidgets.QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._on_browse_project)
        ctx_row.addWidget(self.browse_btn)

        ctx_row.addSpacing(8)
        ctx_row.addWidget(QtWidgets.QLabel("Shot:"))
        self.shot_combo = QtWidgets.QComboBox()
        self.shot_combo.setMinimumWidth(140)
        self.shot_combo.currentIndexChanged.connect(self._on_shot_changed)
        ctx_row.addWidget(self.shot_combo, 1)

        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._reload_media)
        ctx_row.addWidget(self.refresh_btn)

        root_layout.addLayout(ctx_row)

        # Row 2: Category + Filters
        filter_row = QtWidgets.QHBoxLayout()
        filter_row.setSpacing(6)

        self.category_combo = QtWidgets.QComboBox()
        self.category_combo.addItem("Media (Plates & Renders)", "media")
        self.category_combo.addItem("Assets (3D Cameras / Geo — Backlog)", "assets")
        self.category_combo.currentIndexChanged.connect(self._on_category_changed)
        filter_row.addWidget(self.category_combo)

        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Search identifier, version, file, notes...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self.search_edit, 2)

        self.ident_combo = QtWidgets.QComboBox()
        self.ident_combo.addItem("All Identifiers", "all")
        self.ident_combo.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self.ident_combo)

        self.color_combo = QtWidgets.QComboBox()
        self.color_combo.addItem("All Colors", "all")
        self.color_combo.addItem("No Color", "none")
        for cname, chex in PROPERTY_COLOR_PALETTE:
            self.color_combo.addItem(_make_color_dot_icon(chex, 9), cname, chex)
        self.color_combo.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self.color_combo)

        self.latest_only_chk = QtWidgets.QCheckBox("Latest Only")
        self.latest_only_chk.setToolTip("Show only the newest version per shot and identifier")
        self.latest_only_chk.toggled.connect(self._apply_filters)
        filter_row.addWidget(self.latest_only_chk)

        root_layout.addLayout(filter_row)

        # Backlog banner for Assets
        self.backlog_banner = QtWidgets.QFrame()
        self.backlog_banner.setObjectName("card_frame")
        b_lay = QtWidgets.QHBoxLayout(self.backlog_banner)
        b_lay.setContentsMargins(10, 8, 10, 8)
        b_lbl = QtWidgets.QLabel(
            "Assets Loader (3D Cameras, Alembics, USD, Geo) is backlogged for a future release. "
            "Switch back to Media to load plates and 2D renders."
        )
        b_lbl.setWordWrap(True)
        b_lbl.setStyleSheet("color: #9B9A97;")
        b_lay.addWidget(b_lbl)
        self.backlog_banner.setVisible(False)
        root_layout.addWidget(self.backlog_banner)

        # Media Table
        self.table = QtWidgets.QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "Preview",
            "Shot",
            "Identifier",
            "Version",
            "Range / Count",
            "Format",
            "Modified",
            "Sequence / File",
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setIconSize(QtCore.QSize(80, 45))
        self.table.itemDoubleClicked.connect(lambda _: self._on_load_as_read())
        self.table.itemSelectionChanged.connect(self._update_button_states)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(self.COL_PREVIEW, QtWidgets.QHeaderView.Fixed)
        self.table.setColumnWidth(self.COL_PREVIEW, 92)
        hdr.setSectionResizeMode(self.COL_SHOT, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_IDENTIFIER, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_VERSION, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_RANGE, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_FORMAT, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_MODIFIED, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_FILE, QtWidgets.QHeaderView.Stretch)

        root_layout.addWidget(self.table, 1)

        # Footer status + action buttons
        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(8)

        self.status_lbl = QtWidgets.QLabel("")
        self.status_lbl.setStyleSheet("color: #9B9A97; font-size: 11px;")
        footer.addWidget(self.status_lbl, 1)

        self.open_folder_btn = QtWidgets.QPushButton("Open Folder")
        self.open_folder_btn.clicked.connect(self._on_open_folder)
        footer.addWidget(self.open_folder_btn)

        self.replace_read_btn = QtWidgets.QPushButton("Replace Selected Read")
        self.replace_read_btn.setObjectName("accent_btn")
        self.replace_read_btn.clicked.connect(self._on_replace_selected_read)
        footer.addWidget(self.replace_read_btn)

        self.load_read_btn = QtWidgets.QPushButton("Load as Read")
        self.load_read_btn.setObjectName("primary_btn")
        self.load_read_btn.clicked.connect(self._on_load_as_read)
        footer.addWidget(self.load_read_btn)

        self.close_btn = QtWidgets.QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        footer.addWidget(self.close_btn)

        root_layout.addLayout(footer)
        self._update_button_states()

    # ------------------------------------------------------------------
    # Project & Shot Population
    # ------------------------------------------------------------------

    def _populate_projects(self) -> None:
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        roots = _discover_project_roots(self._ctx.vfx_root)
        for root in roots:
            proj = self._repo.load(root)
            label = f"{proj.name}  ({root})" if proj.name else root
            self.project_combo.addItem(label, root)
        self.project_combo.blockSignals(False)

        if self.project_combo.count() > 0:
            self.project_combo.setCurrentIndex(0)
            self._on_project_changed()
        else:
            self.status_lbl.setText("No VFX project found. Click 'Browse...' to select a project folder.")

    def _on_browse_project(self) -> None:
        start_dir = self._project.vfx_root if self._project else ""
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select VFX Project Root (folder containing project.json)", start_dir
        )
        if not chosen:
            return
        resolved = ProjectRepository.resolve_vfx_root(chosen)
        idx = self.project_combo.findData(resolved)
        if idx < 0:
            proj = self._repo.load(resolved)
            label = f"{proj.name}  ({resolved})" if proj.name else resolved
            self.project_combo.insertItem(0, label, resolved)
            idx = 0
        self.project_combo.setCurrentIndex(idx)

    def _on_project_changed(self) -> None:
        root = self.project_combo.currentData()
        if not root or not os.path.isdir(root):
            self._project = None
            self._all_media = []
            self._apply_filters()
            return

        self._project = self._repo.load(root)
        self._populate_shots()
        self._reload_media()

    def _populate_shots(self) -> None:
        self.shot_combo.blockSignals(True)
        self.shot_combo.clear()
        if not self._project:
            self.shot_combo.blockSignals(False)
            return

        shot_ids: List[str] = [s.shot_id for s in self._project.all_shots()]
        for seq, sh in self._repo.discover_disk_shots(self._project):
            sid = format_shot_id(seq, sh)
            if sid not in shot_ids:
                shot_ids.append(sid)
        shot_ids.sort()

        self.shot_combo.addItem("All Shots", "__ALL__")
        for sid in shot_ids:
            self.shot_combo.addItem(sid, sid)

        # Select active shot if present in this project
        target_sid = self._ctx.shot_id
        if target_sid:
            idx = self.shot_combo.findData(target_sid)
            if idx >= 0:
                self.shot_combo.setCurrentIndex(idx)
            elif self.shot_combo.count() > 1:
                self.shot_combo.setCurrentIndex(1)
        elif self.shot_combo.count() > 1:
            self.shot_combo.setCurrentIndex(1)
        self.shot_combo.blockSignals(False)

    def _on_shot_changed(self) -> None:
        self._reload_media()

    def _on_category_changed(self) -> None:
        cat = self.category_combo.currentData()
        is_assets = cat == "assets"
        self.backlog_banner.setVisible(is_assets)
        self.table.setEnabled(not is_assets)
        if is_assets:
            self.table.setRowCount(0)
            self.status_lbl.setText("Assets loader is backlogged — switch to Media to load plates/renders.")
            self._update_button_states()
        else:
            self._apply_filters()

    # ------------------------------------------------------------------
    # Media Scanning & Filtering
    # ------------------------------------------------------------------

    def _reload_media(self) -> None:
        self._all_media = []
        if not self._project:
            self._apply_filters()
            return

        shot_data = self.shot_combo.currentData()
        if shot_data == "__ALL__":
            shot_ids = [
                self.shot_combo.itemData(i)
                for i in range(1, self.shot_combo.count())
                if self.shot_combo.itemData(i)
            ]
            scanned = self._repo.scan_shots_media(self._project, shot_ids)
        elif shot_data:
            scanned = self._repo.scan_all_shot_media(self._project, shot_data)
        else:
            scanned = []

        # Focus on media category for now (assets backlogged)
        self._all_media = [m for m in scanned if m.category == "media"]
        self._rebuild_identifier_filter()
        self._apply_filters()

    def _rebuild_identifier_filter(self) -> None:
        prev = self.ident_combo.currentData()
        idents = sorted({m.identifier for m in self._all_media if m.identifier})
        self.ident_combo.blockSignals(True)
        self.ident_combo.clear()
        self.ident_combo.addItem("All Identifiers", "all")
        for ident in idents:
            self.ident_combo.addItem(ident, ident)
        idx = self.ident_combo.findData(prev)
        if idx >= 0:
            self.ident_combo.setCurrentIndex(idx)
        self.ident_combo.blockSignals(False)

    def _apply_filters(self) -> None:
        if self.category_combo.currentData() == "assets":
            return

        search_q = self.search_edit.text().strip()
        ident_f = self.ident_combo.currentData() or "all"
        color_f = self.color_combo.currentData() or "all"
        latest_only = self.latest_only_chk.isChecked()

        filtered = [
            m
            for m in self._all_media
            if m.matches_filter(
                category_filter="media",
                identifier_filter=ident_f,
                color_filter=color_f,
                search_text=search_q,
            )
        ]

        if latest_only:
            best: Dict[tuple, MediaItem] = {}
            for m in filtered:
                gk = m.group_key
                if gk not in best or m.version_num > best[gk].version_num:
                    best[gk] = m
            filtered = list(best.values())

        self._filtered_media = filtered
        self._populate_table()

    def _populate_table(self) -> None:
        show_shot_col = self.shot_combo.currentData() == "__ALL__"
        self.table.setColumnHidden(self.COL_SHOT, not show_shot_col)

        was_sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        for idx, item in enumerate(self._filtered_media):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setRowHeight(row, 52)

            # Col 0: Preview thumbnail
            prev_it = _SortableItem("", sort_value=idx)
            prev_it.setData(QtCore.Qt.UserRole, item)
            if item.thumb_path and os.path.isfile(item.thumb_path):
                prev_it.setIcon(_render_thumb_icon(item.thumb_path))
            else:
                prev_it.setText("No Preview")
                prev_it.setTextAlignment(QtCore.Qt.AlignCenter)
            self.table.setItem(row, self.COL_PREVIEW, prev_it)

            # Col 1: Shot
            shot_it = _SortableItem(item.shot_id, sort_value=item.shot_id.lower())
            self.table.setItem(row, self.COL_SHOT, shot_it)

            # Col 2: Identifier (with optional color tag dot)
            ident_it = _SortableItem(item.identifier, sort_value=item.identifier.lower())
            if item.color:
                ident_it.setIcon(_make_color_dot_icon(item.color, 9))
            self.table.setItem(row, self.COL_IDENTIFIER, ident_it)

            # Col 3: Version
            ver_it = _SortableItem(item.version, sort_value=item.version_num)
            self.table.setItem(row, self.COL_VERSION, ver_it)

            # Col 4: Range
            rng_it = _SortableItem(item.range_display, sort_value=item.frame_count)
            self.table.setItem(row, self.COL_RANGE, rng_it)

            # Col 5: Format
            fmt = (item.ext or "").lstrip(".").upper()
            fmt_it = _SortableItem(fmt, sort_value=fmt)
            self.table.setItem(row, self.COL_FORMAT, fmt_it)

            # Col 6: Modified
            mod_it = _SortableItem(item.modified_at, sort_value=item.mtime)
            self.table.setItem(row, self.COL_MODIFIED, mod_it)

            # Col 7: File + notes tooltip
            file_display = item.display_file
            if item.notes:
                file_display = f"{item.display_file}  —  {item.notes}"
            file_it = _SortableItem(file_display, sort_value=item.display_file.lower())
            file_it.setToolTip(item.nuke_pattern or item.first_file_path)
            self.table.setItem(row, self.COL_FILE, file_it)

        self.table.setSortingEnabled(was_sorting)
        total = len(self._all_media)
        shown = len(self._filtered_media)
        proj_label = self._project.name if self._project else "No Project"
        self.status_lbl.setText(f"{proj_label}  •  Showing {shown} of {total} media version(s)")
        self._update_button_states()

    # ------------------------------------------------------------------
    # Selection & Actions
    # ------------------------------------------------------------------

    def _selected_media_items(self) -> List[MediaItem]:
        items: List[MediaItem] = []
        seen_rows = set()
        for idx in self.table.selectedIndexes():
            r = idx.row()
            if r in seen_rows:
                continue
            seen_rows.add(r)
            cell = self.table.item(r, self.COL_PREVIEW)
            if cell is not None:
                m = cell.data(QtCore.Qt.UserRole)
                if isinstance(m, MediaItem):
                    items.append(m)
        return items

    def _update_button_states(self) -> None:
        is_assets = self.category_combo.currentData() == "assets"
        sel = [] if is_assets else self._selected_media_items()
        has_sel = bool(sel)
        self.load_read_btn.setEnabled(has_sel)
        self.replace_read_btn.setEnabled(len(sel) == 1)
        self.open_folder_btn.setEnabled(len(sel) == 1)

    def _on_load_as_read(self) -> None:
        import nuke  # type: ignore

        items = self._selected_media_items()
        if not items:
            return

        undo = nuke.Undo()
        undo.begin("Load Media as Read")
        created = []
        try:
            base_x: Optional[int] = None
            base_y: Optional[int] = None
            for idx, item in enumerate(items):
                node = create_read_from_media_item(item)
                if idx == 0:
                    base_x = int(node.xpos())
                    base_y = int(node.ypos())
                elif base_x is not None and base_y is not None:
                    node.setXpos(base_x + idx * 140)
                    node.setYpos(base_y)
                created.append(node)
        finally:
            undo.end()

        self.status_lbl.setText(f"Loaded {len(created)} Read node(s) into Nuke.")

    def _on_replace_selected_read(self) -> None:
        import nuke  # type: ignore

        items = self._selected_media_items()
        if len(items) != 1:
            return
        selected_reads = [n for n in nuke.selectedNodes("Read")]
        if not selected_reads:
            QtWidgets.QMessageBox.information(
                self,
                "No Read Selected",
                "Select one or more Read nodes in Nuke's Node Graph first.",
            )
            return

        undo = nuke.Undo()
        undo.begin("Replace Read with Pipeline Media")
        try:
            for rnode in selected_reads:
                replace_read_with_media_item(rnode, items[0])
        finally:
            undo.end()

        self.status_lbl.setText(
            f"Updated {len(selected_reads)} Read node(s) to {items[0].identifier} ({items[0].version})."
        )

    def _on_open_folder(self) -> None:
        items = self._selected_media_items()
        if len(items) != 1:
            return
        folder = items[0].folder_path or items[0].version_dir
        if folder and os.path.isdir(folder):
            open_path_in_os(folder)


def show_media_loader() -> MediaLoaderDialog:
    """Open (or raise & refresh) the singleton Nuke Media Loader dialog."""
    global _ACTIVE_DIALOG
    parent = _get_nuke_main_window()
    if _ACTIVE_DIALOG is not None:
        try:
            _ACTIVE_DIALOG._ctx = resolve_active_context()
            _ACTIVE_DIALOG._populate_projects()
            _ACTIVE_DIALOG.show()
            _ACTIVE_DIALOG.raise_()
            _ACTIVE_DIALOG.activateWindow()
            return _ACTIVE_DIALOG
        except RuntimeError:
            _ACTIVE_DIALOG = None

    dlg = MediaLoaderDialog(parent=parent)
    dlg.show()
    _ACTIVE_DIALOG = dlg
    return dlg
