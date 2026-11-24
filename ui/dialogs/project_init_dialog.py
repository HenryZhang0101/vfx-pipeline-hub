"""Dialog for initializing a new VFX Project workspace."""

import os
from typing import Tuple

from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from config import DEFAULT_EXPORT_FOLDER, DEFAULT_RESOLUTION_PRESETS, load_user_settings
from core.models.project import infer_project_name_from_path
from core.models.resolution import default_resolution_presets
from ui.theme import get_active_stylesheet
from ui.widgets.spec_widgets import FpsComboBox, ResolutionPickerWidget


# Backwards-compatible alias for any callers importing RESOLUTION_PRESETS
RESOLUTION_PRESETS = [
    (f"{int(w)}x{int(h)} ({name})", (w, h))
    for name, w, h in DEFAULT_RESOLUTION_PRESETS
]


class ProjectInitDialog(QDialog):
    """Minimal Notion-Dark dialog for creating/initializing a VFX Project."""

    def __init__(self, parent=None, default_root: str = ""):
        super().__init__(parent)
        self.root_dir = ""
        self.project_name = ""
        self.fps: float = 24.0
        self.resolution: Tuple[float, float] = (1920.0, 1080.0)
        self.initial_sequence = ""
        self.export_folder = DEFAULT_EXPORT_FOLDER
        self.create_excel = True

        self.setWindowTitle("Initialize VFX Project")
        self.setStyleSheet(get_active_stylesheet())
        self.resize(560, 330)

        layout = QVBoxLayout(self)
        grid = QGridLayout()

        grid.addWidget(QLabel("VFX Project Root:"), 0, 0)
        self.root_edit = QLineEdit(default_root)
        self.root_edit.setPlaceholderText("Select or type VFX folder (e.g. .../04_vfx or .../05_vfx)...")
        self.root_edit.textChanged.connect(self._on_root_changed)
        grid.addWidget(self.root_edit, 0, 1)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._on_browse)
        grid.addWidget(browse_btn, 0, 2)

        grid.addWidget(QLabel("Project Name:"), 1, 0)
        self.name_edit = QLineEdit(infer_project_name_from_path(default_root) if default_root else "")
        grid.addWidget(self.name_edit, 1, 1, 1, 2)

        grid.addWidget(QLabel("FPS:"), 2, 0)
        self.fps_combo = FpsComboBox(fps=24.0)
        grid.addWidget(self.fps_combo, 2, 1, 1, 2)

        grid.addWidget(QLabel("Resolution:"), 3, 0)
        self.res_picker = ResolutionPickerWidget(
            presets=default_resolution_presets(),
            resolution=(1920.0, 1080.0),
        )
        grid.addWidget(self.res_picker, 3, 1, 1, 2)

        grid.addWidget(QLabel("Initial Sequence:"), 4, 0)
        self.seq_edit = QLineEdit("R01")
        self.seq_edit.setPlaceholderText("Optional, e.g. R01 or SH010")
        grid.addWidget(self.seq_edit, 4, 1, 1, 2)

        grid.addWidget(QLabel("Client Export Folder:"), 5, 0)
        self.export_folder_edit = QLineEdit(DEFAULT_EXPORT_FOLDER)
        self.export_folder_edit.setPlaceholderText(f"Subfolder inside VFX Root (default: {DEFAULT_EXPORT_FOLDER})")
        grid.addWidget(self.export_folder_edit, 5, 1, 1, 2)

        layout.addLayout(grid)

        self.excel_chk = QCheckBox("Create linked Excel ShotList (<Project>_ShotList.xlsx)")
        self.excel_chk.setChecked(True)
        layout.addWidget(self.excel_chk)

        layout.addStretch()

        btns = QHBoxLayout()
        btns.addStretch()
        ok_btn = QPushButton("Initialize Project")
        ok_btn.setObjectName("primary_btn")
        ok_btn.clicked.connect(self._on_accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

    def _on_browse(self):
        default_root = (load_user_settings().get("default_projects_root") or "").strip()
        start_dir = (
            self.root_edit.text().strip()
            or (default_root if default_root and os.path.isdir(default_root) else "")
            or os.path.expanduser("~")
        )
        chosen = QFileDialog.getExistingDirectory(self, "Select VFX Project Root Folder", start_dir)
        if chosen:
            self.root_edit.setText(os.path.normpath(chosen))

    def _on_root_changed(self, text: str):
        clean = text.strip().rstrip("\\/")
        if clean and not self.name_edit.isModified():
            self.name_edit.setText(infer_project_name_from_path(clean))

    def _on_accept(self):
        root = self.root_edit.text().strip()
        if not root:
            QMessageBox.warning(self, "Missing Path", "Please select or enter a VFX project root folder.")
            return
        self.root_dir = os.path.normpath(root)
        self.project_name = self.name_edit.text().strip() or infer_project_name_from_path(self.root_dir)
        self.fps = self.fps_combo.fps_value()
        self.resolution = self.res_picker.resolution_value() or (1920.0, 1080.0)
        self.initial_sequence = self.seq_edit.text().strip()
        self.export_folder = self.export_folder_edit.text().strip() or DEFAULT_EXPORT_FOLDER
        self.create_excel = self.excel_chk.isChecked()
        self.accept()
