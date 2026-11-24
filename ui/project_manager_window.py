"""
Project Manager Window — Production, Shot & Media Management App.
"""

import os
from typing import Any, Dict, List, Optional

from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import (
    QAction,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from config import (
    APP_VERSION,
    get_github_repo,
    is_auto_save_excel_enabled,
    load_user_settings,
    open_path_in_os,
    set_auto_save_excel_enabled,
)
from core.io.excel import load_shots_from_excel, save_shots_to_excel
from core.models import Project, Shot
from core.storage import ProjectRepository
from core.thumbnails import set_shot_thumbnail_from_image
from core.timecode import format_shot_id
from dccs import DCCRegistry
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
    MediaIngestDialog,
    NewShotDialog,
    ProjectInitDialog,
    ProjectSettingsDialog,
    RemoveShotsDialog,
    ShotPropertiesDialog,
    UpdateDialog,
)
from ui.dialogs.update_dialog import CheckUpdateWorker
from ui.panels import MediaManagerPanel, ShotExplorerPanel
from ui.theme import get_active_stylesheet, get_app_icon
from ui.widgets import (
    ExcelExportWorker,
    MediaScanWorker,
    ProjectSelectorBar,
    StatusKPIBar,
    ThumbnailService,
    clear_thumbnail_pixmap_cache,
)


