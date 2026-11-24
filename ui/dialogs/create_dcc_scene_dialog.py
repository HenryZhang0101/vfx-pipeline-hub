"""Dialog for creating a new DCC scene (Nuke, Houdini, Blender) for a shot."""

from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from config import DEFAULT_SHOT_DEPARTMENTS, load_user_settings
from core.models import Project
from core.storage import ProjectRepository
from core.timecode import format_shot_id
from ui.theme import get_active_stylesheet


class CreateDCCSceneDialog(QDialog):
    """Dialog for creating a new DCC scene (Nuke, Houdini, Blender) for a shot."""

    def __init__(self, parent, seq: str, shot: str, dcc_plugin, project: Project, repo: ProjectRepository):
        super().__init__(parent)
        self.seq = seq
        self.shot = shot
        self.shot_id = format_shot_id(seq, shot)
        self.dcc_plugin = dcc_plugin
        self.project = project
        self.repo = repo
        self.created_scene_path = ""
        self.launch_after = True

        self.setWindowTitle(f"New {dcc_plugin.display_name} Scene — {self.shot_id}")
        self.resize(460, 300)
        self.setStyleSheet(get_active_stylesheet())

        layout = QVBoxLayout(self)
        grid = QGridLayout()

        grid.addWidget(QLabel("Shot:"), 0, 0)
        lbl_shot = QLabel(self.shot_id)
        lbl_shot.setStyleSheet("font-weight: bold; color: #61afef;")
        grid.addWidget(lbl_shot, 0, 1)

        grid.addWidget(QLabel("DCC Module:"), 1, 0)
        grid.addWidget(QLabel(f"{dcc_plugin.display_name} ({dcc_plugin.primary_extension})"), 1, 1)

        grid.addWidget(QLabel("Department:"), 2, 0)
        self.dept_combo = QComboBox()
        for d in DEFAULT_SHOT_DEPARTMENTS:
            self.dept_combo.addItem(f"{d['name']} ({d['abbr']})", d["name"])
        idx = self.dept_combo.findData(dcc_plugin.default_department)
        if idx < 0:
            for i, d in enumerate(DEFAULT_SHOT_DEPARTMENTS):
                if d["abbr"] == dcc_plugin.default_department:
                    idx = i
                    break
        if idx >= 0:
            self.dept_combo.setCurrentIndex(idx)
        self.dept_combo.currentIndexChanged.connect(self._on_dept_changed)
        grid.addWidget(self.dept_combo, 2, 1)

        grid.addWidget(QLabel("Task Name:"), 3, 0)
        self.task_edit = QLineEdit(dcc_plugin.default_task)
        self.task_edit.textChanged.connect(self._update_version_preview)
        grid.addWidget(self.task_edit, 3, 1)

        grid.addWidget(QLabel("Version:"), 4, 0)
        self.ver_edit = QLineEdit("v0001")
        grid.addWidget(self.ver_edit, 4, 1)

        grid.addWidget(QLabel("Comment:"), 5, 0)
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("Initial setup / plate slapcomp...")
        grid.addWidget(self.comment_edit, 5, 1)

        layout.addLayout(grid)

        self.bootstrap_chk = QCheckBox(
            "Auto-Bootstrap Scene (Frame range, FPS, Read ingested plates & pre-wire Write node)"
        )
        self.bootstrap_chk.setChecked(True)
        layout.addWidget(self.bootstrap_chk)

        self.launch_chk = QCheckBox(f"Launch {dcc_plugin.display_name} immediately after creating scene")
        self.launch_chk.setChecked(True)
        layout.addWidget(self.launch_chk)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        create_btn = QPushButton(f"Create {dcc_plugin.primary_extension} Scene")
        create_btn.setObjectName("success_btn")
        create_btn.clicked.connect(self._on_create)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(create_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._update_version_preview()

    def _on_dept_changed(self):
        dept_name = self.dept_combo.currentData() or "Compositing"
        for d in DEFAULT_SHOT_DEPARTMENTS:
            if d["name"] == dept_name or d["abbr"] == dept_name:
                self.task_edit.setText(d["default_task"])
                break
        self._update_version_preview()

    def _update_version_preview(self):
        dept = self.dept_combo.currentData() or "Compositing"
        next_v = self.repo.get_next_scene_version(self.project, self.shot_id, dept)
        self.ver_edit.setText(next_v)

    def _on_create(self):
        dept = self.dept_combo.currentData() or "Compositing"
        task = self.task_edit.text().strip() or "comp"
        ver = self.ver_edit.text().strip() or "v0001"
        comment = self.comment_edit.text().strip()

        scene_path = self.repo.build_scene_filepath(
            self.project, self.shot_id, dept, ver, self.dcc_plugin.primary_extension, task_label=task
        )
        shot_obj = self.project.get_shot(self.shot_id)
        start_f = shot_obj.start_frame if shot_obj else 1001
        end_f = shot_obj.end_frame if shot_obj else 1065
        fps = self.project.fps
        width, height = self.project.resolution

        all_media = self.repo.scan_all_shot_media(self.project, self.shot_id)
        plates = [
            m for m in all_media
            if m.get("category") == "media" and m.get("identifier", "").lower() != "comp"
        ]

        shot_context = {
            "project_name": self.project.name,
            "vfx_root": self.project.vfx_root,
            "seq": self.seq,
            "shot": self.shot,
            "department": dept,
            "task": task,
            "version": ver,
            "start_frame": start_f,
            "end_frame": end_f,
            "fps": fps,
            "width": width,
            "height": height,
            "plates": plates,
        }

        ok, msg = self.dcc_plugin.create_scene(
            scene_path,
            shot_context=shot_context,
            bootstrap=self.bootstrap_chk.isChecked(),
        )
        if not ok:
            QMessageBox.warning(self, "Scene Creation Error", msg)
            return

        settings = load_user_settings()
        self.repo.write_scene_metadata(
            scene_path,
            comment=comment,
            artist=settings.get("artist_name", ""),
        )
        self.created_scene_path = scene_path
        self.launch_after = self.launch_chk.isChecked()
        self.accept()
