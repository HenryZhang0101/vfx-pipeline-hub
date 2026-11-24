"""
DCC Launcher Window — Minimal Artist Scene Creator & Launcher App.
"""

import os
from typing import Any, Dict, Optional

from qtpy.QtCore import Qt
from qtpy.QtGui import QFont
from qtpy.QtWidgets import (
    QAbstractItemView,
    QAction,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_SHOT_DEPARTMENTS, load_user_settings, open_path_in_os
from core.models import Project, Shot
from core.storage import ProjectRepository
from core.timecode import format_shot_id, split_shot_id
from dccs import DCCRegistry
from tools import ToolRegistry
from ui.controllers import (
    build_shot_dcc_context,
    launch_dcc_scene,
    register_recent_project,
    toggle_always_on_top,
    version_up_dcc_scene,
)
from ui.dialogs import AppSettingsDialog, DCCSettingsDialog
from ui.theme import get_active_stylesheet, get_app_icon
from ui.widgets import ProjectSelectorBar


class DCCLauncherWindow(QMainWindow):
    """Minimal Notion-Dark DCC Scene Creator & Launcher."""

    def __init__(self, initial_project_root: str = "", initial_shot_id: str = ""):
        super().__init__()
        self.dcc_registry = DCCRegistry()
        self.tool_registry = ToolRegistry()
        self.repo = ProjectRepository()
        self.project: Optional[Project] = None
        self.current_shot: Optional[Shot] = None
        self._selected_dcc_plugin = None
        self._dcc_buttons: Dict[str, QPushButton] = {}

        self.setWindowTitle("DCC Launcher")
        self.setWindowIcon(get_app_icon())
        self.resize(520, 580)
        self.setStyleSheet(get_active_stylesheet())

        self._build_menu_bar()
        self._build_ui()
        self._rebuild_dcc_selector()

        first_root = self.project_bar.populate(initial_project_root)
        if first_root:
            self.load_project(first_root, initial_shot_id=initial_shot_id)

    # =========================================================================
    # MENU BAR & UI CONSTRUCTION
    # =========================================================================

    def _build_menu_bar(self):
        mb = self.menuBar()

        m_proj = mb.addMenu("Project")
        m_proj.setMinimumWidth(240)
        self._act_open_proj = QAction("Open Project...", self)
        m_proj.addAction(self._act_open_proj)

        act_ref = QAction("Refresh", self)
        act_ref.setShortcut("F5")
        act_ref.triggered.connect(self.refresh_all)
        m_proj.addAction(act_ref)

        m_set = mb.addMenu("Settings")
        m_set.setMinimumWidth(240)
        act_dcc_set = QAction("DCC Settings...", self)
        act_dcc_set.setShortcut("Ctrl+,")
        act_dcc_set.triggered.connect(self._on_open_dcc_settings)
        m_set.addAction(act_dcc_set)

        act_app_set = QAction("App Settings...", self)
        act_app_set.triggered.connect(self._on_open_app_settings)
        m_set.addAction(act_app_set)

        m_set.addSeparator()
        self.aot_action = QAction("Always on Top", self)
        self.aot_action.setCheckable(True)
        self.aot_action.toggled.connect(lambda c: toggle_always_on_top(self, c))
        m_set.addAction(self.aot_action)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        # 1. Context Selector Card (Project + Shot)
        ctx_card = QFrame()
        ctx_card.setObjectName("card_frame")
        ctx_grid = QGridLayout(ctx_card)
        ctx_grid.setContentsMargins(10, 8, 10, 8)
        ctx_grid.setSpacing(8)

        ctx_grid.addWidget(QLabel("Project:"), 0, 0)
        self.project_bar = ProjectSelectorBar(self, show_open=True, show_refresh=True)
        self.project_bar.project_selected.connect(self.load_project)
        self.project_bar.refresh_requested.connect(self.refresh_all)
        self._act_open_proj.triggered.connect(self.project_bar._on_browse)
        ctx_grid.addWidget(self.project_bar, 0, 1)

        ctx_grid.addWidget(QLabel("Shot:"), 1, 0)
        self.shot_combo = QComboBox()
        self.shot_combo.currentIndexChanged.connect(self._on_shot_combo_changed)
        ctx_grid.addWidget(self.shot_combo, 1, 1)

        self.shot_meta_lbl = QLabel("")
        self.shot_meta_lbl.setStyleSheet("color: #9B9A97; font-size: 11px;")
        ctx_grid.addWidget(self.shot_meta_lbl, 2, 1)

        root.addWidget(ctx_card)

        # 2. Create Scene File Card
        create_card = QFrame()
        create_card.setObjectName("card_frame")
        c_layout = QVBoxLayout(create_card)
        c_layout.setContentsMargins(10, 10, 10, 10)
        c_layout.setSpacing(8)

        self.dcc_btn_row = QHBoxLayout()
        self.dcc_btn_row.setSpacing(6)
        c_layout.addLayout(self.dcc_btn_row)

        form = QGridLayout()
        form.setSpacing(6)

        form.addWidget(QLabel("Department:"), 0, 0)
        self.dept_combo = QComboBox()
        for d in DEFAULT_SHOT_DEPARTMENTS:
            self.dept_combo.addItem(f"{d['name']} ({d['abbr']})", d["name"])
        self.dept_combo.currentIndexChanged.connect(self._on_dept_changed)
        form.addWidget(self.dept_combo, 0, 1)

        form.addWidget(QLabel("Task:"), 1, 0)
        self.task_edit = QLineEdit("comp")
        self.task_edit.textChanged.connect(self._update_version_preview)
        form.addWidget(self.task_edit, 1, 1)

        form.addWidget(QLabel("Version:"), 2, 0)
        self.ver_edit = QLineEdit("v0001")
        form.addWidget(self.ver_edit, 2, 1)

        form.addWidget(QLabel("Comment:"), 3, 0)
        self.comment_edit = QLineEdit()
        form.addWidget(self.comment_edit, 3, 1)

        c_layout.addLayout(form)

        user_cfg = load_user_settings()
        self.bootstrap_chk = QCheckBox("Auto-Bootstrap Scene")
        self.bootstrap_chk.setChecked(bool(user_cfg.get("dcc_default_bootstrap", True)))
        c_layout.addWidget(self.bootstrap_chk)

        self.launch_chk = QCheckBox("Launch immediately")
        self.launch_chk.setChecked(bool(user_cfg.get("dcc_default_launch", True)))
        c_layout.addWidget(self.launch_chk)

        self.create_btn = QPushButton("Create Scene File")
        self.create_btn.setObjectName("primary_btn")
        self.create_btn.clicked.connect(self._on_create_scene)
        c_layout.addWidget(self.create_btn)

        root.addWidget(create_card)

        # 3. Compact Existing Scenes Table
        self.scene_table = QTableWidget(0, 5)
        self.scene_table.setHorizontalHeaderLabels(["Ver", "DCC", "Dept", "Scene File", "Modified"])
        self.scene_table.verticalHeader().setVisible(False)
        self.scene_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.scene_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.scene_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.scene_table.setAlternatingRowColors(True)
        self.scene_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.scene_table.customContextMenuRequested.connect(self._on_scene_context_menu)
        self.scene_table.itemDoubleClicked.connect(lambda _: self._on_launch_selected_scene())

        hdr = self.scene_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        root.addWidget(self.scene_table, 1)

        self.statusBar().showMessage("Ready.")

    # =========================================================================
    # DCC SELECTOR & FORM HELPERS
    # =========================================================================

    def _rebuild_dcc_selector(self):
        while self.dcc_btn_row.count():
            item = self.dcc_btn_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._dcc_buttons.clear()

        enabled = self.dcc_registry.enabled_plugins()
        if not enabled:
            self._selected_dcc_plugin = None
            self.create_btn.setEnabled(False)
            return

        self.create_btn.setEnabled(True)
        for p in enabled:
            btn = QPushButton(f"{p.display_name} ({p.primary_extension})")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked=False, plug=p: self._select_dcc(plug))
            self.dcc_btn_row.addWidget(btn)
            self._dcc_buttons[p.dcc_id] = btn

        target = self._selected_dcc_plugin if (self._selected_dcc_plugin in enabled) else enabled[0]
        self._select_dcc(target)

    def _select_dcc(self, plugin):
        self._selected_dcc_plugin = plugin
        for did, btn in self._dcc_buttons.items():
            is_sel = plugin is not None and did == plugin.dcc_id
            btn.setChecked(is_sel)
            btn.setObjectName("primary_btn" if is_sel else "")

        self.setStyleSheet(get_active_stylesheet())
        if plugin:
            idx = self.dept_combo.findData(plugin.default_department)
            if idx < 0:
                for i, d in enumerate(DEFAULT_SHOT_DEPARTMENTS):
                    if d["abbr"] == plugin.default_department:
                        idx = i
                        break
            if idx >= 0:
                self.dept_combo.setCurrentIndex(idx)
            self.task_edit.setText(plugin.default_task)
            self.create_btn.setText(f"Create {plugin.display_name} Scene ({plugin.primary_extension})")
        self._update_version_preview()

    def _on_dept_changed(self):
        dept_name = self.dept_combo.currentData() or "Compositing"
        for d in DEFAULT_SHOT_DEPARTMENTS:
            if d["name"] == dept_name or d["abbr"] == dept_name:
                self.task_edit.setText(d["default_task"])
                break
        self._update_version_preview()

    def _update_version_preview(self):
        if not self.project or not self.current_shot:
            return
        dept = self.dept_combo.currentData() or "Compositing"
        next_v = self.repo.get_next_scene_version(self.project, self.current_shot.shot_id, dept)
        self.ver_edit.setText(next_v)

    # =========================================================================
    # PROJECT & SHOT LOADING
    # =========================================================================

    def load_project(self, root_dir: str, initial_shot_id: str = ""):
        if not root_dir:
            return
        vfx_root = self.repo.resolve_vfx_root(root_dir)
        self.project = self.repo.load(vfx_root)
        self.repo.initialize_structure(self.project)
        register_recent_project(self.project.vfx_root)
        self.project_bar.select_or_insert(self.project.vfx_root)
        self.setWindowTitle(f"DCC Launcher — {self.project.name} ({self.project.vfx_root})")
        self.refresh_all(preserve_shot_id=initial_shot_id)

    def select_shot(self, shot_id: str) -> None:
        """Select a specific Shot ID in the shot combo box if present."""
        if not shot_id:
            return
        for i in range(self.shot_combo.count()):
            s: Optional[Shot] = self.shot_combo.itemData(i)
            if s and s.shot_id == shot_id:
                self.shot_combo.setCurrentIndex(i)
                return

    def refresh_all(self, preserve_shot_id: str = ""):
        if not self.project:
            return

        for seq, shot in self.repo.discover_disk_shots(self.project):
            sid = format_shot_id(seq, shot)
            if not self.project.get_shot(sid):
                self.project.add_shot(Shot(shot_id=sid, name=f"{sid}_plate", dur="65 (2.71s)", status="Pending"))

        prev_sid = preserve_shot_id or (self.current_shot.shot_id if self.current_shot else "")
        shots = self.project.all_shots()

        self.shot_combo.blockSignals(True)
        self.shot_combo.clear()
        sel_idx = 0
        for i, s in enumerate(shots):
            label = f"{s.shot_id}  ({s.name})" if s.name else s.shot_id
            self.shot_combo.addItem(label, s)
            if s.shot_id == prev_sid:
                sel_idx = i
        self.shot_combo.blockSignals(False)

        if shots:
            self.shot_combo.setCurrentIndex(sel_idx)
            self._on_shot_combo_changed(sel_idx)
        else:
            self.current_shot = None
            self.shot_meta_lbl.setText("")
            self.scene_table.setRowCount(0)

    def _on_shot_combo_changed(self, idx: int):
        if idx < 0 or not self.project:
            self.current_shot = None
            return
        sh: Optional[Shot] = self.shot_combo.currentData()
        self.current_shot = sh
        if sh:
            eff_res = sh.resolution_str or self.project.resolution_str
            eff_fps = sh.fps if sh.fps > 0 else self.project.fps
            self.shot_meta_lbl.setText(f"{sh.start_frame}–{sh.end_frame}  |  {eff_fps:g} fps  |  {eff_res}")
        self._update_version_preview()
        self.refresh_scenes()

    # =========================================================================
    # SCENE CREATION, LAUNCH & VERSION UP
    # =========================================================================

    def _on_create_scene(self):
        if not self.project or not self.current_shot:
            QMessageBox.information(self, "No Shot Selected", "Select a project and shot first.")
            return
        plugin = self._selected_dcc_plugin
        if not plugin:
            QMessageBox.warning(self, "No DCC Selected", "Enable a DCC module in Settings.")
            return

        dept = self.dept_combo.currentData() or plugin.default_department
        task = self.task_edit.text().strip() or plugin.default_task
        ver = self.ver_edit.text().strip() or "v0001"
        if not ver.startswith("v"):
            ver = f"v{ver}"

        seq, shot = split_shot_id(self.current_shot.shot_id)
        self.repo.ensure_shot(self.project, self.current_shot.shot_id)
        target_path = self.repo.build_scene_filepath(self.project, self.current_shot.shot_id, dept, ver, plugin.primary_extension)
        if os.path.exists(target_path):
            QMessageBox.warning(self, "File Exists", f"Scene already exists:\n{target_path}")
            return

        ctx = build_shot_dcc_context(
            self.project,
            self.repo,
            self.current_shot,
            seq,
            shot,
            extra={
                "department": dept,
                "task": task,
                "version": ver,
                "auto_bootstrap": self.bootstrap_chk.isChecked(),
            },
        )
        ok, msg = plugin.create_scene(target_path, shot_context=ctx)
        if not ok:
            QMessageBox.warning(self, "Creation Failed", msg)
            return

        settings = load_user_settings()
        self.repo.write_scene_metadata(
            target_path,
            comment=self.comment_edit.text().strip() or f"Created {plugin.display_name} scene",
            artist=settings.get("artist_name", ""),
        )
        self.comment_edit.clear()
        self.refresh_scenes()
        self._update_version_preview()
        self.statusBar().showMessage(f"Created {os.path.basename(target_path)}")

        if self.launch_chk.isChecked():
            l_ok, l_msg = plugin.launch_scene(target_path, shot_context=ctx)
            if not l_ok:
                QMessageBox.warning(self, "DCC Launch Warning", l_msg)
            else:
                self.statusBar().showMessage(l_msg)

    def refresh_scenes(self):
        self.scene_table.setRowCount(0)
        if not self.project or not self.current_shot:
            return
        scenes = self.repo.scan_shot_scenefiles(self.project, self.current_shot.shot_id, department_filter="all")
        for r_idx, sc in enumerate(scenes):
            self.scene_table.insertRow(r_idx)
            v_item = QTableWidgetItem(sc["version"])
            v_item.setFont(QFont("Segoe UI", 10, QFont.Bold))
            v_item.setData(Qt.UserRole, sc)
            self.scene_table.setItem(r_idx, 0, v_item)

            plugin = self.dcc_registry.get_plugin(sc["dcc_id"])
            self.scene_table.setItem(r_idx, 1, QTableWidgetItem(plugin.display_name if plugin else sc["dcc_id"].upper()))
            self.scene_table.setItem(r_idx, 2, QTableWidgetItem(sc["department"]))
            self.scene_table.setItem(r_idx, 3, QTableWidgetItem(sc["filename"]))
            self.scene_table.setItem(r_idx, 4, QTableWidgetItem(sc["mtime_str"]))

        if scenes:
            self.scene_table.selectRow(0)

    def _get_selected_scene_dict(self) -> Optional[Dict[str, Any]]:
        row = self.scene_table.currentRow()
        if row < 0:
            return None
        v_item = self.scene_table.item(row, 0)
        return v_item.data(Qt.UserRole) if v_item else None

    def _on_launch_selected_scene(self):
        sc = self._get_selected_scene_dict()
        if not sc:
            return
        ok, msg = launch_dcc_scene(self.dcc_registry, self.project, self.repo, self.current_shot, sc)
        if not ok:
            QMessageBox.warning(self, "Launch Failed", msg)
        else:
            self.statusBar().showMessage(msg)

    def _on_version_up_scene(self):
        sc = self._get_selected_scene_dict()
        if not sc or not self.project:
            return
        ok, msg, _ = version_up_dcc_scene(self.dcc_registry, self.project, self.repo, self.current_shot, sc)
        if ok:
            self.refresh_scenes()
            self._update_version_preview()
            self.statusBar().showMessage(msg)
        else:
            QMessageBox.warning(self, "Version Up Failed", msg)

    def _on_reveal_selected_scene(self):
        sc = self._get_selected_scene_dict()
        if not sc:
            return
        folder = os.path.dirname(sc["filepath"])
        if os.path.isdir(folder):
            open_path_in_os(folder)

    def _on_scene_context_menu(self, pos):
        sc = self._get_selected_scene_dict()
        if not sc:
            return
        menu = QMenu(self)
        menu.setStyleSheet(get_active_stylesheet())
        act_open = menu.addAction(f"Launch {sc['filename']}")
        act_open.triggered.connect(self._on_launch_selected_scene)
        act_vup = menu.addAction("Version Up (+v####)")
        act_vup.triggered.connect(self._on_version_up_scene)
        menu.addSeparator()
        act_dir = menu.addAction("Open Scene Folder")
        act_dir.triggered.connect(self._on_reveal_selected_scene)
        menu.exec_(self.scene_table.viewport().mapToGlobal(pos))

    def _on_open_dcc_settings(self):
        dlg = DCCSettingsDialog(self, self.dcc_registry)
        if dlg.exec_() == QDialog.Accepted:
            user_cfg = load_user_settings()
            self.bootstrap_chk.setChecked(bool(user_cfg.get("dcc_default_bootstrap", True)))
            self.launch_chk.setChecked(bool(user_cfg.get("dcc_default_launch", True)))
            self._rebuild_dcc_selector()
            self.refresh_scenes()
            self.statusBar().showMessage("Updated DCC settings.")

    def _on_open_app_settings(self):
        dlg = AppSettingsDialog(self, self.tool_registry)
        if dlg.exec_() == QDialog.Accepted:
            self.setStyleSheet(get_active_stylesheet())
            self.project_bar.reload_from_settings()
            self.statusBar().showMessage("Updated app settings.")