class ProjectManagerWindow(QMainWindow):
    """Standalone VFX Project Manager GUI (Notion Dark minimalist layout)."""

    def __init__(self, initial_project_root: str = ""):
        super().__init__()
        self.dcc_registry = DCCRegistry()
        self.tool_registry = ToolRegistry()
        self.repo = ProjectRepository()
        self.project: Optional[Project] = None
        self.current_shot: Optional[Shot] = None
        self.selected_shots: List[Shot] = []
        self._dcc_launcher_win: Optional[DCCLauncherWindow] = None
        self.thumb_service = ThumbnailService(self)
        self.thumb_service.shot_thumbnail_ready.connect(self._on_shot_thumbnail_ready)
        self.thumb_service.media_thumbnail_ready.connect(self._on_media_thumbnail_ready)
        self.thumb_service.batch_progress.connect(self._on_thumb_batch_progress)
        self.thumb_service.batch_finished.connect(self._on_thumb_batch_finished)

        self._media_scan_worker = MediaScanWorker(self)
        self._media_scan_worker.scan_finished.connect(self._on_media_scan_finished)
        self._media_scan_worker.start()
        self._active_scan_gen: int = 0

        self._excel_worker = ExcelExportWorker(self)
        self._excel_worker.export_finished.connect(self._on_excel_export_finished)
        self._excel_worker.start()

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(300)
        self._autosave_timer.timeout.connect(self._flush_json_autosave)
        self._json_dirty: bool = False

        self._selection_timer = QTimer(self)
        self._selection_timer.setSingleShot(True)
        self._selection_timer.setInterval(80)
        self._selection_timer.timeout.connect(self._dispatch_selected_media_scan)

        self._update_check_worker: Optional[CheckUpdateWorker] = None
        self._latest_update_info = None

        self.setWindowTitle(f"Project Manager  (v{APP_VERSION})")
        self.setWindowIcon(get_app_icon())
        self.resize(1340, 820)
        self.setStyleSheet(get_active_stylesheet())

        self._build_menu_bar()
        self._build_ui()

        first_root = self.project_bar.populate(initial_project_root)
        if first_root:
            self.load_project(first_root)

        QTimer.singleShot(2000, self._start_background_update_check)

    # =========================================================================
    # MENU BAR & UI CONSTRUCTION
    # =========================================================================

    def _build_menu_bar(self):
        mb = self.menuBar()

        m_proj = mb.addMenu("Project")
        m_proj.setMinimumWidth(260)
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
        act_save.triggered.connect(lambda: self.save_shots_session(show_toast=True))
        m_proj.addAction(act_save)

        act_sync = QAction("Sync Google Sheet", self)
        act_sync.triggered.connect(self._on_sync_google_sheet)
        m_proj.addAction(act_sync)

        act_ref = QAction("Refresh", self)
        act_ref.setShortcut("F5")
        act_ref.triggered.connect(self.refresh_all)
        m_proj.addAction(act_ref)

        m_proj.addSeparator()
        act_proj_dir = QAction("Open Project Folder", self)
        act_proj_dir.triggered.connect(self._on_open_project_folder)
        m_proj.addAction(act_proj_dir)

        m_excel = mb.addMenu("Excel")
        m_excel.setMinimumWidth(260)
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
        act_excel.triggered.connect(self._on_open_vfx_excel)
        m_excel.addAction(act_excel)

        m_set = mb.addMenu("Settings")
        m_set.setMinimumWidth(260)
        act_proj_set = QAction("Project Settings...", self)
        act_proj_set.setShortcut("Ctrl+,")
        act_proj_set.triggered.connect(lambda: self._on_open_project_settings())
        m_set.addAction(act_proj_set)

        act_app_set = QAction("App Settings...", self)
        act_app_set.triggered.connect(self._on_open_app_settings)
        m_set.addAction(act_app_set)

        act_check_upd = QAction("Check for Updates...", self)
        act_check_upd.triggered.connect(self._on_check_for_updates)
        m_set.addAction(act_check_upd)

        m_set.addSeparator()
        self.aot_action = QAction("Always on Top", self)
        self.aot_action.setCheckable(True)
        self.aot_action.toggled.connect(lambda c: toggle_always_on_top(self, c))
        m_set.addAction(self.aot_action)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Ultra-Minimal Top Header Bar: [Project Dropdown]  ---  [Inline KPI Summary]  ---  [Sync Status + Sync Sheet]
        header = QFrame()
        header.setObjectName("header_bar")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 8, 12, 8)
        h_layout.setSpacing(12)

        self.project_bar = ProjectSelectorBar(self, show_open=False, show_refresh=False)
        self.project_bar.setMaximumWidth(360)
        self.project_bar.project_selected.connect(self.load_project)
        self.project_bar.refresh_requested.connect(self.refresh_all)
        self._act_open_proj.triggered.connect(self.project_bar._on_browse)
        h_layout.addWidget(self.project_bar)

        h_layout.addStretch(1)

        self.kpi_bar = StatusKPIBar(self)
        h_layout.addWidget(self.kpi_bar)

        h_layout.addStretch(1)

        self.sync_status_lbl = QLabel("")
        self.sync_status_lbl.setStyleSheet("color: #9B9A97; font-size: 11px;")
        h_layout.addWidget(self.sync_status_lbl)

        self.sync_btn = QPushButton("Sync Sheet")
        self.sync_btn.setObjectName("primary_btn")
        self.sync_btn.clicked.connect(self._on_sync_google_sheet)
        h_layout.addWidget(self.sync_btn)

        self.update_btn = QPushButton("Updates")
        self.update_btn.setToolTip(f"Check for updates on GitHub (current: v{APP_VERSION})")
        self.update_btn.clicked.connect(self._on_check_for_updates)
        h_layout.addWidget(self.update_btn)

        root_layout.addWidget(header)

        # Main Two-Pane Splitter: Left Shot Explorer | Right Media Manager
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setContentsMargins(8, 8, 8, 8)

        self.shot_explorer = ShotExplorerPanel(self)
        self.shot_explorer.shots_selection_changed.connect(self._on_shots_selected)
        self.shot_explorer.shot_data_changed.connect(self._schedule_json_autosave)
        self.shot_explorer.kpi_data_updated.connect(self.kpi_bar.update_data)
        self.shot_explorer.status_filter_changed.connect(self.kpi_bar.set_active_filter)
        self.kpi_bar.status_filter_selected.connect(self.shot_explorer.set_status_filter)
        self.shot_explorer.create_shot_requested.connect(self._on_create_new_shot)
        self.shot_explorer.edit_shot_properties_requested.connect(self._on_edit_shot_properties)
        self.shot_explorer.manage_properties_requested.connect(
            lambda: self._on_open_project_settings(initial_page="Shot Properties")
        )
        self.shot_explorer.open_in_dcc_launcher_requested.connect(self._on_open_in_dcc_launcher)
        self.shot_explorer.remove_shots_requested.connect(self._on_remove_selected_shots)
        self.shot_explorer.ingest_requested.connect(lambda: self._on_open_media_ingester(new_shot_mode=False))
        self.shot_explorer.ingest_new_shot_requested.connect(lambda: self._on_open_media_ingester(new_shot_mode=True))
        self.shot_explorer.open_shot_folder_requested.connect(self._on_open_shot_folder)
        self.shot_explorer.save_requested.connect(lambda: self.save_shots_session(show_toast=True))
        self.shot_explorer.qt_export_from_shots_requested.connect(self._open_qt_export_from_shots)
        self.shot_explorer.play_latest_in_djv_requested.connect(self._on_play_latest_from_shots)
        self.shot_explorer.generate_thumbnails_requested.connect(self._on_generate_selected_shot_thumbnails)
        self.shot_explorer.set_custom_thumbnail_requested.connect(self._on_set_custom_shot_thumbnail)
        self.main_splitter.addWidget(self.shot_explorer)

        self.media_panel = MediaManagerPanel(self)
        self.media_panel.ingest_requested.connect(
            lambda sid="": self._on_open_media_ingester(new_shot_mode=False, target_shot_id=sid)
        )
        self.media_panel.qt_export_from_media_requested.connect(self._open_qt_export_from_media)
        self.media_panel.status_message.connect(self.statusBar().showMessage)
        self.media_panel.generate_media_thumbnails_requested.connect(self._on_generate_selected_media_thumbnails)
        self.media_panel.set_as_shot_thumbnail_requested.connect(self._on_set_media_as_shot_thumbnail)
        self.media_panel.media_metadata_changed.connect(self._schedule_json_autosave)
        self.main_splitter.addWidget(self.media_panel)

        self.main_splitter.setStretchFactor(0, 4)
        self.main_splitter.setStretchFactor(1, 6)
        root_layout.addWidget(self.main_splitter, 1)

        self.statusBar().showMessage("Ready.")
        self._rebuild_dynamic_module_ui()

    def _rebuild_dynamic_module_ui(self):
        self.media_panel.update_module_visibility(self.tool_registry)
        self.shot_explorer.update_module_visibility(self.tool_registry)

    # =========================================================================
    # PROJECT INITIALIZATION & LOADING
    # =========================================================================

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
        self.statusBar().showMessage(f"Initialized '{project.name}' at {project.vfx_root}")

    def load_project(self, root_dir: str):
        if not root_dir:
            return
        self._flush_json_autosave()
        self.thumb_service.clear_pending()
        self._media_scan_worker.cancel_pending()
        self.repo.invalidate_media_cache()
        vfx_root = self.repo.resolve_vfx_root(root_dir)
        self.project = self.repo.load(vfx_root)
        self.repo.initialize_structure(self.project)
        register_recent_project(self.project.vfx_root)
        self.project_bar.select_or_insert(self.project.vfx_root)
        self.setWindowTitle(f"Project Manager — {self.project.name} ({self.project.vfx_root})")
        self.refresh_all()

    def refresh_all(self):
        if not self.project:
            return

        self.repo.invalidate_media_cache(project=self.project)
        clear_thumbnail_pixmap_cache()

        excel_path = self.project.default_excel_path
        if os.path.isfile(excel_path) and not self.project.all_shots():
            self.project.replace_shots(load_shots_from_excel(excel_path))

        for seq, shot in self.repo.discover_disk_shots(self.project):
            sid = format_shot_id(seq, shot)
            if not self.project.get_shot(sid):
                self.project.add_shot(Shot(shot_id=sid, name=f"{sid}_plate", dur="65 (2.71s)", status="Pending"))

        shots = self.project.all_shots()
        prev_sid = self.current_shot.shot_id if self.current_shot else ""
        self.shot_explorer.set_context(
            self.project,
            self.repo,
            self.tool_registry,
            shots,
            preserve_shot_id=prev_sid,
        )
        # Queue visible shots at high priority first, then remaining shots
        visible_ids = {s.shot_id for s in self.shot_explorer.visible_shots()}
        for sh in shots:
            if sh.shot_id in visible_ids:
                self.thumb_service.queue_shot(self.project, self.repo, sh, force=False, priority=0)
        for sh in shots:
            if sh.shot_id not in visible_ids:
                self.thumb_service.queue_shot(self.project, self.repo, sh, force=False, priority=1)
        self._update_sync_label()
        self.statusBar().showMessage(f"{self.project.name} — {len(shots)} shot(s)")

    # =========================================================================
    # SHOT CONTEXT & POPUP PROPERTIES
    # =========================================================================

    def _on_shots_selected(self, shots: List[Shot]):
        self.selected_shots = [s for s in (shots or []) if s is not None]
        self.current_shot = self.selected_shots[0] if self.selected_shots else None
        self.thumb_service.clear_media_pending()
        self.media_panel.set_loading_state(self.project, self.repo, self.tool_registry, self.selected_shots)
        if not self.project or not self.selected_shots:
            self._selection_timer.stop()
            self._active_scan_gen = self._media_scan_worker.cancel_pending()
            return
        self._selection_timer.start()

    def _dispatch_selected_media_scan(self) -> None:
        if not self.project or not self.selected_shots:
            return
        shot_ids = [s.shot_id for s in self.selected_shots if s.shot_id]
        self._active_scan_gen = self._media_scan_worker.request_scan(
            self.project, self.repo, shot_ids, use_cache=True
        )

    def _on_media_scan_finished(self, gen_id: int, _shot_ids: List[str], media_items: List[Any]) -> None:
        if gen_id != self._active_scan_gen or not self.project:
            return
        self.media_panel.apply_scanned_media(media_items)
        # Queue thumbnails for visible media items first (priority 0), then offscreen items (priority 1)
        visible_keys = {m.media_key for m in self.media_panel.visible_media_items()}
        for m in media_items:
            if m.is_playable and not m.has_thumb and m.media_key in visible_keys:
                self.thumb_service.queue_media(self.project, m, force=False, priority=0)
        for m in media_items:
            if m.is_playable and not m.has_thumb and m.media_key not in visible_keys:
                self.thumb_service.queue_media(self.project, m, force=False, priority=1)

    def _on_shot_selected(self, rec: Optional[Shot]):
        self._on_shots_selected([rec] if rec else [])

    def _on_edit_shot_properties(self):
        if not self.project or not self.current_shot:
            return
        dlg = ShotPropertiesDialog(self, self.project, self.current_shot)
        accepted = dlg.exec_() == QDialog.Accepted
        if accepted or getattr(dlg, "schema_modified", False) or getattr(dlg, "thumb_modified", False):
            self.save_shots_session(show_toast=False)
            self.shot_explorer.set_context(
                self.project,
                self.repo,
                self.tool_registry,
                self.project.all_shots(),
                preserve_shot_id=self.current_shot.shot_id,
            )
            if accepted:
                self.statusBar().showMessage(f"Updated {self.current_shot.shot_id}")
            else:
                self.statusBar().showMessage("Updated project shot properties.")

    def _on_create_new_shot(self):
        if not self.project:
            return
        shots = self.project.all_shots()
        existing_seqs = sorted({s.seq for s in shots if s.seq} | set(self.project.sequences.keys()))
        next_num = (len(shots) + 1) * 10
        prefix = existing_seqs[0] if existing_seqs else "R01"
        default_sid = f"{prefix}-{next_num:04d}"

        dlg = NewShotDialog(
            self,
            default_shot_id=default_sid,
            existing_sequences=existing_seqs,
            default_start_frame=self.project.default_start_frame,
            default_handles=self.project.default_handles,
            project_resolution=self.project.resolution,
            resolution_presets=self.project.resolution_presets,
        )
        if dlg.exec_() == QDialog.Accepted:
            dur = dlg.end_frame - dlg.start_frame + 1
            full_sid = format_shot_id(dlg.seq, dlg.shot)
            self.repo.ensure_shot(self.project, full_sid, start_frame=dlg.start_frame)
            sh_obj = self.project.get_shot(full_sid)
            if sh_obj:
                if not sh_obj.name:
                    sh_obj.name = f"{full_sid}_plate"
                sh_obj.duration = f"{dur} ({dur / max(1.0, self.project.fps):.2f}s)"
                sh_obj.handles = str(dlg.handles)
                if dlg.resolution:
                    sh_obj.resolution = dlg.resolution
                if dlg.notes:
                    sh_obj.notes = dlg.notes
            self.current_shot = sh_obj
            self.save_shots_session(show_toast=False)
            self.refresh_all()

    def _on_open_shot_folder(self):
        if not self.current_shot or not self.project:
            return
        shot_dir = self.repo.get_shot_dir(self.project, self.current_shot.shot_id)
        os.makedirs(shot_dir, exist_ok=True)
        open_path_in_os(shot_dir)

    def _on_open_project_folder(self):
        if not self.project or not self.project.root_dir:
            return
        open_path_in_os(self.project.root_dir)

    def _on_toggle_auto_save_excel(self, checked: bool):
        set_auto_save_excel_enabled(checked)
        state = "ON" if checked else "OFF"
        self.statusBar().showMessage(f"Auto-Save Excel: {state}")

    def _on_load_from_excel(self, browse: bool = False):
        if not self.project:
            QMessageBox.information(self, "No Project Loaded", "Open or initialize a project first.")
            return
        self._flush_json_autosave()
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
        self.refresh_all()
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
        self._excel_worker.enqueue_export(self.project, now_ts, file_path=file_path)
        self.statusBar().showMessage(
            f"Exporting Excel to {os.path.basename(file_path)} ({len(self.project.all_shots())} shots)..."
        )

    def _on_open_vfx_excel(self):
        if not self.project:
            return
        excel_path = self.project.default_excel_path
        if not os.path.isfile(excel_path):
            save_project_session(self.project, self.repo, write_excel=True)
        if os.path.isfile(excel_path):
            open_path_in_os(excel_path)

    def _on_open_in_dcc_launcher(self):
        if not self.project:
            return
        sid = self.current_shot.shot_id if self.current_shot else ""
        if self._dcc_launcher_win is None:
            self._dcc_launcher_win = DCCLauncherWindow(
                initial_project_root=self.project.root_dir,
                initial_shot_id=sid,
            )
        else:
            self._dcc_launcher_win.load_project(self.project.root_dir, initial_shot_id=sid)
        self._dcc_launcher_win.show()
        self._dcc_launcher_win.raise_()
        self._dcc_launcher_win.activateWindow()

    def _on_remove_selected_shots(self):
        if not self.project:
            return
        selected = self.shot_explorer.selected_shots()
        if not selected:
            return

        shot_ids = [s.shot_id for s in selected]
        dlg = RemoveShotsDialog(self, shot_ids)
        if dlg.exec_() != QDialog.Accepted:
            return

        delete_from_disk = dlg.delete_from_disk
        removed_ids: List[str] = []
        error_msgs: List[str] = []
        for sid in shot_ids:
            ok, msg = self.repo.remove_shot(
                self.project,
                sid,
                delete_from_disk=delete_from_disk,
            )
            if ok:
                removed_ids.append(sid)
            else:
                error_msgs.append(msg)

        if removed_ids:
            if self.current_shot and self.current_shot.shot_id in removed_ids:
                self.current_shot = None
            self.selected_shots = [s for s in self.selected_shots if s.shot_id not in removed_ids]
            self.save_shots_session(show_toast=False)
            self.refresh_all()
            mode_note = "deleted folder(s) from disk" if delete_from_disk else "kept files on disk"
            self.statusBar().showMessage(
                f"Removed {len(removed_ids)} shot(s) ({mode_note}): {', '.join(removed_ids)}"
            )

        if error_msgs:
            QMessageBox.warning(
                self,
                "Shot Removal Error",
                "\n\n".join(error_msgs),
            )

    def _on_open_exports_root(self):
        if not self.project or not self.project.exports_dir:
            return
        exp_dir = (
            os.path.join(self.project.exports_dir, self.current_shot.shot_id)
            if self.current_shot
            else self.project.exports_dir
        )
        os.makedirs(exp_dir, exist_ok=True)
        open_path_in_os(exp_dir)

    # =========================================================================
    # DJV PLAYBACK & SEND TO CLIENT
    # =========================================================================

    def _on_play_latest_from_shots(self):
        selected_shots = self.shot_explorer.selected_shots()
        if not selected_shots or not self.project:
            return

        found, _ = collect_latest_shot_media(self.project, self.repo, selected_shots)
        paths = [m["first_file_path"] for _, m in found if m.get("first_file_path")]
        if not paths:
            QMessageBox.information(self, "No Playable Media", "Selected shot(s) have no playable renders or plates yet.")
            return

        djv = self.tool_registry.djv
        if not djv:
            open_path_in_os(paths[0])
            return

        ok, msg = djv.play_media_list(
            paths,
            speed=self.project.fps,
            ocio_config=self.project.effective_ocio_config,
        )
        if not ok:
            QMessageBox.warning(self, "DJV Playback Error", msg)
        else:
            self.statusBar().showMessage(msg)

    def _open_qt_export_from_shots(self, mode: str = "qt_review"):
        slate_tool = self.tool_registry.slate_delivery
        if not slate_tool:
            QMessageBox.warning(self, "Module Disabled", "Slate & Delivery module is disabled in Settings.")
            return
        if not self.project:
            return

        found, missing_shots = collect_latest_shot_media(self.project, self.repo, self.shot_explorer.selected_shots())
        clips: List[Dict[str, Any]] = []
        for sid, media_rec in found:
            c_info = build_qt_clip_info(self.project, slate_tool, sid, media_rec)
            if c_info:
                clips.append(c_info)
            else:
                missing_shots.append(sid)

        if not clips:
            reply = QMessageBox.question(
                self,
                "No Shot Renders Found",
                f"Selected shot(s) ({', '.join(missing_shots)}) have no plates or renders yet.\n\n"
                "Open Send to Client anyway and browse external files?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        open_send_to_client_dialog(self, self.project, slate_tool, clips, initial_mode=mode, repo=self.repo)

    def _open_qt_export_from_media(self, mode: str, selected_media: List[Any]):
        slate_tool = self.tool_registry.slate_delivery
        if not slate_tool:
            QMessageBox.warning(self, "Module Disabled", "Slate & Delivery module is disabled in Settings.")
            return

        fallback_shot_id = self.current_shot.shot_id if self.current_shot else "SHOT_0010"
        clips = [
            c_info
            for m in selected_media
            if (
                c_info := build_qt_clip_info(
                    self.project, slate_tool, m.get("shot_id") or fallback_shot_id, m
                )
            )
            is not None
        ]
        if not clips:
            QMessageBox.information(self, "Select Playable Media", "Select one or more render or plate versions.")
            return

        open_send_to_client_dialog(self, self.project, slate_tool, clips, initial_mode=mode, repo=self.repo)

    # =========================================================================
    # MEDIA INGESTION, SETTINGS & CLOUD SYNC
    # =========================================================================

    def _on_open_media_ingester(self, new_shot_mode: bool = False, target_shot_id: str = ""):
        if not self.project:
            return
        ingester = self.tool_registry.ingester
        if not ingester:
            QMessageBox.warning(self, "Module Disabled", "Media Ingester module is disabled in Settings.")
            return

        shot_ids = [s.shot_id for s in self.project.all_shots()]
        default_sid = (
            target_shot_id
            if target_shot_id and target_shot_id in shot_ids
            else (self.current_shot.shot_id if self.current_shot else (shot_ids[0] if shot_ids else "R01_0010"))
        )

        dlg = MediaIngestDialog(
            self,
            self.project,
            self.repo,
            ingester,
            shot_ids,
            default_shot_id=default_sid,
            new_shot_mode=new_shot_mode,
        )
        dlg.setWindowModality(Qt.NonModal)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.widget.ingest_completed.connect(self._on_media_ingest_completed)

        open_dialogs = getattr(self, "_open_tool_dialogs", None)
        if open_dialogs is None:
            open_dialogs = []
            self._open_tool_dialogs = open_dialogs
        open_dialogs.append(dlg)
        dlg.destroyed.connect(lambda _=None, d=dlg: open_dialogs.remove(d) if d in open_dialogs else None)

        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_media_ingest_completed(self, ing_sid: str, _target_dir: str = ""):
        if not self.project:
            return
        if ing_sid:
            self.repo.invalidate_media_cache(shot_id=ing_sid, project=self.project)
            sh_obj = self.project.get_shot(ing_sid)
            if sh_obj:
                if not sh_obj.name:
                    sh_obj.name = f"{ing_sid}_plate"
                if sh_obj.status == "Pending":
                    sh_obj.status = "WIP"
                self.current_shot = sh_obj
            self.save_shots_session(show_toast=False, write_excel=False)
        self.refresh_all()

    def _on_open_project_settings(self, initial_page: str = ""):
        if not self.project:
            QMessageBox.information(self, "No Project Loaded", "Open or initialize a project first.")
            return
        dlg = ProjectSettingsDialog(self, self.project, initial_page=initial_page)
        if dlg.exec_() == QDialog.Accepted:
            self.setWindowTitle(f"Project Manager — {self.project.name}")
            self.save_shots_session(show_toast=False)
            self.refresh_all()
            self._update_sync_label()
            self.statusBar().showMessage("Updated project settings.")

    def _on_open_app_settings(self):
        dlg = AppSettingsDialog(self, self.tool_registry)
        if dlg.exec_() == QDialog.Accepted:
            qss = get_active_stylesheet()
            self.setStyleSheet(qss)
            if self._dcc_launcher_win is not None:
                self._dcc_launcher_win.setStyleSheet(qss)
            self._rebuild_dynamic_module_ui()
            self.project_bar.reload_from_settings()
            self.statusBar().showMessage("Updated app settings.")

    def _schedule_json_autosave(self):
        """Debounced auto-save (300ms): writes project.json and queues Excel export if Auto-Save Excel is ON."""
        if not self.project:
            return
        self._json_dirty = True
        self._autosave_timer.start()

    def _flush_json_autosave(self):
        if self._autosave_timer.isActive():
            self._autosave_timer.stop()
        if self._json_dirty and self.project:
            self._json_dirty = False
            now_ts = save_project_session(self.project, self.repo, write_excel=False)
            if is_auto_save_excel_enabled():
                self._excel_worker.enqueue_export(self.project, now_ts)

    def save_shots_session(self, show_toast: bool = False, write_excel: Optional[bool] = None):
        if not self.project:
            return
        if self._autosave_timer.isActive():
            self._autosave_timer.stop()
        self._json_dirty = False
        do_excel = is_auto_save_excel_enabled() if write_excel is None else bool(write_excel)
        now_ts = save_project_session(self.project, self.repo, write_excel=False)
        if do_excel:
            self._excel_worker.enqueue_export(self.project, now_ts)
            if show_toast:
                self.statusBar().showMessage(f"Saved project.json — exporting Excel ({len(self.project.all_shots())} shots)...")
        elif show_toast:
            self.statusBar().showMessage(f"Saved {len(self.project.all_shots())} shots ({now_ts})")

    def _on_excel_export_finished(self, ok: bool, timestamp: str, shot_count: int) -> None:
        if ok:
            self.statusBar().showMessage(f"Saved {shot_count} shots & Excel ({timestamp})")
        else:
            self.statusBar().showMessage("Saved project.json (Excel export failed).")

    def _update_sync_label(self):
        if not self.project or not self.project.google_sheet_url:
            self.sync_status_lbl.setText("")
        elif self.project.last_sync_time:
            self.sync_status_lbl.setText(f"Synced: {self.project.last_sync_time}")
            self.sync_status_lbl.setStyleSheet("color: #4DAB9A; font-size: 11px;")
        else:
            self.sync_status_lbl.setText("Cloud: Ready")
            self.sync_status_lbl.setStyleSheet("color: #9B9A97; font-size: 11px;")

    def _on_sync_google_sheet(self):
        if not self.project:
            return
        if not self.project.google_sheet_url:
            QMessageBox.information(
                self,
                "Configure Google Sheet",
                "Paste your Google Sheet Apps Script URL inside Project Settings -> Cloud Sync.",
            )
            self._on_open_project_settings(initial_page="Cloud Sync")
            return
        self.statusBar().showMessage("Syncing Google Sheet...")
        ok, _ = sync_google_sheet_interactive(self, self.project, self.repo, on_pulled=self.refresh_all)
        if ok:
            self._update_sync_label()
            self.statusBar().showMessage("Google Sheet sync complete.")

    # =========================================================================
    # THUMBNAIL GENERATION & LIVE UPDATES
    # =========================================================================

    def _on_shot_thumbnail_ready(self, shot_id: str, thumb_path: str):
        if not self.project or not shot_id:
            return
        sh = self.project.get_shot(shot_id)
        if sh:
            sh.thumb = thumb_path
        self.shot_explorer.update_shot_thumbnail(shot_id, thumb_path)

    def _on_media_thumbnail_ready(self, shot_id: str, version_dir: str, thumb_path: str):
        if any(s.shot_id == shot_id for s in self.selected_shots):
            self.media_panel.update_media_thumbnail(version_dir, thumb_path)

    def _on_thumb_batch_progress(self, done: int, total: int, label: str):
        self.statusBar().showMessage(f"Generating thumbnails ({done}/{total}): {label}...")

    def _on_thumb_batch_finished(self, generated_count: int):
        if self.project and generated_count > 0:
            self.repo.save(self.project)
        self.statusBar().showMessage(f"Thumbnails ready ({generated_count} generated).")

    def _on_generate_selected_shot_thumbnails(self):
        if not self.project:
            return
        selected = self.shot_explorer.selected_shots()
        if not selected:
            return
        for sh in selected:
            self.thumb_service.queue_shot(self.project, self.repo, sh, force=True)
        self.statusBar().showMessage(f"Generating thumbnail(s) for {len(selected)} shot(s)...")

    def _on_set_custom_shot_thumbnail(self):
        if not self.project or not self.current_shot:
            return
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            f"Set Custom Thumbnail — {self.current_shot.shot_id}",
            self.project.vfx_root,
            "Images & Media (*.png *.jpg *.jpeg *.tif *.tiff *.exr *.dpx *.mov *.mp4);;All Files (*)",
        )
        if not chosen:
            return
        out = set_shot_thumbnail_from_image(self.project, self.current_shot, chosen)
        if out:
            clear_thumbnail_pixmap_cache(out)
            self.shot_explorer.update_shot_thumbnail(self.current_shot.shot_id, out)
            self.save_shots_session(show_toast=False)
            self.statusBar().showMessage(f"Updated thumbnail for {self.current_shot.shot_id}")
        else:
            QMessageBox.warning(self, "Thumbnail Error", f"Could not import thumbnail from:\n{chosen}")

    def _on_generate_selected_media_thumbnails(self, media_list: List[Dict[str, Any]]):
        if not self.project or not media_list:
            return
        for m in media_list:
            self.thumb_service.queue_media(self.project, m, force=True)
        self.statusBar().showMessage(f"Generating thumbnail(s) for {len(media_list)} media item(s)...")

    def _on_set_media_as_shot_thumbnail(self, media_item: Any):
        if not self.project or not media_item:
            return
        media_sid = media_item.get("shot_id", "") if hasattr(media_item, "get") else ""
        target_shot = (self.project.get_shot(media_sid) if media_sid else None) or self.current_shot
        if not target_shot:
            return
        self.thumb_service.queue_shot(
            self.project, self.repo, target_shot, force=True, media_dict=media_item
        )
        self.statusBar().showMessage(
            f"Setting {media_item.get('identifier')} {media_item.get('version')} as {target_shot.shot_id} thumbnail..."
        )

    def _on_generate_project_thumbnails(self, force: bool = False):
        if not self.project:
            return
        self.thumb_service.queue_project_batch(self.project, self.repo, force=force)
        verb = "Regenerating all" if force else "Generating missing"
        self.statusBar().showMessage(f"{verb} thumbnails for {self.project.name}...")

    def _start_background_update_check(self) -> None:
        if not load_user_settings().get("check_updates_on_startup", True):
            return
        if not get_github_repo():
            return
        if self._update_check_worker and self._update_check_worker.isRunning():
            return
        self._update_check_worker = CheckUpdateWorker(self)
        self._update_check_worker.finished_info.connect(self._on_background_update_checked)
        self._update_check_worker.start()

    def _on_background_update_checked(self, info) -> None:
        self._latest_update_info = info
        if info and info.available and not info.error:
            ver_tag = info.tag_name or f"v{info.latest_version}"
            self.update_btn.setText(f"Update {ver_tag}")
            self.update_btn.setObjectName("accent_btn")
            self.update_btn.setToolTip(f"New release {ver_tag} available on GitHub — click to install")
            self.update_btn.style().unpolish(self.update_btn)
            self.update_btn.style().polish(self.update_btn)
            self.statusBar().showMessage(f"Update available: {ver_tag} (click 'Update {ver_tag}' to install)", 8000)

    def _on_check_for_updates(self) -> None:
        cached = self._latest_update_info if (self._latest_update_info and self._latest_update_info.available) else None
        relaunch_args = [self.project.vfx_root] if (self.project and self.project.vfx_root) else []
        dlg = UpdateDialog(
            self,
            initial_info=cached,
            entry_script="project_manager.py",
            relaunch_args=relaunch_args,
        )
        dlg.exec_()

    def closeEvent(self, event):
        for dlg in list(getattr(self, "_open_tool_dialogs", []) or []):
            try:
                if dlg and not dlg.close():
                    event.ignore()
                    return
            except RuntimeError:
                pass
        self._flush_json_autosave()
        self._selection_timer.stop()
        self._media_scan_worker.request_stop()
        self._excel_worker.request_stop()
        self.thumb_service.shutdown()
        if self._update_check_worker and self._update_check_worker.isRunning():
            self._update_check_worker.wait(1000)
        if self._media_scan_worker.isRunning():
            self._media_scan_worker.wait(1500)
        if self._excel_worker.isRunning():
            self._excel_worker.wait(3000)
        super().closeEvent(event)
