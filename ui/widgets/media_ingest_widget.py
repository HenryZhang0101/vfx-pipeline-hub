"""Embeddable Media Ingest widget for Project Manager, MediaIngestDialog, and Resolve UI."""

import copy
import os
import threading
from typing import Any, Dict, List, Optional

from qtpy.QtCore import QThread, QTimer, Signal
from qtpy.QtWidgets import (
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
from ui.widgets.thumbnail_worker import clear_thumbnail_pixmap_cache


class _SourceAnalyzeWorker(QThread):
    """Background worker that probes a media source path without freezing the UI."""

    analysis_finished = Signal(int, dict)  # generation_id, info_dict

    def __init__(self, ingester, path: str, generation_id: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ingester = ingester
        self.path = path
        self.generation_id = generation_id

    def run(self) -> None:
        try:
            info = self.ingester.analyze_source(self.path)
        except Exception as e:
            info = {"valid": False, "error": str(e)}
        self.analysis_finished.emit(self.generation_id, info)


class _MediaIngestWorker(QThread):
    """Background worker for copying/hardlinking media and generating thumbnails."""

    progress_changed = Signal(int, int, str)  # done, total, status_text
    finished_ingest = Signal(bool, str, str, list, bool)  # ok, msg, target_dir, thumb_paths, cancelled

    def __init__(
        self,
        ingester,
        project: Project,
        repo: ProjectRepository,
        seq: str,
        shot: str,
        norm_id: str,
        source_info: Dict[str, Any],
        category: str,
        identifier: str,
        version: str,
        mode: str,
        renumber: bool,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.ingester = ingester
        self.project = project
        self.repo = repo
        self.seq = seq
        self.shot = shot
        self.norm_id = norm_id
        self.source_info = copy.deepcopy(source_info)
        self.category = category
        self.identifier = identifier
        self.version = version
        self.mode = mode
        self.renumber = renumber
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def run(self) -> None:
        def _on_prog(done: int, total: int, label: str) -> None:
            self.progress_changed.emit(done, total, label)

        ok, msg, target_dir = self.ingester.ingest_to_shot(
            vfx_root=self.project.vfx_root,
            seq=self.seq,
            shot=self.shot,
            source_info=self.source_info,
            category=self.category,
            identifier=self.identifier,
            version=self.version,
            mode=self.mode,
            renumber_to_start_frame=self.renumber,
            target_start_frame=DEFAULT_START_FRAME,
            progress_callback=_on_prog,
            cancel_check=self._cancel_event.is_set,
        )

        thumb_paths: List[str] = []
        if ok and not self._cancel_event.is_set():
            self.progress_changed.emit(1000, 1000, "Generating thumbnails...")
            try:
                inspected = self.repo.inspect_media_folder(target_dir)
                if inspected and inspected.get("is_playable"):
                    media_rec = {
                        "shot_id": self.norm_id,
                        "category": self.category,
                        "identifier": self.identifier,
                        "version": self.version,
                        "version_dir": os.path.normpath(target_dir),
                        **inspected,
                    }
                    media_thumb = generate_media_thumbnail(self.project, media_rec, force=True)
                    if media_thumb:
                        thumb_paths.append(media_thumb)
                    sh_obj = self.project.get_shot(self.norm_id)
                    if sh_obj:
                        shot_png = get_shot_thumbnail_path(self.project, self.norm_id)
                        if not shot_png or not os.path.isfile(shot_png):
                            gen_shot_png = generate_shot_thumbnail(
                                self.project, self.repo, sh_obj, force=True, media_dict=media_rec
                            )
                            if gen_shot_png:
                                thumb_paths.append(gen_shot_png)
            except Exception as e:
                print(f"Warning generating thumbnail after ingest: {e}")

        self.finished_ingest.emit(ok, msg, target_dir, thumb_paths, self._cancel_event.is_set())


class MediaIngestWidget(QWidget):
    """
    Modular Media / Plate / 3D Asset Ingest widget.
    Can be hosted inside MediaIngestDialog or embedded directly in a DaVinci Resolve UI.
    """

    ingest_started = Signal()
    ingest_completed = Signal(str, str)  # (shot_id, target_dir)
    ingest_finished = Signal(bool, str, str, str)  # (ok, shot_id, target_dir, message)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.project: Optional[Project] = None
        self.repo: Optional[ProjectRepository] = None
        self.ingester = None
        self.shot_ids: List[str] = []
        self.source_info: Dict[str, Any] = {}
        self.ingested_shot_id = ""
        self._analyze_gen = 0
        self._analyze_worker: Optional[_SourceAnalyzeWorker] = None
        self._ingest_worker: Optional[_MediaIngestWorker] = None
        self._pending_ingest_meta: Dict[str, Any] = {}

        self._analyze_timer = QTimer(self)
        self._analyze_timer.setSingleShot(True)
        self._analyze_timer.setInterval(180)
        self._analyze_timer.timeout.connect(self._start_source_analysis)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # 1. Source Selection
        self.src_grp = QGroupBox("Source")
        src_grid = QGridLayout(self.src_grp)

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
        layout.addWidget(self.src_grp)

        # 2. Target Shot & Routing
        self.tgt_grp = QGroupBox("Target & Routing")
        tgt_grid = QGridLayout(self.tgt_grp)

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

        layout.addWidget(self.tgt_grp)

        self.status_lbl = QLabel("")
        self.status_lbl.setVisible(False)
        layout.addWidget(self.status_lbl)

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
        raw = path_text.strip()
        self._analyze_gen += 1
        if not raw:
            self._analyze_timer.stop()
            self.source_info = {}
            self.src_summary_lbl.setText("")
            self.specs_edit.clear()
            return
        self.src_summary_lbl.setText("Inspecting source...")
        self._analyze_timer.start()

    def _start_source_analysis(self):
        if not self.ingester:
            return
        raw = self.src_edit.text().strip()
        if not raw:
            return
        gen = self._analyze_gen
        worker = _SourceAnalyzeWorker(self.ingester, raw, gen, self)
        worker.analysis_finished.connect(self._on_source_analysis_finished)
        worker.finished.connect(worker.deleteLater)
        self._analyze_worker = worker
        worker.start()

    def _on_source_analysis_finished(self, gen: int, info: Dict[str, Any]):
        if gen != self._analyze_gen:
            return
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

    def is_ingesting(self) -> bool:
        return bool(self._ingest_worker and self._ingest_worker.isRunning())

    def cancel_ingest(self) -> None:
        if self._ingest_worker and self._ingest_worker.isRunning():
            self._ingest_worker.request_cancel()
            self.status_lbl.setText("Cancelling ingestion...")

    def _set_busy(self, busy: bool) -> None:
        self.src_grp.setEnabled(not busy)
        self.tgt_grp.setEnabled(not busy)

    def start_ingest(self) -> bool:
        """Validate inputs and start asynchronous media ingestion. Returns True if worker started."""
        if self.is_ingesting():
            return False
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

        self._pending_ingest_meta = {
            "norm_id": norm_id,
            "s_frame": s_frame,
            "f_count": f_count,
            "update_range": self.update_range_chk.isChecked(),
            "source_info": copy.deepcopy(self.source_info),
        }

        self._set_busy(True)
        self.status_lbl.setVisible(True)
        self.status_lbl.setText("Starting ingestion...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(1000)
        self.progress_bar.setValue(0)

        worker = _MediaIngestWorker(
            ingester=self.ingester,
            project=self.project,
            repo=self.repo,
            seq=seq,
            shot=shot,
            norm_id=norm_id,
            source_info=self.source_info,
            category=cat_key,
            identifier=ident,
            version=ver,
            mode=mode,
            renumber=renum,
            parent=self,
        )
        worker.progress_changed.connect(self._on_ingest_progress)
        worker.finished_ingest.connect(self._on_ingest_worker_finished)
        self._ingest_worker = worker
        self.ingest_started.emit()
        worker.start()
        return True

    def run_ingest(self) -> bool:
        """Backwards-compatible alias that starts non-blocking ingestion."""
        return self.start_ingest()

    def _on_ingest_progress(self, done: int, total: int, label: str) -> None:
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(min(done, total))
        if label:
            self.status_lbl.setText(label)

    def _on_ingest_worker_finished(
        self,
        ok: bool,
        msg: str,
        target_dir: str,
        thumb_paths: List[str],
        was_cancelled: bool,
    ) -> None:
        meta = self._pending_ingest_meta or {}
        norm_id = meta.get("norm_id", "")
        self._ingest_worker = None
        self._set_busy(False)

        if ok and self.project and self.repo:
            sh_obj = self.project.get_shot(norm_id)
            src_info = meta.get("source_info") or {}
            s_frame = int(meta.get("s_frame", DEFAULT_START_FRAME))
            f_count = int(meta.get("f_count", 1))
            if meta.get("update_range") and sh_obj:
                probed_fps = float(src_info.get("fps") or 0.0)
                eff_fps = probed_fps if probed_fps > 0 else self.project.fps
                sh_obj.start_frame = s_frame
                if f_count > 1:
                    sh_obj.duration = f"{f_count} ({f_count / max(1.0, eff_fps):.2f}s)"
                if src_info.get("resolution"):
                    sh_obj.set_field("resolution", src_info["resolution"])
                if probed_fps > 0:
                    sh_obj.fps = probed_fps
                if not sh_obj.name or sh_obj.name == f"{norm_id}_plate":
                    sh_obj.name = os.path.splitext(src_info.get("display_name", ""))[0] or f"{norm_id}_plate"

            for tp in thumb_paths:
                clear_thumbnail_pixmap_cache(tp)

            if sh_obj:
                self.repo.save(self.project)
            self.ingested_shot_id = norm_id
            self.status_lbl.setText("Ingestion complete.")
            self.ingest_completed.emit(norm_id, target_dir)
            QMessageBox.information(self, "Ingestion Complete", f"{msg}\n\n{target_dir}")
            self.ingest_finished.emit(True, norm_id, target_dir, msg)
            return

        if was_cancelled:
            self.status_lbl.setText("Ingestion cancelled.")
            self.ingest_finished.emit(False, norm_id, target_dir, msg)
            return

        self.status_lbl.setText("Ingestion failed.")
        QMessageBox.warning(self, "Ingestion Failed", msg)
        self.ingest_finished.emit(False, norm_id, target_dir, msg)
