"""Reusable Project Selector Bar widget for Project Manager, DCC Launcher, and Resolve UI."""

import json
import os
from typing import Optional

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QPushButton, QWidget

from config import load_user_settings
from core.models.project import infer_project_name_from_path
from core.storage import ProjectRepository
from ui.controllers import get_saved_recent_projects


def _format_project_combo_label(vfx_root: str) -> str:
    norm = os.path.normpath(vfx_root)
    proj_json = os.path.join(norm, "project.json")
    proj_name = ""
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
    return f"{proj_name}  ({norm})"


class ProjectSelectorBar(QWidget):
    """Minimal Notion-Dark project selector (ComboBox + optional Open... + Refresh)."""

    project_selected = Signal(str)
    refresh_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None, show_open: bool = True, show_refresh: bool = True):
        super().__init__(parent)
        self._current_root = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(220)
        self.project_combo.currentIndexChanged.connect(self._on_combo_changed)
        layout.addWidget(self.project_combo, 1)

        if show_open:
            self.open_btn = QPushButton("Open...")
            self.open_btn.clicked.connect(self._on_browse)
            layout.addWidget(self.open_btn)

        if show_refresh:
            self.refresh_btn = QPushButton("Refresh")
            self.refresh_btn.clicked.connect(self.refresh_requested.emit)
            layout.addWidget(self.refresh_btn)

    def populate(self, initial_root: str = "") -> str:
        """Populate recent projects strictly from user_settings.json (no drive auto-scan)."""
        recents = get_saved_recent_projects(initial_root)
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for r in recents:
            if os.path.isdir(r):
                self.project_combo.addItem(_format_project_combo_label(r), r)
        self.project_combo.blockSignals(False)

        if self.project_combo.count() > 0:
            self._current_root = self.project_combo.currentData() or ""
            return self._current_root
        self._current_root = ""
        return ""

    def reload_from_settings(self, preserve_current: bool = True) -> None:
        """Refresh the dropdown from user_settings.json after Recent Projects edits in App Settings."""
        prev_root = self.current_root() if preserve_current else ""
        prev_key = os.path.normcase(os.path.normpath(prev_root)) if prev_root else ""
        recents = get_saved_recent_projects()

        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        match_idx = -1
        for idx, r in enumerate(recents):
            if os.path.isdir(r):
                self.project_combo.addItem(_format_project_combo_label(r), r)
                if prev_key and os.path.normcase(os.path.normpath(r)) == prev_key:
                    match_idx = self.project_combo.count() - 1
        if match_idx >= 0:
            self.project_combo.setCurrentIndex(match_idx)
            self._current_root = self.project_combo.currentData() or ""
        elif self.project_combo.count() > 0:
            self.project_combo.setCurrentIndex(0)
            self._current_root = self.project_combo.currentData() or ""
        else:
            self._current_root = ""
        self.project_combo.blockSignals(False)

    def select_or_insert(self, root_dir: str) -> None:
        norm = ProjectRepository.resolve_vfx_root(root_dir)
        label = _format_project_combo_label(norm)
        idx = self.project_combo.findData(norm)
        if idx < 0:
            self.project_combo.blockSignals(True)
            self.project_combo.insertItem(0, label, norm)
            self.project_combo.setCurrentIndex(0)
            self.project_combo.blockSignals(False)
        else:
            self.project_combo.blockSignals(True)
            self.project_combo.setItemText(idx, label)
            self.project_combo.setCurrentIndex(idx)
            self.project_combo.blockSignals(False)
        self._current_root = norm

    def current_root(self) -> str:
        return self.project_combo.currentData() or self._current_root

    def _on_combo_changed(self, idx: int):
        if idx < 0:
            return
        root_dir = self.project_combo.currentData()
        if root_dir:
            self._current_root = root_dir
            self.project_selected.emit(root_dir)

    def _on_browse(self):
        default_root = (load_user_settings().get("default_projects_root") or "").strip()
        cur = (
            self.current_root()
            or (default_root if default_root and os.path.isdir(default_root) else "")
            or os.path.expanduser("~")
        )
        chosen = QFileDialog.getExistingDirectory(self, "Open VFX Project Root Folder", cur)
        if not chosen:
            return
        chosen = ProjectRepository.resolve_vfx_root(chosen)
        self.select_or_insert(chosen)
        self.project_selected.emit(chosen)
