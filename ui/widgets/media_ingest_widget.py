"""Embeddable Media Ingest widget for Project Manager, MediaIngestDialog, and Resolve UI."""

import os
from typing import Any, Dict, List, Optional

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_START_FRAME
from core.models import Project
from core.storage import ProjectRepository
from core.thumbnails import generate_media_thumbnail, generate_shot_thumbnail, get_shot_thumbnail_path
from core.timecode import format_shot_id, split_shot_id


class MediaIngestWidget(QWidget):
    """
    Modular Media / Plate / 3D Asset Ingest widget.
    Can be hosted inside MediaIngestDialog or embedded directly in a DaVinci Resolve UI.
    """

    ingest_completed = Signal(str, str)  # (shot_id, target_dir)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.project: Optional[Project] = None
        self.repo: Optional[ProjectRepository] = None
        self.ingester = None
        self.shot_ids: List[str] = []
        self.source_info: Dict[str, Any] = {}
        self.ingested_shot_id = ""

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # 1. Source Selection
        src_grp = QGroupBox("Source")
        src_grid = QGridLayout(src_grp)

        src_grid.addWidget(QLabel("Path:"), 0, 0)
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("Sequence frame (.exr/.dpx/.png), movie (.mov/.mp4), or 3D cache...")
        self.src_edit.textChanged.connect(self._on_source_changed)
        src_grid.addWidget(self.src_edit, 0, 1)

        btn_file = QPushButton("File...")
        btn_file.clicked.connect(self._on_browse_file)
        src_grid.addWidget(btn_file, 0, 2)

        btn_dir = QPushButton("Folder...")
        btn_dir.clicked.connect(self._on_browse_folder)
        src_grid.addWidget(btn_dir, 0, 3)

        self.src_summary_lbl = QLabel("")
        self.src_summary_lbl.setWordWrap(True)
        src_grid.addWidget(self.src_summary_lbl, 1, 0, 1, 4)
        layout.addWidget(src_grp)

        # 2. Target Shot & Routing
        tgt_grp = QGroupBox("Target & Routing")
        tgt_grid = QGridLayout(tgt_grp)

        mode_row = QHBoxLayout()
        self.existing_radio = QRadioButton("Existing Shot")
        self.new_shot_radio = QRadioButton("New Shot (Auto-Detect)")
        self.existing_radio.toggled.connect(self._on_target_mode_changed)
        mode_row.addWidget(self.existing_radio)
        mode_row.addWidget(self.new_shot_radio)
        mode_row.addStretch()
        tgt_grid.addLayout(mode_row, 0, 0, 1, 2)

        tgt_grid.addWidget(QLabel("Shot ID:"), 1, 0)
        self.shot_combo = QComboBox()
        self.shot_combo.setEditable(True)
        self.shot_combo.currentTextChanged.connect(self._update_next_version)
        tgt_grid.addWidget(self.shot_combo, 1, 1)

        tgt_grid.addWidget(QLabel("Specs:"), 2, 0)
        self.specs_edit = QLineEdit()
        self.specs_edit.setReadOnly(True)
        tgt_grid.addWidget(self.specs_edit, 2, 1)

        tgt_grid.addWidget(QLabel("Category:"), 3, 0)
        self.cat_combo = QComboBox()
        self.cat_combo.currentIndexChanged.connect(self._on_category_changed)
        tgt_grid.addWidget(self.cat_combo, 3, 1)

        tgt_grid.addWidget(QLabel("Identifier:"), 4, 0)
        self.ident_combo = QComboBox()
        self.ident_combo.setEditable(True)
        self.ident_combo.currentTextChanged.connect(self._update_next_version)
        tgt_grid.addWidget(self.ident_combo, 4, 1)

        tgt_grid.addWidget(QLabel("Version:"), 5, 0)
        self.ver_edit = QLineEdit("v0001")
        tgt_grid.addWidget(self.ver_edit, 5, 1)

        tgt_grid.addWidget(QLabel("Method:"), 6, 0)
        self.mode_combo = QComboBox()
        tgt_grid.addWidget(self.mode_combo, 6, 1)

        self.renumber_chk = QCheckBox(f"Renumber sequence to start at {DEFAULT_START_FRAME}")
        self.renumber_chk.setChecked(True)
        tgt_grid.addWidget(self.renumber_chk, 7, 0, 1, 2)

        self.update_range_chk = QCheckBox("Update shot properties (range, duration, resolution, FPS)")
        self.update_range_chk.setChecked(True)
        tgt_grid.addWidget(self.update_range_chk, 8, 0, 1, 2)

        layout.addWidget(tgt_grp)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

    def set_project(
        self,
        project: Project,
        repo: ProjectRepository,
        ingester_tool,
        shot_ids: List[str],
        default_shot_id: str = "",
        new_shot_mode: bool = False,
    ):
        self.project = project
        self.repo = repo
        self.ingester = ingester_tool
        self.shot_ids = list(shot_ids)

        self.cat_combo.blockSignals(True)
        self.cat_combo.clear()
        for cat_key, cat_label in self.ingester.CATEGORIES:
            self.cat_combo.addItem(cat_label, cat_key)
        self.cat_combo.blockSignals(False)

        self.mode_combo.clear()
        for mode_key, mode_label in self.ingester.INGEST_MODES:
            self.mode_combo.addItem(mode_label, mode_key)

        self.shot_combo.blockSignals(True)
        self.shot_combo.clear()
        for sid in self.shot_ids:
            self.shot_combo.addItem(sid)
        if default_shot_id:
            self.shot_combo.setCurrentText(default_shot_id)
        self.shot_combo.blockSignals(False)

        if new_shot_mode or not self.shot_ids:
            self.new_shot_radio.setChecked(True)
        else:
            self.existing_radio.setChecked(True)

        self._on_category_changed()
        self._on_target_mode_changed()

    def set_source_path(self, path: str):
        self.src_edit.setText(os.path.normpath(path) if path else "")

    def _suggest_next_shot_id(self) -> str:
        if not self.project:
            return "R01_0010"
        shots = self.project.all_shots()
        next_num = (len(shots) + 1) * 10
        prefix = "R01"
        if shots:
            s0_seq, _ = split_shot_id(shots[0].shot_id)
            if s0_seq:
                prefix = s0_seq
        return f"{prefix}-{next_num:04d}"

    def _on_target_mode_changed(self):
        if self.new_shot_radio.isChecked():
            self.shot_combo.setCurrentText(self._suggest_next_shot_id())
            self.update_range_chk.setChecked(True)
        elif self.shot_ids and self.shot_combo.currentText() not in self.shot_ids:
            self.shot_combo.setCurrentText(self.shot_ids[0])
        self._update_next_version()

    def _on_browse_file(self):
        chosen, _ = QFileDialog.getOpenFileName(self, "Select Source Media or Asset File")
        if chosen:
            self.src_edit.setText(os.path.normpath(chosen))

    def _on_browse_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, "Select Source Media Folder")
        if chosen:
            self.src_edit.setText(os.path.normpath(chosen))

    def _on_source_changed(self, path_text: str):
        if not self.ingester:
            return
        info = self.ingester.analyze_source(path_text.strip())
        self.source_info = info
        if not info.get("valid"):
            self.src_summary_lbl.setText(info.get("error", ""))
            self.specs_edit.clear()
            return

        res_str = info.get("resolution") or "Unknown res"
        fps_val = info.get("fps") or (self.project.fps if self.project else 24.0)
        f_count = int(info.get("frame_count", 1))
        dur_sec = info.get("duration_sec") or round(f_count / max(1.0, float(fps_val)), 2)

        if info.get("is_sequence"):
            txt = f"{info['display_name']}  |  {f_count} frames ({info['start_frame']}–{info['end_frame']})"
        else:
            txt = f"{info['display_name']} ({info['ext']})"

        self.src_summary_lbl.setText(txt)
        self.specs_edit.setText(f"{res_str}  |  {fps_val:g} fps  |  {f_count} frames ({dur_sec:.2f}s)")

        sug_cat = info.get("suggested_category")
        if sug_cat:
            idx = self.cat_combo.findData(sug_cat)
            if idx >= 0:
                self.cat_combo.setCurrentIndex(idx)

    def _on_category_changed(self):
        if not self.ingester:
            return
        cat_key = self.cat_combo.currentData() or "media"
        presets = self.ingester.COMMON_IDENTIFIERS.get(cat_key, ["plate"])
        cur_txt = self.ident_combo.currentText()
        self.ident_combo.blockSignals(True)
        self.ident_combo.clear()
        for p in presets:
            self.ident_combo.addItem(p)
        if cur_txt and cur_txt in presets:
            self.ident_combo.setCurrentText(cur_txt)
        self.ident_combo.blockSignals(False)
        self._update_next_version()

    def _update_next_version(self):
        if not self.project or not self.repo:
            return
        shot_id = self.shot_combo.currentText().strip()
        seq, shot = split_shot_id(shot_id)
        cat_key = self.cat_combo.currentData() or "media"
        ident = self.ident_combo.currentText().strip() or "plate"
        if seq and shot:
            norm_id = format_shot_id(seq, shot)
            next_v = self.repo.get_next_media_version(self.project, norm_id, cat_key, ident)
            self.ver_edit.setText(next_v)

    def run_ingest(self) -> bool:
        """Execute ingestion. Returns True on success."""
        if not self.project or not self.repo or not self.ingester:
            return False
        if not self.source_info.get("valid"):
            QMessageBox.warning(self, "Invalid Source", "Please select a valid source file or sequence folder first.")
            return False

        shot_id = self.shot_combo.currentText().strip()
        seq, shot = split_shot_id(shot_id)
        if not seq or not shot:
            QMessageBox.warning(self, "Invalid Shot ID", "Enter a Shot ID like 'R01_0010'.")
            return False

        norm_id = format_shot_id(seq, shot)
        cat_key = self.cat_combo.currentData() or "media"
        ident = self.ident_combo.currentText().strip() or "plate"
        ver = self.ver_edit.text().strip() or "v0001"
        mode = self.mode_combo.currentData() or "copy_rename"
        renum = self.renumber_chk.isChecked()

        f_count = int(self.source_info.get("frame_count", 1))
        existing_shot = self.project.get_shot(norm_id)
        if self.source_info.get("is_sequence"):
            s_frame = DEFAULT_START_FRAME if renum else int(self.source_info.get("start_frame", DEFAULT_START_FRAME))
        else:
            s_frame = existing_shot.start_frame if existing_shot else DEFAULT_START_FRAME

        self.repo.ensure_shot(self.project, norm_id, start_frame=s_frame)

        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(max(1, len(self.source_info.get("files", [])) or 1))
        self.progress_bar.setValue(0)

        def on_prog(done: int, total: int, fname: str):
            self.progress_bar.setValue(done)
            QApplication.processEvents()

        ok, msg, target_dir = self.ingester.ingest_to_shot(
            vfx_root=self.project.vfx_root,
            seq=seq,
            shot=shot,
            source_info=self.source_info,
            category=cat_key,
            identifier=ident,
            version=ver,
            mode=mode,
            renumber_to_start_frame=renum,
            target_start_frame=DEFAULT_START_FRAME,
            progress_callback=on_prog,
        )

        if ok:
            sh_obj = self.project.get_shot(norm_id)
            if self.update_range_chk.isChecked() and sh_obj:
                probed_fps = float(self.source_info.get("fps") or 0.0)
                eff_fps = probed_fps if probed_fps > 0 else self.project.fps
                sh_obj.start_frame = s_frame
                if f_count > 1:
                    sh_obj.duration = f"{f_count} ({f_count / max(1.0, eff_fps):.2f}s)"
                if self.source_info.get("resolution"):
                    sh_obj.set_field("resolution", self.source_info["resolution"])
                if probed_fps > 0:
                    sh_obj.fps = probed_fps
                if not sh_obj.name or sh_obj.name == f"{norm_id}_plate":
                    sh_obj.name = os.path.splitext(self.source_info.get("display_name", ""))[0] or f"{norm_id}_plate"

            # Auto-generate media thumbnail (and shot thumbnail if missing)
            try:
                inspected = self.repo.inspect_media_folder(target_dir)
                if inspected and inspected.get("is_playable"):
                    media_rec = {
                        "shot_id": norm_id,
                        "category": cat_key,
                        "identifier": ident,
                        "version": ver,
                        "version_dir": os.path.normpath(target_dir),
                        **inspected,
                    }
                    generate_media_thumbnail(self.project, media_rec, force=True)
                    if sh_obj:
                        shot_png = get_shot_thumbnail_path(self.project, norm_id)
                        if not shot_png or not os.path.isfile(shot_png):
                            generate_shot_thumbnail(
                                self.project, self.repo, sh_obj, force=True, media_dict=media_rec
                            )
            except Exception as e:
                print(f"Warning generating thumbnail after ingest: {e}")

            if sh_obj:
                self.repo.save(self.project)
            self.ingested_shot_id = norm_id
            self.ingest_completed.emit(norm_id, target_dir)
            QMessageBox.information(self, "Ingestion Complete", f"{msg}\n\n{target_dir}")
            return True

        QMessageBox.warning(self, "Ingestion Failed", msg)
        return False
