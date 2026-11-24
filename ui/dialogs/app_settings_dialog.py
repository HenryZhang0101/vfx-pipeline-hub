"""Global App Settings dialog (Appearance & Identity, Global Paths, Pipeline Tools)."""

import json
import os
import sys
from typing import Dict, List, Optional, Tuple

from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from config import (
    DEFAULT_OCIO_CONFIG,
    DEFAULT_SLATE_DISCLAIMER,
    FFMPEG_BIN,
    FFPROBE_BIN,
    GITHUB_REPO,
    PIPELINE_ASSETS_DIR,
    PIPELINE_OCIO_DIR,
    PIPELINE_ROOT,
    get_default_artist_name,
    load_user_settings,
    open_path_in_os,
    save_user_settings,
)
from core.models.project import infer_project_name_from_path
from core.storage import ProjectRepository
from tools import ToolRegistry
from ui.controllers import get_recorded_recent_projects, set_recent_projects
from ui.dialogs.settings_base import SidebarSettingsDialog
from ui.theme import ACCENT_PRESETS, DEFAULT_ACCENT_COLOR, build_notion_dark_qss


class AppSettingsDialog(SidebarSettingsDialog):
    """Global workstation settings persisted in user_settings.json."""

    def __init__(self, parent, tool_registry: Optional[ToolRegistry] = None, initial_page: str = ""):
        super().__init__(
            parent=parent,
            title="App Settings",
            save_label="Save App Settings",
            width=760,
            height=480,
        )
        self.tool_registry = tool_registry or ToolRegistry()
        self._settings = load_user_settings()
        self.accent_color = self._settings.get("accent_color", DEFAULT_ACCENT_COLOR)
        self._tool_widgets: Dict[str, Tuple[QCheckBox, Optional[QLineEdit]]] = {}

        self._build_appearance_page()
        self._build_recent_projects_page()
        self._build_paths_page()
        self._build_tools_page()

        if initial_page:
            self.select_page(initial_page)

    # ------------------------------------------------------------------
    # Page 1: Appearance & Identity
    # ------------------------------------------------------------------
    def _build_appearance_page(self) -> None:
        _, lay = self.add_page(
            "Appearance & Identity",
            "Personal artist display name and global Notion Dark accent color across all pipeline apps.",
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        grid.addWidget(QLabel("Artist Name:"), 0, 0)
        self.artist_edit = QLineEdit(self._settings.get("artist_name", get_default_artist_name()))
        self.artist_edit.setPlaceholderText("Your name or studio handle...")
        grid.addWidget(self.artist_edit, 0, 1)

        grid.addWidget(QLabel("Accent Color:"), 1, 0)
        accent_row = QHBoxLayout()
        accent_row.setSpacing(6)

        self.accent_preview_lbl = QLabel(self.accent_color)
        self.accent_preview_lbl.setStyleSheet(
            f"padding: 3px 10px; border-radius: 4px; background-color: {self.accent_color}; "
            f"color: #ffffff; font-weight: 600;"
        )
        accent_row.addWidget(self.accent_preview_lbl)

        for preset_name, preset_hex in ACCENT_PRESETS:
            swatch_btn = QPushButton("")
            swatch_btn.setFixedSize(22, 22)
            swatch_btn.setToolTip(f"{preset_name} ({preset_hex})")
            swatch_btn.setStyleSheet(
                f"background-color: {preset_hex}; border: 1px solid #3E3E3E; border-radius: 4px; padding: 0;"
            )
            swatch_btn.clicked.connect(lambda _=False, c=preset_hex: self._set_accent_color(c))
            accent_row.addWidget(swatch_btn)

        custom_btn = QPushButton("Custom...")
        custom_btn.clicked.connect(self._pick_custom_accent)
        accent_row.addWidget(custom_btn)
        accent_row.addStretch()
        grid.addLayout(accent_row, 1, 1)

        # Slate & Studio Branding
        branding_hdr = QLabel("Slate & Studio Branding")
        branding_hdr.setStyleSheet("font-weight: 600; color: #E3E2E0; margin-top: 10px;")
        grid.addWidget(branding_hdr, 2, 0, 1, 2)

        grid.addWidget(QLabel("Studio Name:"), 3, 0)
        self.studio_name_edit = QLineEdit(self._settings.get("studio_name", ""))
        self.studio_name_edit.setPlaceholderText("Optional text shown on slate if no logo PNG is set")
        grid.addWidget(self.studio_name_edit, 3, 1)

        grid.addWidget(QLabel("Slate Logo (PNG):"), 4, 0)
        logo_row = QHBoxLayout()
        logo_row.setSpacing(6)
        self.slate_logo_edit = QLineEdit(self._settings.get("slate_logo_path", ""))
        self.slate_logo_edit.setPlaceholderText("Blank = neutral slate (or assets/slate_logo.png)")
        logo_row.addWidget(self.slate_logo_edit, 1)
        browse_logo_btn = QPushButton("Browse...")
        browse_logo_btn.clicked.connect(self._browse_slate_logo)
        logo_row.addWidget(browse_logo_btn)
        clear_logo_btn = QPushButton("Reset")
        clear_logo_btn.clicked.connect(lambda: self.slate_logo_edit.clear())
        logo_row.addWidget(clear_logo_btn)
        grid.addLayout(logo_row, 4, 1)

        grid.addWidget(QLabel("Slate Footer:"), 5, 0)
        self.slate_disclaimer_edit = QLineEdit(
            self._settings.get("slate_disclaimer", DEFAULT_SLATE_DISCLAIMER)
        )
        self.slate_disclaimer_edit.setPlaceholderText(DEFAULT_SLATE_DISCLAIMER)
        grid.addWidget(self.slate_disclaimer_edit, 5, 1)

        lay.addLayout(grid)
        lay.addStretch()

    # ------------------------------------------------------------------
    # Page 2: Recent Projects
    # ------------------------------------------------------------------
    @staticmethod
    def _format_recent_project_label(vfx_root: str) -> str:
        norm = os.path.normpath(vfx_root)
        exists = os.path.isdir(norm)
        proj_name = ""
        if exists:
            proj_json = os.path.join(norm, "project.json")
            if os.path.isfile(proj_json):
                try:
                    with open(proj_json, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        proj_name = (data.get("name") or "").strip()
                except Exception:
                    pass
        if not proj_name:
            proj_name = infer_project_name_from_path(norm)
        suffix = "" if exists else "  [missing]"
        return f"{proj_name}  —  {norm}{suffix}"

    def _build_recent_projects_page(self) -> None:
        _, lay = self.add_page(
            "Recent Projects",
            "Manage recorded VFX project directories shown in the project dropdown across all pipeline apps.",
        )

        self.recent_projects_list = QListWidget()
        self.recent_projects_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for path in get_recorded_recent_projects():
            self._append_recent_project_item(path)
        lay.addWidget(self.recent_projects_list, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        add_btn = QPushButton("Add VFX Folder...")
        add_btn.clicked.connect(self._on_add_recent_project)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove Selected")
        remove_btn.setObjectName("warn_btn")
        remove_btn.clicked.connect(self._on_remove_selected_recent_projects)
        btn_row.addWidget(remove_btn)

        clear_missing_btn = QPushButton("Clear Missing")
        clear_missing_btn.clicked.connect(self._on_clear_missing_recent_projects)
        btn_row.addWidget(clear_missing_btn)

        btn_row.addStretch(1)
        lay.addLayout(btn_row)

    def _append_recent_project_item(self, vfx_root: str) -> None:
        norm = os.path.normpath(vfx_root)
        item = QListWidgetItem(self._format_recent_project_label(norm))
        item.setData(Qt.UserRole, norm)
        item.setToolTip(norm)
        if not os.path.isdir(norm):
            item.setForeground(QColor("#9B9A97"))
        self.recent_projects_list.addItem(item)

    def _on_add_recent_project(self) -> None:
        default_root = (self._settings.get("default_projects_root") or "").strip()
        start = (
            default_root
            if default_root and os.path.isdir(default_root)
            else os.path.expanduser("~")
        )
        chosen = QFileDialog.getExistingDirectory(self, "Add VFX Project Directory", start)
        if not chosen:
            return
        resolved = ProjectRepository.resolve_vfx_root(chosen)
        target_key = os.path.normcase(os.path.normpath(resolved))
        for i in range(self.recent_projects_list.count()):
            existing = self.recent_projects_list.item(i).data(Qt.UserRole) or ""
            if os.path.normcase(os.path.normpath(existing)) == target_key:
                self.recent_projects_list.setCurrentRow(i)
                return
        self._append_recent_project_item(resolved)
        self.recent_projects_list.setCurrentRow(self.recent_projects_list.count() - 1)

    def _on_remove_selected_recent_projects(self) -> None:
        selected_rows = sorted(
            {self.recent_projects_list.row(item) for item in self.recent_projects_list.selectedItems()},
            reverse=True,
        )
        for row in selected_rows:
            self.recent_projects_list.takeItem(row)

    def _on_clear_missing_recent_projects(self) -> None:
        for row in range(self.recent_projects_list.count() - 1, -1, -1):
            path = self.recent_projects_list.item(row).data(Qt.UserRole) or ""
            if not path or not os.path.isdir(path):
                self.recent_projects_list.takeItem(row)

    # ------------------------------------------------------------------
    # Page 3: Global Paths
    # ------------------------------------------------------------------
    def _build_paths_page(self) -> None:
        _, lay = self.add_page(
            "Global Paths",
            "Workstation install paths, default project directory, and optional FFmpeg/FFprobe overrides.",
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        # Default Projects Root
        grid.addWidget(QLabel("Default Projects Root:"), 0, 0)
        self.projects_root_edit = QLineEdit(self._settings.get("default_projects_root", ""))
        self.projects_root_edit.setPlaceholderText("e.g. H:\\ or /Volumes/Projects (optional)")
        grid.addWidget(self.projects_root_edit, 0, 1)
        browse_proj_btn = QPushButton("Browse...")
        browse_proj_btn.clicked.connect(self._browse_projects_root)
        grid.addWidget(browse_proj_btn, 0, 2)

        # Pipeline Install Path (read-only)
        grid.addWidget(QLabel("Pipeline Install Path:"), 1, 0)
        self.install_path_edit = QLineEdit(os.path.normpath(PIPELINE_ROOT))
        self.install_path_edit.setReadOnly(True)
        self.install_path_edit.setStyleSheet("color: #9B9A97;")
        grid.addWidget(self.install_path_edit, 1, 1)
        open_install_btn = QPushButton("Open Folder")
        open_install_btn.clicked.connect(lambda: open_path_in_os(PIPELINE_ROOT))
        grid.addWidget(open_install_btn, 1, 2)

        # FFmpeg Override
        grid.addWidget(QLabel("FFmpeg Override:"), 2, 0)
        self.ffmpeg_edit = QLineEdit(self._settings.get("ffmpeg_path", ""))
        self.ffmpeg_edit.setPlaceholderText("Blank = auto-detect bundled bin/ffmpeg or PATH")
        grid.addWidget(self.ffmpeg_edit, 2, 1)
        ff_btns = QHBoxLayout()
        ff_btns.setSpacing(4)
        browse_ff_btn = QPushButton("Browse...")
        browse_ff_btn.clicked.connect(lambda: self._browse_exe(self.ffmpeg_edit, "FFmpeg"))
        ff_btns.addWidget(browse_ff_btn)
        clear_ff_btn = QPushButton("Reset")
        clear_ff_btn.clicked.connect(lambda: self.ffmpeg_edit.clear())
        ff_btns.addWidget(clear_ff_btn)
        grid.addLayout(ff_btns, 2, 2)

        resolved_ff = QLabel(f"Bundled / Auto: {os.path.normpath(FFMPEG_BIN)}")
        resolved_ff.setStyleSheet("color: #9B9A97; font-size: 11px;")
        grid.addWidget(resolved_ff, 3, 1, 1, 2)

        # FFprobe Override
        grid.addWidget(QLabel("FFprobe Override:"), 4, 0)
        self.ffprobe_edit = QLineEdit(self._settings.get("ffprobe_path", ""))
        self.ffprobe_edit.setPlaceholderText("Blank = auto-detect bundled bin/ffprobe or PATH")
        grid.addWidget(self.ffprobe_edit, 4, 1)
        fp_btns = QHBoxLayout()
        fp_btns.setSpacing(4)
        browse_fp_btn = QPushButton("Browse...")
        browse_fp_btn.clicked.connect(lambda: self._browse_exe(self.ffprobe_edit, "FFprobe"))
        fp_btns.addWidget(browse_fp_btn)
        clear_fp_btn = QPushButton("Reset")
        clear_fp_btn.clicked.connect(lambda: self.ffprobe_edit.clear())
        fp_btns.addWidget(clear_fp_btn)
        grid.addLayout(fp_btns, 4, 2)

        resolved_fp = QLabel(f"Bundled / Auto: {os.path.normpath(FFPROBE_BIN)}")
        resolved_fp.setStyleSheet("color: #9B9A97; font-size: 11px;")
        grid.addWidget(resolved_fp, 5, 1, 1, 2)

        # OCIO Config Override
        grid.addWidget(QLabel("OCIO Config Override:"), 6, 0)
        self.ocio_edit = QLineEdit(self._settings.get("ocio_config", ""))
        self.ocio_edit.setPlaceholderText("Blank = use bundled ACES 1.3 Studio OCIO config")
        grid.addWidget(self.ocio_edit, 6, 1)
        ocio_btns = QHBoxLayout()
        ocio_btns.setSpacing(4)
        browse_ocio_btn = QPushButton("Browse...")
        browse_ocio_btn.clicked.connect(self._browse_ocio)
        ocio_btns.addWidget(browse_ocio_btn)
        clear_ocio_btn = QPushButton("Reset")
        clear_ocio_btn.clicked.connect(lambda: self.ocio_edit.clear())
        ocio_btns.addWidget(clear_ocio_btn)
        grid.addLayout(ocio_btns, 6, 2)

        resolved_ocio = QLabel(f"Bundled / Default: {os.path.normpath(DEFAULT_OCIO_CONFIG)}")
        resolved_ocio.setStyleSheet("color: #9B9A97; font-size: 11px;")
        grid.addWidget(resolved_ocio, 7, 1, 1, 2)

        # Software Updates (GitHub Releases)
        grid.addWidget(QLabel("GitHub Updates Repo:"), 8, 0)
        self.github_repo_edit = QLineEdit(self._settings.get("github_repo", ""))
        default_repo_hint = GITHUB_REPO or "owner/repo"
        self.github_repo_edit.setPlaceholderText(f"Blank = default ({default_repo_hint})")
        grid.addWidget(self.github_repo_edit, 8, 1)
        clear_repo_btn = QPushButton("Reset")
        clear_repo_btn.clicked.connect(lambda: self.github_repo_edit.clear())
        grid.addWidget(clear_repo_btn, 8, 2)

        self.auto_check_updates_chk = QCheckBox("Check for updates automatically on startup")
        self.auto_check_updates_chk.setChecked(bool(self._settings.get("check_updates_on_startup", True)))
        grid.addWidget(self.auto_check_updates_chk, 9, 1, 1, 2)

        lay.addLayout(grid)
        lay.addStretch()

    # ------------------------------------------------------------------
    # Page 3: Pipeline Tools
    # ------------------------------------------------------------------
    def _build_tools_page(self) -> None:
        _, lay = self.add_page(
            "Pipeline Tools",
            "Enable or disable modular pipeline utilities and configure their executable paths.",
        )

        for tool in self.tool_registry.all_tools():
            card = QFrame()
            card.setObjectName("card_frame")
            c_lay = QVBoxLayout(card)
            c_lay.setContentsMargins(10, 8, 10, 8)
            c_lay.setSpacing(6)

            top_row = QHBoxLayout()
            chk = QCheckBox(f"{tool.display_name}")
            chk.setChecked(self.tool_registry.is_enabled(tool.tool_id))
            chk.setToolTip(tool.description)
            top_row.addWidget(chk)
            top_row.addStretch()

            cat_lbl = QLabel(tool.category)
            cat_lbl.setStyleSheet("color: #9B9A97; font-size: 11px;")
            top_row.addWidget(cat_lbl)
            c_lay.addLayout(top_row)

            if tool.requires_executable:
                path_row = QHBoxLayout()
                path_row.setSpacing(6)
                exe_edit = QLineEdit(tool.get_executable())
                exe_edit.setPlaceholderText(f"Path to {tool.display_name} executable...")
                path_row.addWidget(exe_edit, 1)
                browse_btn = QPushButton("Browse...")
                browse_btn.clicked.connect(
                    lambda _=False, le=exe_edit, name=tool.display_name: self._browse_exe(le, name)
                )
                path_row.addWidget(browse_btn)
                c_lay.addLayout(path_row)
                self._tool_widgets[tool.tool_id] = (chk, exe_edit)
            else:
                desc_lbl = QLabel(tool.description)
                desc_lbl.setWordWrap(True)
                desc_lbl.setStyleSheet("color: #9B9A97; font-size: 11px;")
                c_lay.addWidget(desc_lbl)
                self._tool_widgets[tool.tool_id] = (chk, None)

            lay.addWidget(card)

        lay.addStretch()

    # ------------------------------------------------------------------
    # Helpers & Persistence
    # ------------------------------------------------------------------
    def _set_accent_color(self, hex_color: str) -> None:
        self.accent_color = hex_color
        self.accent_preview_lbl.setText(hex_color)
        self.accent_preview_lbl.setStyleSheet(
            f"padding: 3px 10px; border-radius: 4px; background-color: {hex_color}; "
            f"color: #ffffff; font-weight: 600;"
        )
        self.setStyleSheet(build_notion_dark_qss(hex_color))

    def _pick_custom_accent(self) -> None:
        chosen = QColorDialog.getColor(QColor(self.accent_color), self, "Choose Accent Color")
        if chosen.isValid():
            self._set_accent_color(chosen.name())

    def _browse_projects_root(self) -> None:
        start = self.projects_root_edit.text().strip() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Select Default Projects Root", start)
        if chosen:
            self.projects_root_edit.setText(os.path.normpath(chosen))

    def _browse_exe(self, line_edit: QLineEdit, title: str) -> None:
        exe_filter = "Executables (*.exe);;All Files (*.*)" if sys.platform == "win32" else "All Files (*)"
        chosen, _ = QFileDialog.getOpenFileName(self, f"Select {title} Executable", line_edit.text(), exe_filter)
        if chosen:
            line_edit.setText(os.path.normpath(chosen))

    def _browse_ocio(self) -> None:
        start = self.ocio_edit.text().strip() or PIPELINE_OCIO_DIR
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Select OCIO Config",
            start,
            "OCIO Configs (*.ocio);;All Files (*.*)",
        )
        if chosen:
            self.ocio_edit.setText(os.path.normpath(chosen))

    def _browse_slate_logo(self) -> None:
        start = self.slate_logo_edit.text().strip() or PIPELINE_ASSETS_DIR
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Select Slate Logo Image",
            start,
            "Images (*.png *.jpg *.jpeg *.webp);;All Files (*.*)",
        )
        if chosen:
            self.slate_logo_edit.setText(os.path.normpath(chosen))

    def save_settings(self) -> bool:
        enabled_tools = []
        tool_paths = {}
        for tool_id, (chk, le) in self._tool_widgets.items():
            if chk.isChecked():
                enabled_tools.append(tool_id)
            if le and le.text().strip():
                tool_paths[tool_id] = os.path.normpath(le.text().strip())

        proj_root = self.projects_root_edit.text().strip()
        ffmpeg_override = self.ffmpeg_edit.text().strip()
        ffprobe_override = self.ffprobe_edit.text().strip()
        ocio_override = self.ocio_edit.text().strip()
        slate_logo = self.slate_logo_edit.text().strip()

        save_user_settings({
            "artist_name": self.artist_edit.text().strip() or get_default_artist_name(),
            "studio_name": self.studio_name_edit.text().strip(),
            "slate_logo_path": os.path.normpath(slate_logo) if slate_logo else "",
            "slate_disclaimer": self.slate_disclaimer_edit.text().strip(),
            "accent_color": self.accent_color,
            "default_projects_root": os.path.normpath(proj_root) if proj_root else "",
            "ffmpeg_path": os.path.normpath(ffmpeg_override) if ffmpeg_override else "",
            "ffprobe_path": os.path.normpath(ffprobe_override) if ffprobe_override else "",
            "ocio_config": os.path.normpath(ocio_override) if ocio_override else "",
            "github_repo": self.github_repo_edit.text().strip(),
            "check_updates_on_startup": self.auto_check_updates_chk.isChecked(),
            "enabled_tools": enabled_tools,
            "tool_paths": tool_paths,
        })
        recent_paths: List[str] = [
            self.recent_projects_list.item(i).data(Qt.UserRole)
            for i in range(self.recent_projects_list.count())
            if self.recent_projects_list.item(i).data(Qt.UserRole)
        ]
        set_recent_projects(recent_paths)
        self.tool_registry.reload_from_settings()
        return True
