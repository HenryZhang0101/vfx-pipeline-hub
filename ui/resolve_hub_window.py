"""
Resolve Hub Window — Lean Notion-Dark DaVinci Resolve Companion for the VFX Pipeline Hub.
"""

import os
from typing import Any, Dict, List, Optional

from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QAbstractItemView,
    QAction,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config import (
    DEFAULT_COMP_IDENTIFIER,
    DEFAULT_HANDLES,
    DEFAULT_SEQ_PREFIX,
    DEFAULT_SHOT_START,
    DEFAULT_SHOT_STEP,
    DEFAULT_STATUSES,
    is_auto_save_excel_enabled,
    load_user_settings,
    open_path_in_os,
    set_auto_save_excel_enabled,
)
from core.io.excel import load_shots_from_excel, save_shots_to_excel
from core.io.resolve import (
    DEFAULT_MARKER_FIELDS,
    capture_shot_thumbnail_to_vfx,
    ensure_qapp,
    find_timeline_comp_version,
    get_current_project,
    get_playhead_clip_info,
    get_resolve_fusion,
    get_resolve_specs,
    get_timeline_by_name,
    get_timeline_fps,
    jump_playhead_to_shot,
    load_resolve_project_binding,
    save_resolve_project_binding,
    sync_shots_vfx_markers,
    update_shot_vfx_marker,
)
from core.models import MediaItem, Project, Shot
from core.storage import ProjectRepository
from core.timecode import format_shot_id, split_shot_id, timecode_to_frame
from tools import ToolRegistry
from ui.controllers import (
    build_qt_clip_info,
    collect_latest_shot_media,
    load_excel_into_project,
    open_send_to_client_dialog,
    register_recent_project,
    save_project_session,
    sync_google_sheet_interactive,
    toggle_always_on_top,
)
from ui.dcc_launcher_window import DCCLauncherWindow
from ui.dialogs import (
    AppSettingsDialog,
    ProjectInitDialog,
    ProjectSettingsDialog,
    RemoveShotsDialog,
    ShotPropertiesDialog,
)
from ui.dialogs.resolve_dialogs import (
    AssignShotDialog,
    BatchAssignTimelineDialog,
    ResolveExportDialog,
    ResolveLoadCompsDialog,
    ResolveMarkerFieldsDialog,
)
from ui.project_manager_window import ProjectManagerWindow
from ui.theme import get_active_stylesheet, get_app_icon
from ui.widgets import ProjectSelectorBar, StatusKPIBar
from ui.widgets.property_widgets import normalize_qcolor


class ResolveHubWindow(QMainWindow):
    """Lean Notion-Dark DaVinci Resolve VFX Companion (Timeline Assign, Plate/Comp Render, Comp Load, Markers)."""

    HEADERS = [
        "Shot ID",
        "Clip Name",
        "Timeline",
        "Start TC",
        "End TC",
        "Duration",
        "Handles",
        "Status",
        "Plate",
        "TL Comp",
        "Comp Status",
        "Notes",
    ]

    def __init__(self, initial_project_root: str = ""):
        ensure_qapp()
        super().__init__()
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("DaVinci Resolve — VFX Pipeline Hub")
        self.setWindowIcon(get_app_icon())
        self.setStyleSheet(get_active_stylesheet())
        self.resize(1340, 760)

        self.resolve, self.fu, _, _, _ = get_resolve_fusion()
        self.repo = ProjectRepository()
        self.tool_registry = ToolRegistry()
        self.project: Optional[Project] = None
        self.vfx_shot_state: Dict[str, Dict[str, Any]] = {}
        self._pm_win: Optional[ProjectManagerWindow] = None
        self._dcc_win: Optional[DCCLauncherWindow] = None

        self.global_seq_name = DEFAULT_SEQ_PREFIX
        self.global_shot_num = DEFAULT_SHOT_START
        self.global_handles = DEFAULT_HANDLES

        self._build_menu_bar()
        self._build_ui()
        self._startup_load(initial_project_root)

    # =========================================================================
    # MENU BAR & UI CONSTRUCTION
    # =========================================================================

    def _build_menu_bar(self):
        mb = self.menuBar()

        # Project menu
        m_proj = mb.addMenu("Project")
        m_proj.setMinimumWidth(240)

        act_init = QAction("Init Project...", self)
        act_init.triggered.connect(self._on_init_project)
        m_proj.addAction(act_init)

        act_open = QAction("Open Project...", self)
        act_open.setShortcut("Ctrl+O")
        m_proj.addAction(act_open)
        self._act_open_proj = act_open

        m_proj.addSeparator()
        act_save = QAction("Save Project", self)
        act_save.setShortcut("Ctrl+S")
        act_save.triggered.connect(lambda: self.save_session(show_status=True))
        m_proj.addAction(act_save)

        act_sync = QAction("Sync Google Sheet", self)
        act_sync.triggered.connect(self._on_sync_google_sheet)
        m_proj.addAction(act_sync)

        act_ref = QAction("Refresh", self)
        act_ref.setShortcut("F5")
        act_ref.triggered.connect(lambda: self.refresh_table(scan_vfx=True))
        m_proj.addAction(act_ref)

        m_proj.addSeparator()
        act_dir = QAction("Open Project Folder", self)
        act_dir.triggered.connect(self._on_open_project_folder)
        m_proj.addAction(act_dir)

        # Excel menu
        m_excel = mb.addMenu("Excel")
        m_excel.setMinimumWidth(250)

        self.auto_save_excel_action = QAction("Auto-Save Excel", self)
        self.auto_save_excel_action.setCheckable(True)
        self.auto_save_excel_action.setChecked(is_auto_save_excel_enabled())
        self.auto_save_excel_action.toggled.connect(self._on_toggle_auto_save_excel)
        m_excel.addAction(self.auto_save_excel_action)

        m_excel.addSeparator()
        act_load_excel = QAction("Load Data from Excel", self)
        act_load_excel.triggered.connect(lambda: self._on_load_from_excel(browse=False))
        m_excel.addAction(act_load_excel)

        act_load_excel_file = QAction("Load from Excel File...", self)
        act_load_excel_file.triggered.connect(lambda: self._on_load_from_excel(browse=True))
        m_excel.addAction(act_load_excel_file)

        m_excel.addSeparator()
        act_save_excel = QAction("Save Data to Excel", self)
        act_save_excel.triggered.connect(lambda: self._on_save_to_excel(browse=False))
        m_excel.addAction(act_save_excel)

        act_save_excel_as = QAction("Save Excel As...", self)
        act_save_excel_as.triggered.connect(lambda: self._on_save_to_excel(browse=True))
        m_excel.addAction(act_save_excel_as)

        m_excel.addSeparator()
        act_excel = QAction("Open VFX Excel", self)
        act_excel.triggered.connect(self._on_open_excel)
        m_excel.addAction(act_excel)

        # Resolve menu
        m_res = mb.addMenu("Resolve")
        m_res.setMinimumWidth(250)

        act_assign = QAction("Assign Playhead Clip...", self)
        act_assign.triggered.connect(self._on_assign_playhead_clip)
        m_res.addAction(act_assign)

        act_batch = QAction("Batch Assign from Timeline...", self)
        act_batch.triggered.connect(self._on_batch_assign_timeline)
        m_res.addAction(act_batch)

        m_res.addSeparator()
        act_exp_plates = QAction("Export Plates...", self)
        act_exp_plates.triggered.connect(
            lambda: self.open_export_plates_dialog(self._get_scoped_shots(), "All Shots", default_mode="plate")
        )
        m_res.addAction(act_exp_plates)

        act_exp_comp = QAction("Render VFX Comp...", self)
        act_exp_comp.triggered.connect(self._on_render_comp_clicked)
        m_res.addAction(act_exp_comp)

        act_load_comps = QAction("Load Comps to Timeline...", self)
        act_load_comps.triggered.connect(
            lambda: self.open_load_comps_dialog(self._get_scoped_shots(), "All Shots")
        )
        m_res.addAction(act_load_comps)

        m_res.addSeparator()
        act_markers = QAction("Sync VFX Timeline Markers", self)
        act_markers.triggered.connect(lambda: self._on_sync_vfx_markers(self._get_selected_shots() or self._get_scoped_shots()))
        m_res.addAction(act_markers)

        act_marker_fields = QAction("Marker Fields...", self)
        act_marker_fields.triggered.connect(self._on_configure_marker_fields)
        m_res.addAction(act_marker_fields)

        act_thumbs = QAction("Capture Missing Thumbnails", self)
        act_thumbs.triggered.connect(self._on_capture_missing_thumbnails)
        m_res.addAction(act_thumbs)

        # Settings menu
        m_set = mb.addMenu("Settings")
        m_set.setMinimumWidth(240)

        act_proj_set = QAction("Project Settings...", self)
        act_proj_set.setShortcut("Ctrl+,")
        act_proj_set.triggered.connect(self._on_open_project_settings)
        m_set.addAction(act_proj_set)

        act_app_set = QAction("App Settings...", self)
        act_app_set.triggered.connect(self._on_open_app_settings)
        m_set.addAction(act_app_set)

        m_set.addSeparator()
        self.aot_action = QAction("Always on Top", self)
        self.aot_action.setCheckable(True)
        self.aot_action.setChecked(True)
        self.aot_action.toggled.connect(lambda c: toggle_always_on_top(self, c))
        m_set.addAction(self.aot_action)

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Top Header Bar (Matches ProjectManagerWindow)
        header = QFrame()
        header.setObjectName("header_bar")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 8, 12, 8)
        h_layout.setSpacing(12)

        self.project_bar = ProjectSelectorBar(self, show_open=False, show_refresh=False)
        self.project_bar.setMaximumWidth(360)
        self.project_bar.project_selected.connect(self.load_project)
        self.project_bar.refresh_requested.connect(lambda: self.refresh_table(scan_vfx=True))
        self._act_open_proj.triggered.connect(self.project_bar._on_browse)
        h_layout.addWidget(self.project_bar)

        h_layout.addStretch(1)

        self.kpi_bar = StatusKPIBar(self)
        self.kpi_bar.status_filter_selected.connect(self._on_kpi_status_selected)
        h_layout.addWidget(self.kpi_bar)

        h_layout.addStretch(1)

        self.sync_status_lbl = QLabel("")
        self.sync_status_lbl.setStyleSheet("color: #9B9A97; font-size: 11px;")
        h_layout.addWidget(self.sync_status_lbl)

        self.sync_btn = QPushButton("Sync Sheet")
        self.sync_btn.setObjectName("primary_btn")
        self.sync_btn.clicked.connect(self._on_sync_google_sheet)
        h_layout.addWidget(self.sync_btn)

        root_layout.addWidget(header)

        # Main Splitter
        body_wrap = QWidget()
        body_layout = QHBoxLayout(body_wrap)
        body_layout.setContentsMargins(10, 10, 10, 10)
        body_layout.setSpacing(10)

        splitter = QSplitter(Qt.Horizontal)
        body_layout.addWidget(splitter)

        # === LEFT PANE: Filters + Resolve Shot Table ===
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(8)

        filter_bar.addWidget(QLabel("Timeline:"))
        self.filter_timeline_cb = QComboBox()
        self.filter_timeline_cb.addItem("All Timelines")
        self.filter_timeline_cb.currentIndexChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.filter_timeline_cb)

        filter_bar.addWidget(QLabel("Seq:"))
        self.filter_seq_cb = QComboBox()
        self.filter_seq_cb.addItem("All Sequences")
        self.filter_seq_cb.currentIndexChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.filter_seq_cb)

        filter_bar.addWidget(QLabel("Status:"))
        self.filter_status_cb = QComboBox()
        self.filter_status_cb.addItem("All Statuses")
        for st in DEFAULT_STATUSES:
            self.filter_status_cb.addItem(st)
        self.filter_status_cb.currentIndexChanged.connect(self._on_status_combo_changed)
        filter_bar.addWidget(self.filter_status_cb)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter shots...")
        self.search_edit.textChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.search_edit, 1)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._on_clear_filter)
        filter_bar.addWidget(clear_btn)
        left_layout.addLayout(filter_bar)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        self.table.itemDoubleClicked.connect(self._on_table_double_clicked)
        left_layout.addWidget(self.table, 1)

        splitter.addWidget(left_widget)

        # === RIGHT PANE: Compact Resolve Action Sidebar ===
        right_widget = QWidget()
        right_widget.setMinimumWidth(260)
        right_widget.setMaximumWidth(310)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        # Group 1: Timeline Shot Assignment
        assign_group = QGroupBox("1. TIMELINE SHOT ASSIGN")
        assign_layout = QVBoxLayout(assign_group)
        assign_layout.setSpacing(8)

        grid_assign = QGridLayout()
        grid_assign.setSpacing(6)
        grid_assign.addWidget(QLabel("Default Seq:"), 0, 0)
        self.quick_seq_edit = QLineEdit(self.global_seq_name)
        grid_assign.addWidget(self.quick_seq_edit, 0, 1)

        grid_assign.addWidget(QLabel("Next Shot:"), 1, 0)
        self.quick_shot_spin = QSpinBox()
        self.quick_shot_spin.setRange(1, 99999)
        self.quick_shot_spin.setSingleStep(DEFAULT_SHOT_STEP)
        self.quick_shot_spin.setValue(self.global_shot_num)
        grid_assign.addWidget(self.quick_shot_spin, 1, 1)
        assign_layout.addLayout(grid_assign)

        self.assign_btn = QPushButton("+ Assign Playhead Clip")
        self.assign_btn.setObjectName("primary_btn")
        self.assign_btn.clicked.connect(self._on_assign_playhead_clip)
        assign_layout.addWidget(self.assign_btn)

        self.batch_assign_btn = QPushButton("Batch Assign from Timeline...")
        self.batch_assign_btn.clicked.connect(self._on_batch_assign_timeline)
        assign_layout.addWidget(self.batch_assign_btn)

        marker_row = QHBoxLayout()
        marker_row.setSpacing(6)
        self.sync_markers_btn = QPushButton("Sync VFX Timeline Markers")
        self.sync_markers_btn.setToolTip("Write selected shot fields onto Resolve timeline markers")
        self.sync_markers_btn.clicked.connect(
            lambda: self._on_sync_vfx_markers(self._get_selected_shots() or self._get_scoped_shots())
        )
        marker_row.addWidget(self.sync_markers_btn, 1)

        self.marker_fields_btn = QPushButton("Fields...")
        self.marker_fields_btn.setToolTip("Choose which shot info is logged in Resolve timeline markers")
        self.marker_fields_btn.clicked.connect(self._on_configure_marker_fields)
        marker_row.addWidget(self.marker_fields_btn)
        assign_layout.addLayout(marker_row)
        right_layout.addWidget(assign_group)

        # Group 2: Render & Conform
        render_group = QGroupBox("2. RENDER & CONFORM")
        render_layout = QVBoxLayout(render_group)
        render_layout.setSpacing(8)

        self.export_all_btn = QPushButton("Export Plates...")
        self.export_all_btn.setObjectName("warn_btn")
        self.export_all_btn.clicked.connect(
            lambda: self.open_export_plates_dialog(
                self._get_selected_shots() or self._get_scoped_shots(),
                f"Selected ({len(self._get_selected_shots())})" if self._get_selected_shots() else "All Shots",
                default_mode="plate",
            )
        )
        render_layout.addWidget(self.export_all_btn)

        self.render_comp_btn = QPushButton("Render VFX Comp...")
        self.render_comp_btn.setObjectName("accent_btn")
        self.render_comp_btn.setToolTip(
            "Render active timeline tracks as a Single Clip composite into media/comp/ and back up .drt/.comp"
        )
        self.render_comp_btn.clicked.connect(self._on_render_comp_clicked)
        render_layout.addWidget(self.render_comp_btn)

        self.load_all_btn = QPushButton("Load Comps to Timeline...")
        self.load_all_btn.setObjectName("success_btn")
        self.load_all_btn.clicked.connect(
            lambda: self.open_load_comps_dialog(
                self._get_selected_shots() or self._get_scoped_shots(),
                f"Selected ({len(self._get_selected_shots())})" if self._get_selected_shots() else "All Shots",
            )
        )
        render_layout.addWidget(self.load_all_btn)

        self.capture_thumbs_btn = QPushButton("Capture Missing Thumbs")
        self.capture_thumbs_btn.setToolTip("Capture missing shot thumbnails from Resolve Color page & update Excel")
        self.capture_thumbs_btn.clicked.connect(self._on_capture_missing_thumbnails)
        render_layout.addWidget(self.capture_thumbs_btn)
        right_layout.addWidget(render_group)

        # Group 3: Pipeline Apps
        apps_group = QGroupBox("3. PIPELINE APPS")
        apps_layout = QVBoxLayout(apps_group)
        apps_layout.setSpacing(8)

        pm_btn = QPushButton("Open Project Manager")
        pm_btn.setToolTip("Open the full Shot Explorer & Media Manager window")
        pm_btn.clicked.connect(self._on_open_project_manager)
        apps_layout.addWidget(pm_btn)

        dcc_btn = QPushButton("Open DCC Launcher")
        dcc_btn.setToolTip("Open the Nuke / Houdini / Blender / Fusion Scene Launcher")
        dcc_btn.clicked.connect(self._on_open_dcc_launcher)
        apps_layout.addWidget(dcc_btn)
        right_layout.addWidget(apps_group)

        right_layout.addStretch()
        splitter.addWidget(right_widget)
        splitter.setSizes([1020, 290])

        root_layout.addWidget(body_wrap, 1)
        self.statusBar().showMessage("Ready.")

    # =========================================================================
    # STARTUP, PROJECT LOADING & PERSISTENCE
    # =========================================================================

    def _startup_load(self, initial_project_root: str = ""):
        saved = load_resolve_project_binding(self.resolve, self.fu)
        if saved:
            self.global_seq_name = saved.get("seq", DEFAULT_SEQ_PREFIX) or DEFAULT_SEQ_PREFIX
            self.global_shot_num = int(saved.get("shot", DEFAULT_SHOT_START) or DEFAULT_SHOT_START)
            self.global_handles = int(saved.get("handles", DEFAULT_HANDLES) or DEFAULT_HANDLES)
            self.quick_seq_edit.setText(self.global_seq_name)
            self.quick_shot_spin.setValue(self.global_shot_num)

        preferred_root = initial_project_root or saved.get("root_dir", "")
        chosen_root = self.project_bar.populate(preferred_root)
        if chosen_root:
            self.load_project(chosen_root)
        else:
            conn_msg = "Connected to DaVinci Resolve" if self.resolve else "Offline mode (DaVinci Resolve not detected)"
            self.statusBar().showMessage(f"{conn_msg} — Use Project > Open or Init Project to begin.")

    def load_project(self, root_dir: str):
        if not root_dir:
            return
        vfx_root = self.repo.resolve_vfx_root(root_dir)
        self.project = self.repo.load(vfx_root)
        if self.resolve:
            fps, (res_w, res_h) = get_resolve_specs(self.resolve)
            if not os.path.isfile(self.project.project_json_path):
                self.project.fps = fps
                self.project.resolution = (float(res_w), float(res_h))

        self.repo.initialize_structure(self.project)

        excel_path = self.project.default_excel_path
        if os.path.isfile(excel_path) and not self.project.all_shots():
            self.project.replace_shots(load_shots_from_excel(excel_path))

        for seq, shot in self.repo.discover_disk_shots(self.project):
            sid = format_shot_id(seq, shot)
            if not self.project.get_shot(sid):
                self.project.add_shot(Shot(shot_id=sid, name=f"{sid}_plate", dur="65 (2.71s)", status="Pending"))

        register_recent_project(self.project.vfx_root)
        self.project_bar.select_or_insert(self.project.vfx_root)
        self.setWindowTitle(f"DaVinci Resolve — {self.project.name} ({self.project.vfx_root})")
        self._sync_status_filter_options()
        self._update_sync_status_label()
        self.refresh_table(scan_vfx=True)
        self.save_session(write_excel=False)

    def save_session(self, write_excel: Optional[bool] = None, show_status: bool = False):
        if self.project:
            do_excel = is_auto_save_excel_enabled() if write_excel is None else bool(write_excel)
            save_project_session(self.project, self.repo, write_excel=do_excel)
            save_resolve_project_binding(
                self.resolve,
                self.fu,
                {
                    "root_dir": self.project.vfx_root,
                    "proj_name": self.project.name,
                    "seq": self.quick_seq_edit.text().strip() or DEFAULT_SEQ_PREFIX,
                    "shot": self.quick_shot_spin.value(),
                    "handles": self.global_handles,
                },
            )
            if show_status:
                if do_excel:
                    self.statusBar().showMessage(
                        f"Saved project.json & {os.path.basename(self.project.default_excel_path)}"
                    )
                else:
                    self.statusBar().showMessage("Saved project.json")

    def get_shot_timeline_name(self, shot: Shot) -> str:
        if not self.project or not shot.seq:
            return ""
        seq_obj = self.project.sequences.get(shot.seq)
        return seq_obj.timeline if seq_obj else ""

    # =========================================================================
    # VFX SCANNING & TABLE RENDERING
    # =========================================================================

    @staticmethod
    def format_comp_display(
        sel_ver: str,
        latest_ver: str,
        tl_ver: str,
        mode: str = "latest",
        ident: str = DEFAULT_COMP_IDENTIFIER,
    ) -> str:
        if not sel_ver:
            return "-"
        suffix_parts = []
        if not tl_ver:
            suffix_parts.append("New")
        elif tl_ver.lower() == sel_ver.lower():
            suffix_parts.append("OK")
        else:
            suffix_parts.append("Update")
        if mode == "manual" or sel_ver.lower() != latest_ver.lower():
            suffix_parts.append("Selected")
        prefix = f"{ident}: " if ident and ident.lower() != DEFAULT_COMP_IDENTIFIER.lower() else ""
        return f"{prefix}{sel_ver} ({', '.join(suffix_parts)})"

    def scan_vfx_for_shots(self):
        self.vfx_shot_state.clear()
        if not self.project:
            return

        r_proj = get_current_project(self.resolve)
        curr_tl = r_proj.GetCurrentTimeline() if r_proj else None
        comp_json = self.project.comp_versions
        json_dirty = False

        for shot in self.project.all_shots():
            if not shot.seq or not shot.shot:
                continue
            tl = (get_timeline_by_name(r_proj, self.get_shot_timeline_name(shot)) if r_proj else None) or curr_tl
            fps = get_timeline_fps(tl) if tl else self.project.fps
            cut_start_f = timecode_to_frame(shot.start_tc, fps) if shot.start_tc else 0

            all_media: List[MediaItem] = self.repo.scan_all_shot_media(self.project, shot.shot_id)
            renders_by_id: Dict[str, List[MediaItem]] = {}
            for m in all_media:
                if m.category != "media":
                    continue
                ident = m.identifier or "plate"
                renders_by_id.setdefault(ident, []).append(m)
            for v_list in renders_by_id.values():
                v_list.sort(key=lambda x: x.version.lower())

            comp_renders_by_id = {
                k: v for k, v in renders_by_id.items() if not k.lower().startswith("plate") and v
            }
            plate_vers = renders_by_id.get("plate") or []
            latest_plate_ver = plate_vers[-1].version if plate_vers else "-"

            shot_entry = comp_json.get(shot.shot_id)
            if not isinstance(shot_entry, dict):
                shot_entry = {}
                comp_json[shot.shot_id] = shot_entry
                json_dirty = True
            saved_ident = shot_entry.get("active_identifier", DEFAULT_COMP_IDENTIFIER)
            if saved_ident in comp_renders_by_id:
                chosen_ident = saved_ident
            elif DEFAULT_COMP_IDENTIFIER in comp_renders_by_id:
                chosen_ident = DEFAULT_COMP_IDENTIFIER
            elif comp_renders_by_id:
                chosen_ident = next(iter(comp_renders_by_id.keys()))
            else:
                chosen_ident = DEFAULT_COMP_IDENTIFIER

            comp_vers: List[MediaItem] = comp_renders_by_id.get(chosen_ident) or []
            tl_ver, existing_trk = find_timeline_comp_version(tl, cut_start_f, shot.seq, shot.shot)
            latest_comp = comp_vers[-1] if comp_vers else None
            selected_comp = latest_comp
            ver_mode = "latest"

            for ident_key, v_list in comp_renders_by_id.items():
                avail_names = [v.version for v in v_list]
                lat_name = v_list[-1].version
                ident_entry = shot_entry.get(ident_key) if isinstance(shot_entry.get(ident_key), dict) else {}
                s_mode = ident_entry.get("mode", "latest")
                s_pub = ident_entry.get("published_version", "")
                if s_mode == "manual" and s_pub in avail_names:
                    eff_mode = "manual"
                    eff_pub = s_pub
                else:
                    eff_mode = "latest"
                    eff_pub = lat_name
                new_ie = {
                    "mode": eff_mode,
                    "published_version": eff_pub,
                    "latest_version": lat_name,
                    "available_versions": avail_names,
                }
                if ident_entry != new_ie:
                    shot_entry[ident_key] = new_ie
                    json_dirty = True

            if comp_vers and latest_comp:
                if shot_entry.get("active_identifier") != chosen_ident:
                    shot_entry["active_identifier"] = chosen_ident
                    json_dirty = True
                active_ie = shot_entry.get(chosen_ident, {})
                ver_mode = active_ie.get("mode", "latest")
                pub_ver = active_ie.get("published_version", latest_comp.version)
                selected_comp = next((v for v in comp_vers if v.version == pub_ver), latest_comp)

            comp_display = (
                self.format_comp_display(selected_comp.version, latest_comp.version, tl_ver, ver_mode, chosen_ident)
                if (selected_comp and latest_comp)
                else "-"
            )

            self.vfx_shot_state[shot.shot_id] = {
                "plate_ver": latest_plate_ver,
                "tl_comp": tl_ver or "None",
                "comp_display": comp_display,
                "identifier": chosen_ident,
                "versions": comp_vers,
                "renders_by_id": comp_renders_by_id,
                "selected_item": selected_comp,
                "mode": ver_mode,
                "existing_trk": existing_trk,
            }

        if json_dirty and self.project:
            self.repo.save(self.project)

    def _sync_status_filter_options(self):
        cur = self.filter_status_cb.currentText()
        statuses = (
            [o.name for o in self.project.get_status_property().options if o.name]
            if self.project
            else list(DEFAULT_STATUSES)
        )
        self.filter_status_cb.blockSignals(True)
        self.filter_status_cb.clear()
        self.filter_status_cb.addItem("All Statuses")
        for st in statuses:
            self.filter_status_cb.addItem(st)
        idx = self.filter_status_cb.findText(cur)
        self.filter_status_cb.setCurrentIndex(idx if idx >= 0 else 0)
        self.filter_status_cb.blockSignals(False)

    def refresh_table(self, scan_vfx: bool = True):
        if not self.project:
            self.table.setRowCount(0)
            return
        if scan_vfx:
            self.scan_vfx_for_shots()

        all_shots = self.project.all_shots()
        all_timelines = sorted({self.get_shot_timeline_name(s) for s in all_shots if self.get_shot_timeline_name(s)})
        all_seqs = sorted({s.seq for s in all_shots if s.seq})

        cur_tl = self.filter_timeline_cb.currentText()
        self.filter_timeline_cb.blockSignals(True)
        self.filter_timeline_cb.clear()
        self.filter_timeline_cb.addItem("All Timelines")
        for tl in all_timelines:
            self.filter_timeline_cb.addItem(tl)
        idx_tl = self.filter_timeline_cb.findText(cur_tl)
        self.filter_timeline_cb.setCurrentIndex(idx_tl if idx_tl >= 0 else 0)
        self.filter_timeline_cb.blockSignals(False)

        cur_seq = self.filter_seq_cb.currentText()
        self.filter_seq_cb.blockSignals(True)
        self.filter_seq_cb.clear()
        self.filter_seq_cb.addItem("All Sequences")
        for seq in all_seqs:
            self.filter_seq_cb.addItem(seq)
        idx_seq = self.filter_seq_cb.findText(cur_seq)
        self.filter_seq_cb.setCurrentIndex(idx_seq if idx_seq >= 0 else 0)
        self.filter_seq_cb.blockSignals(False)

        status_color_map = self.project.get_status_property().color_map()
        filtered_shots = self._get_scoped_shots()
        self.table.setRowCount(len(filtered_shots))

        for r, shot in enumerate(filtered_shots):
            p_state = self.vfx_shot_state.get(shot.shot_id, {})
            cols = [
                shot.shot_id,
                shot.name,
                self.get_shot_timeline_name(shot),
                shot.start_tc,
                shot.end_tc,
                shot.duration,
                shot.handles,
                shot.status,
                p_state.get("plate_ver", "-"),
                p_state.get("tl_comp", "None"),
                p_state.get("comp_display", "-"),
                shot.notes,
            ]
            for c, val in enumerate(cols):
                item = QTableWidgetItem(str(val or ""))
                if c == 7:
                    col_hex = status_color_map.get(shot.status, "")
                    if col_hex:
                        item.setForeground(normalize_qcolor(col_hex))
                elif c == 10:
                    disp = p_state.get("comp_display", "")
                    if "OK" in disp:
                        item.setForeground(QColor("#4DAB9A"))
                    elif "Update" in disp or "Selected" in disp:
                        item.setForeground(QColor("#FFA344"))
                    elif "New" in disp:
                        item.setForeground(QColor("#529CCA"))
                self.table.setItem(r, c, item)

        active_st = self.filter_status_cb.currentText()
        self.kpi_bar.update_data(self.project, all_shots, "" if active_st == "All Statuses" else active_st)
        self.statusBar().showMessage(f"Displaying {len(filtered_shots)} / {len(all_shots)} shot(s)")

    def _get_scoped_shots(self) -> List[Shot]:
        if not self.project:
            return []
        tl_filter = self.filter_timeline_cb.currentText()
        seq_filter = self.filter_seq_cb.currentText()
        status_filter = self.filter_status_cb.currentText()
        search_txt = self.search_edit.text().strip().lower()

        filtered: List[Shot] = []
        for s in self.project.all_shots():
            if tl_filter and tl_filter != "All Timelines" and self.get_shot_timeline_name(s) != tl_filter:
                continue
            if s.matches_filter(seq_filter, status_filter, search_txt):
                filtered.append(s)
        return filtered

    def _get_selected_shots(self) -> List[Shot]:
        sel_rows = sorted({idx.row() for idx in self.table.selectionModel().selectedRows()})
        scoped = self._get_scoped_shots()
        return [scoped[r] for r in sel_rows if 0 <= r < len(scoped)]

    # =========================================================================
    # FILTER & KPI HANDLERS
    # =========================================================================

    def _on_filter_changed(self):
        self.refresh_table(scan_vfx=False)

    def _on_status_combo_changed(self):
        st = self.filter_status_cb.currentText()
        self.kpi_bar.set_active_filter("" if st == "All Statuses" else st)
        self.refresh_table(scan_vfx=False)

    def _on_kpi_status_selected(self, status_name: str):
        target = status_name if status_name else "All Statuses"
        idx = self.filter_status_cb.findText(target)
        if idx >= 0 and idx != self.filter_status_cb.currentIndex():
            self.filter_status_cb.setCurrentIndex(idx)
        else:
            self.refresh_table(scan_vfx=False)

    def _on_clear_filter(self):
        self.search_edit.clear()
        self.filter_timeline_cb.setCurrentIndex(0)
        self.filter_seq_cb.setCurrentIndex(0)
        self.filter_status_cb.setCurrentIndex(0)
        self.kpi_bar.set_active_filter("")
        self.refresh_table(scan_vfx=False)

    # =========================================================================
    # TABLE INTERACTIONS (DOUBLE-CLICK SEEK & CONTEXT MENU)
    # =========================================================================

    def _on_table_double_clicked(self, item: QTableWidgetItem):
        row = item.row()
        scoped = self._get_scoped_shots()
        if not (0 <= row < len(scoped)):
            return
        shot = scoped[row]
        ok, msg, backfilled = jump_playhead_to_shot(self.resolve, shot, self.get_shot_timeline_name(shot))
        if backfilled:
            self.save_session()
            self.refresh_table(scan_vfx=False)
        self.statusBar().showMessage(msg)

    def _on_table_context_menu(self, pos):
        sel_shots = self._get_selected_shots()
        if not sel_shots:
            return
        count = len(sel_shots)
        first = sel_shots[0]

        menu = QMenu(self)
        menu.setStyleSheet(get_active_stylesheet())

        act_props = menu.addAction(f"Shot Properties ({first.shot_id})...")
        menu.addSeparator()

        act_export = menu.addAction(
            f"Export Plate(s) ({count})..." if count > 1 else f"Export Plate ({first.shot_id})..."
        )
        act_comp = menu.addAction(
            f"Render as Comp ({count})..." if count > 1 else f"Render as Comp ({first.shot_id})..."
        )
        act_load = menu.addAction(
            f"Load Comp(s) to Timeline ({count})..." if count > 1 else f"Load Comp to Timeline ({first.shot_id})..."
        )
        menu.addSeparator()

        act_marker = menu.addAction(
            f"Sync Timeline Marker(s) ({count})" if count > 1 else f"Sync Timeline Marker ({first.shot_id})"
        )
        act_thumb = menu.addAction(
            f"Capture Resolve Thumbnail(s) ({count})" if count > 1 else f"Capture Resolve Thumbnail ({first.shot_id})"
        )
        menu.addSeparator()

        act_qt_rev = menu.addAction(f"Send to Client — QT Review ({count})...")
        act_qt_del = menu.addAction(f"Send to Client — QT Delivery ({count})...")
        menu.addSeparator()

        act_folder = menu.addAction(f"Open Shot Folder ({first.shot_id})")
        act_remove = menu.addAction(f"Remove ({count} Shots)..." if count > 1 else f"Remove Shot ({first.shot_id})...")

        action = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if not action:
            return

        if action == act_props:
            self._on_edit_shot_properties(first)
        elif action == act_export:
            self.open_export_plates_dialog(sel_shots, f"Selected ({count})", default_mode="plate")
        elif action == act_comp:
            self.open_export_plates_dialog(sel_shots, f"Selected ({count})", default_mode="comp")
        elif action == act_load:
            self.open_load_comps_dialog(sel_shots, f"Selected ({count})")
        elif action == act_marker:
            self._on_sync_vfx_markers(sel_shots)
        elif action == act_thumb:
            self._on_capture_thumbnails_for_shots(sel_shots)
        elif action == act_qt_rev:
            self._open_qt_export(sel_shots, mode="qt_review")
        elif action == act_qt_del:
            self._open_qt_export(sel_shots, mode="qt_delivery")
        elif action == act_folder:
            if self.project:
                s_dir = self.repo.get_shot_dir(self.project, first.shot_id)
                if os.path.isdir(s_dir):
                    open_path_in_os(s_dir)
                else:
                    self.statusBar().showMessage(f"Shot folder does not exist yet: {s_dir}")
        elif action == act_remove:
            self._on_remove_shots(sel_shots)

    # =========================================================================
    # RESOLVE ACTIONS (ASSIGN, BATCH ASSIGN, EXPORT, LOAD, MARKERS, THUMBS)
    # =========================================================================

    def _upsert_assigned_shot(self, data: Dict[str, Any]) -> Shot:
        assert self.project is not None
        shot_id = data["shot_id"]
        seq, shot_code = split_shot_id(shot_id)
        if not seq:
            seq = data.get("seq", DEFAULT_SEQ_PREFIX)
        if not shot_code:
            shot_code = f"{int(data.get('shot_num', DEFAULT_SHOT_START)):04d}"
        norm_id = format_shot_id(seq, shot_code)

        seq_obj = self.project.get_or_create_sequence(seq)
        if data.get("timeline"):
            seq_obj.timeline = data["timeline"]

        dur_f = int(data.get("dur_f") or 0)
        fps = float(data.get("fps") or self.project.fps or 24.0)
        dur_str = f"{dur_f} ({dur_f / fps:.2f}s)" if dur_f > 0 else ""

        existing = self.project.get_shot(norm_id)
        if existing:
            rec = existing
            rec.name = data.get("clip_name", rec.name)
            rec.start_tc = data.get("start_tc", rec.start_tc)
            rec.end_tc = data.get("end_tc", rec.end_tc)
            if dur_str:
                rec.duration = dur_str
            rec.handles = str(data.get("handles", rec.handles))
            rec.status = data.get("status", rec.status)
            if data.get("notes"):
                rec.notes = data["notes"]
        else:
            rec = Shot(
                shot_id=norm_id,
                name=data.get("clip_name", ""),
                start_tc=data.get("start_tc", ""),
                end_tc=data.get("end_tc", ""),
                duration=dur_str,
                handles=str(data.get("handles", DEFAULT_HANDLES)),
                status=data.get("status", "Pending"),
                notes=data.get("notes", ""),
            )
            self.project.add_shot(rec)

        if data.get("sync_marker", True):
            update_shot_vfx_marker(
                self.resolve,
                rec,
                timeline_name=data.get("timeline", ""),
                marker_fields=self._get_marker_fields(),
            )
        return rec

    def _on_assign_playhead_clip(self):
        if not self.project:
            QMessageBox.warning(self, "No Project", "Please open or initialize a VFX project first.")
            return
        clip_info, err = get_playhead_clip_info(self.resolve)
        if err or not clip_info:
            QMessageBox.warning(self, "Assign Playhead Clip", err or "No clip under playhead.")
            return

        default_seq = self.quick_seq_edit.text().strip() or DEFAULT_SEQ_PREFIX
        default_shot = self.quick_shot_spin.value()
        dlg = AssignShotDialog(self, clip_info, default_seq, default_shot, self.global_handles, project=self.project)
        if dlg.exec_() != QDialog.Accepted:
            return

        res = dlg.result_data
        res["clip_name"] = clip_info["clip_name"]
        res["timeline"] = clip_info["timeline"]
        rec = self._upsert_assigned_shot(res)

        self.quick_seq_edit.setText(res["seq"])
        self.quick_shot_spin.setValue(int(res["shot_num"]) + DEFAULT_SHOT_STEP)
        self.global_handles = int(res["handles"])

        self.refresh_table(scan_vfx=True)
        self.save_session()
        self.statusBar().showMessage(f"Assigned {rec.shot_id} to clip '{clip_info['clip_name']}'.")

    def _on_batch_assign_timeline(self):
        if not self.project:
            QMessageBox.warning(self, "No Project", "Please open or initialize a VFX project first.")
            return
        if not get_current_project(self.resolve):
            QMessageBox.warning(self, "No Resolve Project", "No active DaVinci Resolve project found.")
            return

        dlg = BatchAssignTimelineDialog(
            self,
            self.resolve,
            self.project,
            default_seq=self.quick_seq_edit.text().strip() or DEFAULT_SEQ_PREFIX,
            default_shot=self.quick_shot_spin.value(),
            default_handles=self.global_handles,
        )
        if dlg.exec_() != QDialog.Accepted:
            return

        assignments = dlg.get_selected_assignments()
        if not assignments:
            return

        max_num = self.quick_shot_spin.value()
        last_seq = self.quick_seq_edit.text().strip() or DEFAULT_SEQ_PREFIX
        for item in assignments:
            self._upsert_assigned_shot(item)
            last_seq = item.get("seq", last_seq)
            max_num = max(max_num, int(item.get("shot_num", 0)) + DEFAULT_SHOT_STEP)

        self.quick_seq_edit.setText(last_seq)
        self.quick_shot_spin.setValue(max_num)
        self.global_handles = int(assignments[0].get("handles", self.global_handles))

        self.refresh_table(scan_vfx=True)
        self.save_session()
        self.statusBar().showMessage(f"Batch assigned {len(assignments)} shot(s) from timeline.")

    def _on_render_comp_clicked(self):
        sel = self._get_selected_shots()
        self.open_export_plates_dialog(
            sel or self._get_scoped_shots(),
            f"Selected ({len(sel)})" if sel else "All Shots",
            default_mode="comp",
        )

    def open_export_plates_dialog(
        self,
        target_shots: Optional[List[Shot]] = None,
        scope_label: str = "All Shots",
        default_mode: str = "plate",
    ):
        if not self.project:
            QMessageBox.warning(self, "No Project", "Please open or initialize a VFX project first.")
            return
        target_shots = target_shots if target_shots is not None else self._get_scoped_shots()
        if not target_shots:
            QMessageBox.information(self, "Export", "No shots in current scope to export.")
            return
        dlg = ResolveExportDialog(self, self, target_shots, scope_label, default_mode=default_mode)
        dlg.exec_()

    def open_load_comps_dialog(self, target_shots: Optional[List[Shot]] = None, scope_label: str = "All Shots"):
        if not self.project:
            QMessageBox.warning(self, "No Project", "Please open or initialize a VFX project first.")
            return
        target_shots = target_shots if target_shots is not None else self._get_scoped_shots()
        if not target_shots:
            QMessageBox.information(self, "Load Comps", "No shots in current scope to load comps for.")
            return
        dlg = ResolveLoadCompsDialog(self, self, target_shots, scope_label)
        dlg.exec_()

    def _get_marker_fields(self) -> List[str]:
        saved = load_user_settings().get("resolve_marker_fields")
        if isinstance(saved, list):
            return [str(f) for f in saved]
        return list(DEFAULT_MARKER_FIELDS)

    def _on_configure_marker_fields(self):
        sel = self._get_selected_shots()
        sample = sel[0] if sel else (self._get_scoped_shots()[0] if self._get_scoped_shots() else None)
        dlg = ResolveMarkerFieldsDialog(self, project=self.project, sample_shot=sample)
        if dlg.exec_() == QDialog.Accepted:
            self.statusBar().showMessage(
                f"Saved Resolve marker fields: {', '.join(dlg.selected_fields()) or '(none)'}"
            )

    def _on_sync_vfx_markers(self, shots: List[Shot]):
        if not self.project or not shots:
            return
        updated, skipped = sync_shots_vfx_markers(
            self.resolve,
            self.project,
            shots,
            marker_fields=self._get_marker_fields(),
        )
        self.statusBar().showMessage(f"Synced VFX timeline markers: {updated} updated, {skipped} skipped.")

    def _on_capture_thumbnails_for_shots(self, shots: List[Shot]):
        if not self.project or not shots:
            return
        r_proj = get_current_project(self.resolve)
        if not r_proj:
            QMessageBox.warning(self, "No Resolve Project", "No active DaVinci Resolve project found.")
            return
        captured = 0
        for s in shots:
            if s.shot_id and s.start_tc:
                if capture_shot_thumbnail_to_vfx(
                    self.resolve, self.project, s, timeline_name=self.get_shot_timeline_name(s), resolve_project=r_proj
                ):
                    captured += 1
        if self.resolve:
            self.resolve.OpenPage("edit")
        self.save_session()
        self.statusBar().showMessage(f"Captured {captured} thumbnail(s) from DaVinci Resolve.")

    def _on_capture_missing_thumbnails(self):
        if not self.project:
            return
        missing = [
            s
            for s in self.project.all_shots()
            if s.shot_id and s.start_tc and not os.path.isfile(os.path.join(self.project.thumbnails_dir, f"{s.shot_id}.png"))
        ]
        if not missing:
            self.save_session()
            self.statusBar().showMessage("All shots already have thumbnails.")
            return
        self._on_capture_thumbnails_for_shots(missing)

    # =========================================================================
    # SHARED PIPELINE WORKFLOWS (PROPERTIES, REMOVE, QT EXPORT, CLOUD SYNC, APPS)
    # =========================================================================

    def _on_edit_shot_properties(self, shot: Shot):
        if not self.project or not shot:
            return
        dlg = ShotPropertiesDialog(self, self.project, shot)
        if dlg.exec_() == QDialog.Accepted:
            self._sync_status_filter_options()
            self.refresh_table(scan_vfx=True)
            self.save_session()
            self.statusBar().showMessage(f"Updated properties for {shot.shot_id}.")

    def _on_remove_shots(self, shots: List[Shot]):
        if not self.project or not shots:
            return
        shot_ids = [s.shot_id for s in shots]
        dlg = RemoveShotsDialog(self, shot_ids)
        if dlg.exec_() != QDialog.Accepted:
            return

        delete_from_disk = dlg.delete_from_disk
        errors: List[str] = []
        removed = 0
        for sid in shot_ids:
            ok, msg = self.repo.remove_shot(self.project, sid, delete_from_disk=delete_from_disk)
            if ok:
                removed += 1
            else:
                errors.append(msg)

        if removed:
            self.refresh_table(scan_vfx=True)
            self.save_session()
            mode_note = "deleted from disk" if delete_from_disk else "kept files on disk"
            self.statusBar().showMessage(f"Removed {removed} shot(s) ({mode_note}).")
        if errors:
            QMessageBox.warning(self, "Shot Removal Error", "\n\n".join(errors))

    def _open_qt_export(self, shots: List[Shot], mode: str = "qt_review"):
        if not self.project or not shots:
            return
        slate_tool = self.tool_registry.get_tool("slate_delivery")
        if not slate_tool:
            QMessageBox.warning(self, "Tool Unavailable", "Slate & QT Delivery tool is not enabled.")
            return
        found, _missing = collect_latest_shot_media(self.project, self.repo, shots)
        clips = []
        for sid, media_rec in found:
            c_info = build_qt_clip_info(self.project, slate_tool, sid, media_rec)
            if c_info:
                clips.append(c_info)
        open_send_to_client_dialog(self, self.project, slate_tool, clips, initial_mode=mode, repo=self.repo)

    def _on_init_project(self):
        default_root = self.project.vfx_root if self.project else ""
        dlg = ProjectInitDialog(self, default_root=default_root)
        if dlg.exec_() != QDialog.Accepted:
            return
        project = Project(
            root_dir=dlg.root_dir,
            name=dlg.project_name,
            fps=dlg.fps,
            resolution=dlg.resolution,
            export_folder=dlg.export_folder,
        )
        ok, msg = self.repo.initialize_structure(project)
        if not ok:
            QMessageBox.warning(self, "Init Failed", msg)
            return
        if dlg.initial_sequence:
            project.get_or_create_sequence(dlg.initial_sequence)
            os.makedirs(os.path.join(project.vfx_root, dlg.initial_sequence), exist_ok=True)
        self.repo.save(project)
        if dlg.create_excel and not os.path.exists(project.default_excel_path):
            save_shots_to_excel(
                project.default_excel_path,
                project.all_shots(),
                thumb_dir=project.thumbnails_dir,
                project_name=project.name,
            )
        self.project_bar.select_or_insert(dlg.root_dir)
        self.load_project(dlg.root_dir)

    def _on_open_project_settings(self):
        if not self.project:
            QMessageBox.information(self, "Project Settings", "Open or initialize a project first.")
            return
        dlg = ProjectSettingsDialog(self, self.project)
        if dlg.exec_() == QDialog.Accepted:
            self.repo.save(self.project)
            self._sync_status_filter_options()
            self._update_sync_status_label()
            self.refresh_table(scan_vfx=False)
            self.save_session()

    def _on_open_app_settings(self):
        dlg = AppSettingsDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            self.setStyleSheet(get_active_stylesheet())
            self.project_bar.reload_from_settings()
            self.statusBar().showMessage("Updated app settings.")

    def _update_sync_status_label(self):
        if not self.project or not self.project.google_sheet_url:
            self.sync_status_lbl.setText("Cloud: Not linked")
            self.sync_status_lbl.setStyleSheet("color: #9B9A97; font-size: 11px;")
        elif self.project.last_sync_time:
            t_part = self.project.last_sync_time.split(" ")[-1]
            self.sync_status_lbl.setText(f"Synced {t_part}")
            self.sync_status_lbl.setStyleSheet("color: #4DAB9A; font-size: 11px;")
        else:
            self.sync_status_lbl.setText("Cloud: Ready")
            self.sync_status_lbl.setStyleSheet("color: #529CCA; font-size: 11px;")

    def _on_sync_google_sheet(self):
        if not self.project:
            QMessageBox.warning(self, "Sync Error", "No active project.")
            return
        ok, msg = sync_google_sheet_interactive(
            self,
            self.project,
            self.repo,
            on_pulled=lambda: self.refresh_table(scan_vfx=False),
        )
        if not ok and msg == "NO_URL":
            reply = QMessageBox.question(
                self,
                "Google Sheet Not Connected",
                "Google Sheet Webhook URL is not configured.\n\nOpen Project Settings > Cloud Sync now?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                dlg = ProjectSettingsDialog(self, self.project, initial_page="Cloud Sync")
                if dlg.exec_() == QDialog.Accepted:
                    self.repo.save(self.project)
                    self._update_sync_status_label()
            return
        self._update_sync_status_label()
        if ok:
            self.statusBar().showMessage("Google Sheet sync complete.")

    def _on_open_project_folder(self):
        if self.project and os.path.isdir(self.project.vfx_root):
            open_path_in_os(self.project.vfx_root)

    def _on_toggle_auto_save_excel(self, checked: bool):
        set_auto_save_excel_enabled(checked)
        state = "ON" if checked else "OFF"
        self.statusBar().showMessage(f"Auto-Save Excel: {state}")

    def _on_load_from_excel(self, browse: bool = False):
        if not self.project:
            QMessageBox.information(self, "No Project Loaded", "Open or initialize a project first.")
            return
        if browse:
            start = (
                self.project.default_excel_path
                if os.path.isfile(self.project.default_excel_path)
                else self.project.vfx_root
            )
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Load Shot Data from Excel",
                start,
                "Excel Workbooks (*.xlsx);;All Files (*)",
            )
            if not file_path:
                return
        else:
            file_path = self.project.default_excel_path
            if not os.path.isfile(file_path):
                QMessageBox.information(
                    self,
                    "Excel File Not Found",
                    f"Project Excel file does not exist yet:\n{file_path}\n\n"
                    "Use 'Save Data to Excel' first or choose 'Load from Excel File...'.",
                )
                return

        ok, msg, _updated, _added = load_excel_into_project(self.project, self.repo, file_path)
        if not ok:
            QMessageBox.warning(self, "Excel Load", msg)
            return
        self._sync_status_filter_options()
        self.refresh_table(scan_vfx=False)
        self.statusBar().showMessage(msg)

    def _on_save_to_excel(self, browse: bool = False):
        if not self.project:
            QMessageBox.information(self, "No Project Loaded", "Open or initialize a project first.")
            return
        if browse:
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save Shot Data to Excel",
                self.project.default_excel_path,
                "Excel Workbooks (*.xlsx);;All Files (*)",
            )
            if not file_path:
                return
            if not file_path.lower().endswith(".xlsx"):
                file_path += ".xlsx"
        else:
            file_path = self.project.default_excel_path

        now_ts = save_project_session(self.project, self.repo, write_excel=False)
        ok = save_shots_to_excel(
            file_path,
            self.project.all_shots(),
            thumb_dir=self.project.thumbnails_dir,
            project_name=self.project.name,
            last_modified=now_ts,
            custom_field_names=self.project.shot_custom_fields,
            status_colors=self.project.get_status_property().color_map(),
        )
        if ok:
            self.statusBar().showMessage(
                f"Saved Excel to {os.path.basename(file_path)} ({len(self.project.all_shots())} shots)"
            )
        else:
            QMessageBox.warning(self, "Excel Save Failed", f"Could not write Excel file:\n{file_path}")

    def _on_open_excel(self):
        if not self.project:
            return
        excel_path = self.project.default_excel_path
        if not os.path.isfile(excel_path):
            self.save_session(write_excel=True)
        if os.path.isfile(excel_path):
            open_path_in_os(excel_path)

    def _on_open_project_manager(self):
        root_dir = self.project.vfx_root if self.project else ""
        self._pm_win = ProjectManagerWindow(initial_project_root=root_dir)
        self._pm_win.show()
        self._pm_win.raise_()
        self._pm_win.activateWindow()

    def _on_open_dcc_launcher(self):
        root_dir = self.project.vfx_root if self.project else ""
        sel = self._get_selected_shots()
        sid = sel[0].shot_id if sel else ""
        self._dcc_win = DCCLauncherWindow(initial_project_root=root_dir, initial_shot_id=sid)
        self._dcc_win.show()
        self._dcc_win.raise_()
        self._dcc_win.activateWindow()

    def closeEvent(self, event):
        self.save_session()
        event.accept()


def main():
    app = ensure_qapp()
    win = ResolveHubWindow()
    win.show()
    if app is not None:
        if not hasattr(app, "_pipeline_hub_instances"):
            app._pipeline_hub_instances = []
        app._pipeline_hub_instances.append(win)
        app.exec_()


if __name__ == "__main__":
    main()
