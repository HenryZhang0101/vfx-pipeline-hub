"""DaVinci Resolve-specific Notion-Dark dialogs (Assign, Batch Assign, Export Plates/Comps, Load Comps)."""

import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from config import (
    DEFAULT_COMP_IDENTIFIER,
    DEFAULT_HANDLES,
    DEFAULT_PLATE_IDENTIFIER,
    DEFAULT_RENDER_PRESET,
    DEFAULT_SEQ_PREFIX,
    DEFAULT_SHOT_START,
    DEFAULT_SHOT_STEP,
    DEFAULT_START_FRAME,
    DEFAULT_STATUSES,
    DEFAULT_VERSION,
    load_user_settings,
    save_user_settings,
)
from core.io.resolve import (
    BUILTIN_MARKER_FIELD_DEFS,
    DEFAULT_MARKER_FIELDS,
    capture_shot_thumbnail_to_vfx,
    collect_timeline_clips,
    collect_timeline_markers,
    find_shot_clips_on_timeline,
    format_shot_marker_note,
    get_current_project,
    get_timeline_by_name,
    get_timeline_fps,
    list_timelines,
    load_media_item_to_timeline,
    normalize_comp_render_files,
    normalize_plate_render_files,
    render_shot_job,
    save_resolve_comp_scenefiles,
)
from core.models import MediaItem, Project, Shot
from core.timecode import format_shot_id, timecode_to_frame
from ui.theme import get_active_stylesheet, get_app_icon


def _project_status_names(project: Optional[Project]) -> List[str]:
    if project:
        opts = [opt.name for opt in project.get_status_property().options if opt.name]
        if opts:
            return opts
    return list(DEFAULT_STATUSES)


# =============================================================================
# 1. ASSIGN PLAYHEAD CLIP DIALOG
# =============================================================================


class ResolveMarkerFieldsDialog(QDialog):
    """Configure which shot fields are logged into DaVinci Resolve VFX timeline markers."""

    def __init__(
        self,
        parent=None,
        project: Optional[Project] = None,
        sample_shot: Optional[Shot] = None,
        current_fields: Optional[List[str]] = None,
    ):
        super().__init__(parent)
        self.project = project
        self.sample_shot = sample_shot or self._build_default_sample_shot(project)

        if current_fields is None:
            saved = load_user_settings().get("resolve_marker_fields")
            if isinstance(saved, list):
                active_fields = [str(f) for f in saved]
            else:
                active_fields = list(DEFAULT_MARKER_FIELDS)
        else:
            active_fields = [str(f) for f in current_fields]
        active_set = set(active_fields)

        self.setWindowTitle("Resolve Marker Fields")
        self.setWindowIcon(get_app_icon())
        self.setStyleSheet(get_active_stylesheet())
        self.resize(460, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        desc = QLabel(
            "Choose which shot details are logged into DaVinci Resolve VFX timeline markers.\n"
            "Empty fields (such as an unassigned artist or blank notes) are omitted automatically."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #9B9A97;")
        layout.addWidget(desc)

        # Built-in fields
        self._checkboxes: List[Tuple[str, QCheckBox]] = []
        builtin_group = QGroupBox("Built-in Shot Info")
        builtin_grid = QGridLayout(builtin_group)
        builtin_grid.setSpacing(8)
        for idx, (key, label) in enumerate(BUILTIN_MARKER_FIELD_DEFS):
            chk = QCheckBox(label)
            chk.setChecked(key in active_set)
            chk.toggled.connect(self._update_preview)
            self._checkboxes.append((key, chk))
            builtin_grid.addWidget(chk, idx // 2, idx % 2)
        layout.addWidget(builtin_group)

        # Custom project properties (if any)
        custom_prop_names: List[str] = []
        if project:
            for prop in (getattr(project, "shot_properties", None) or []):
                if not prop.builtin and prop.name and prop.name not in custom_prop_names:
                    custom_prop_names.append(prop.name)

        if custom_prop_names:
            custom_group = QGroupBox("Project Custom Properties")
            custom_grid = QGridLayout(custom_group)
            custom_grid.setSpacing(8)
            for idx, prop_name in enumerate(custom_prop_names):
                chk = QCheckBox(prop_name)
                chk.setChecked(prop_name in active_set or f"custom:{prop_name}" in active_set)
                chk.toggled.connect(self._update_preview)
                self._checkboxes.append((prop_name, chk))
                custom_grid.addWidget(chk, idx // 2, idx % 2)
            layout.addWidget(custom_group)

        # Live preview
        preview_group = QGroupBox("Marker Note Preview")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_edit = QPlainTextEdit()
        self.preview_edit.setReadOnly(True)
        self.preview_edit.setPlaceholderText("(Empty marker note)")
        self.preview_edit.setMaximumHeight(110)
        preview_layout.addWidget(self.preview_edit)
        layout.addWidget(preview_group)

        # Footer buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._on_reset_defaults)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()

        save_btn = QPushButton("Save")
        save_btn.setObjectName("primary_btn")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._update_preview()

    @staticmethod
    def _build_default_sample_shot(project: Optional[Project]) -> Shot:
        custom_vals: Dict[str, str] = {}
        if project:
            for prop in (getattr(project, "shot_properties", None) or []):
                if not prop.builtin and prop.name:
                    sample_val = prop.options[0].name if prop.options else "Sample"
                    custom_vals[prop.name] = sample_val
        from config import get_default_artist_name

        return Shot(
            shot_id="SH_0010",
            name="A001C012_250101.mov",
            dur="72 (3.00s)",
            handles="10",
            status="WIP",
            assign=get_default_artist_name(),
            notes="Rig removal + sky replacement",
            comp_version="v0002",
            resolution=(3840, 2160),
            fps=24.0,
            custom_fields=custom_vals,
        )

    def selected_fields(self) -> List[str]:
        return [key for key, chk in self._checkboxes if chk.isChecked()]

    def _update_preview(self):
        self.preview_edit.setPlainText(format_shot_marker_note(self.sample_shot, self.selected_fields()))

    def _on_reset_defaults(self):
        default_set = set(DEFAULT_MARKER_FIELDS)
        for key, chk in self._checkboxes:
            chk.setChecked(key in default_set)
        self._update_preview()

    def _on_save(self):
        save_user_settings({"resolve_marker_fields": self.selected_fields()})
        self.accept()


class AssignShotDialog(QDialog):
    """Confirmation dialog for assigning the DaVinci Resolve playhead clip to a VFX Shot."""

    def __init__(
        self,
        parent,
        clip_info: Dict[str, Any],
        default_seq: str,
        default_shot: int,
        default_handles: int,
        project: Optional[Project] = None,
    ):
        super().__init__(parent)
        self.clip_info = clip_info
        self.project = project
        self.setWindowTitle(f"Assign VFX Shot — {clip_info['clip_name']}")
        self.setWindowIcon(get_app_icon())
        self.setStyleSheet(get_active_stylesheet())
        self.resize(480, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Clip metadata card
        info_frame = QFrame()
        info_frame.setObjectName("card_frame")
        info_layout = QGridLayout(info_frame)
        info_layout.setContentsMargins(12, 10, 12, 10)
        info_layout.setSpacing(6)

        info_layout.addWidget(QLabel("Clip Name:"), 0, 0)
        lbl_cname = QLabel(clip_info["clip_name"])
        lbl_cname.setStyleSheet("font-weight: bold; color: #529CCA;")
        info_layout.addWidget(lbl_cname, 0, 1)

        info_layout.addWidget(QLabel("Timeline:"), 1, 0)
        info_layout.addWidget(QLabel(clip_info["timeline"]), 1, 1)

        info_layout.addWidget(QLabel("Cut Range:"), 2, 0)
        range_str = (
            f"{clip_info['start_tc']} - {clip_info['end_tc']} "
            f"({clip_info['dur_f']} frames @ {clip_info['fps']:.3g} fps)"
        )
        info_layout.addWidget(QLabel(range_str), 2, 1)
        layout.addWidget(info_frame)

        # Assignment form
        grid = QGridLayout()
        grid.setSpacing(8)

        grid.addWidget(QLabel("Sequence:"), 0, 0)
        self.seq_edit = QLineEdit(default_seq)
        self.seq_edit.textChanged.connect(self._update_preview)
        grid.addWidget(self.seq_edit, 0, 1)

        grid.addWidget(QLabel("Shot #:"), 1, 0)
        self.shot_spin = QSpinBox()
        self.shot_spin.setRange(1, 99999)
        self.shot_spin.setSingleStep(DEFAULT_SHOT_STEP)
        self.shot_spin.setValue(int(default_shot))
        self.shot_spin.valueChanged.connect(self._update_preview)
        grid.addWidget(self.shot_spin, 1, 1)

        grid.addWidget(QLabel("Shot ID Preview:"), 2, 0)
        self.preview_lbl = QLabel()
        self.preview_lbl.setStyleSheet("font-weight: bold; font-size: 14px; color: #4DAB9A;")
        grid.addWidget(self.preview_lbl, 2, 1)

        grid.addWidget(QLabel("Handles (+/- frames):"), 3, 0)
        self.handles_spin = QSpinBox()
        self.handles_spin.setRange(0, 500)
        self.handles_spin.setValue(int(default_handles))
        grid.addWidget(self.handles_spin, 3, 1)

        grid.addWidget(QLabel("Status:"), 4, 0)
        self.status_cb = QComboBox()
        for st in _project_status_names(project):
            self.status_cb.addItem(st)
        grid.addWidget(self.status_cb, 4, 1)

        grid.addWidget(QLabel("Notes:"), 5, 0)
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("Optional notes or description...")
        grid.addWidget(self.notes_edit, 5, 1)

        marker_row = QHBoxLayout()
        marker_row.setSpacing(8)
        self.marker_chk = QCheckBox("Add / Update VFX Timeline Marker")
        self.marker_chk.setChecked(True)
        marker_row.addWidget(self.marker_chk)
        marker_row.addStretch()
        marker_fields_btn = QPushButton("Fields...")
        marker_fields_btn.setToolTip("Choose which shot info is logged in Resolve timeline markers")
        marker_fields_btn.clicked.connect(self._on_configure_marker_fields)
        marker_row.addWidget(marker_fields_btn)
        grid.addLayout(marker_row, 6, 0, 1, 2)

        layout.addLayout(grid)
        layout.addStretch()

        btn_box = QHBoxLayout()
        btn_box.setSpacing(8)
        btn_box.addStretch()

        assign_btn = QPushButton("Assign Shot")
        assign_btn.setObjectName("primary_btn")
        assign_btn.clicked.connect(self.accept)
        btn_box.addWidget(assign_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_box.addWidget(cancel_btn)

        layout.addLayout(btn_box)
        self._update_preview()

    def _on_configure_marker_fields(self):
        sample = Shot(
            shot_id=self.preview_lbl.text().strip() or "R01_0010",
            name=self.clip_info.get("clip_name", ""),
            status=self.status_cb.currentText(),
            handles=str(self.handles_spin.value()),
            notes=self.notes_edit.text().strip() or "Remove rig and cleanup",
        )
        dlg = ResolveMarkerFieldsDialog(self, project=self.project, sample_shot=sample)
        dlg.exec_()

    def _update_preview(self):
        seq = self.seq_edit.text().strip() or DEFAULT_SEQ_PREFIX
        shot_num = self.shot_spin.value()
        self.preview_lbl.setText(format_shot_id(seq, shot_num))

    @property
    def result_data(self) -> Dict[str, Any]:
        seq = self.seq_edit.text().strip() or DEFAULT_SEQ_PREFIX
        shot_num = self.shot_spin.value()
        return {
            "seq": seq,
            "shot_num": shot_num,
            "shot_id": format_shot_id(seq, shot_num),
            "handles": self.handles_spin.value(),
            "status": self.status_cb.currentText(),
            "notes": self.notes_edit.text().strip(),
            "sync_marker": self.marker_chk.isChecked(),
            "start_tc": self.clip_info.get("start_tc", ""),
            "end_tc": self.clip_info.get("end_tc", ""),
            "dur_f": self.clip_info.get("dur_f", 0),
            "fps": self.clip_info.get("fps", 24.0),
        }


# =============================================================================
# 2. BATCH ASSIGN SHOTS FROM TIMELINE DIALOG (VIDEO TRACK OR MARKERS)
# =============================================================================


class BatchAssignTimelineDialog(QDialog):
    """Batch-assign VFX shots from all clips on a Resolve video track or timeline markers."""

    MARKER_COLORS = [
        "All",
        "Blue",
        "Cyan",
        "Green",
        "Yellow",
        "Red",
        "Pink",
        "Purple",
        "Fuchsia",
        "Rose",
        "Lavender",
        "Sky",
        "Mint",
        "Lemon",
        "Sand",
        "Cocoa",
        "Cream",
    ]

    def __init__(
        self,
        parent,
        resolve,
        project: Optional[Project],
        default_seq: str = DEFAULT_SEQ_PREFIX,
        default_shot: int = DEFAULT_SHOT_START,
        default_handles: int = DEFAULT_HANDLES,
    ):
        super().__init__(parent)
        self.resolve = resolve
        self.vfx_project = project
        self._timelines: List[Any] = []
        self._rows: List[Dict[str, Any]] = []
        self._updating_table = False

        self.setWindowTitle("Batch Assign Shots from Timeline")
        self.setWindowIcon(get_app_icon())
        self.setStyleSheet(get_active_stylesheet())
        self.resize(1040, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QLabel("BATCH ASSIGN VFX SHOTS FROM DAVINCI RESOLVE TIMELINE")
        header.setStyleSheet("font-weight: bold; font-size: 13px; color: #529CCA;")
        layout.addWidget(header)

        # Source controls
        src_card = QFrame()
        src_card.setObjectName("card_frame")
        src_grid = QGridLayout(src_card)
        src_grid.setContentsMargins(12, 10, 12, 10)
        src_grid.setSpacing(8)

        src_grid.addWidget(QLabel("Timeline:"), 0, 0)
        self.timeline_cb = QComboBox()
        self.timeline_cb.currentIndexChanged.connect(self._on_timeline_changed)
        src_grid.addWidget(self.timeline_cb, 0, 1)

        src_grid.addWidget(QLabel("Source:"), 0, 2)
        self.source_cb = QComboBox()
        self.source_cb.addItem("Video Track Clips", "track")
        self.source_cb.addItem("Timeline Markers", "markers")
        self.source_cb.currentIndexChanged.connect(self._on_source_changed)
        src_grid.addWidget(self.source_cb, 0, 3)

        self.sub_lbl = QLabel("Video Track:")
        src_grid.addWidget(self.sub_lbl, 0, 4)

        self.track_cb = QComboBox()
        self.track_cb.currentIndexChanged.connect(self._scan_preview)
        src_grid.addWidget(self.track_cb, 0, 5)

        self.marker_color_cb = QComboBox()
        for col in self.MARKER_COLORS:
            self.marker_color_cb.addItem(col)
        self.marker_color_cb.currentIndexChanged.connect(self._scan_preview)
        self.marker_color_cb.setVisible(False)
        src_grid.addWidget(self.marker_color_cb, 0, 5)

        # Naming & defaults row
        src_grid.addWidget(QLabel("Sequence:"), 1, 0)
        self.seq_edit = QLineEdit(default_seq)
        self.seq_edit.textChanged.connect(self._scan_preview)
        src_grid.addWidget(self.seq_edit, 1, 1)

        src_grid.addWidget(QLabel("Start Shot #:"), 1, 2)
        self.start_spin = QSpinBox()
        self.start_spin.setRange(1, 99999)
        self.start_spin.setSingleStep(DEFAULT_SHOT_STEP)
        self.start_spin.setValue(int(default_shot))
        self.start_spin.valueChanged.connect(self._scan_preview)
        src_grid.addWidget(self.start_spin, 1, 3)

        src_grid.addWidget(QLabel("Step:"), 1, 4)
        self.step_spin = QSpinBox()
        self.step_spin.setRange(1, 1000)
        self.step_spin.setValue(DEFAULT_SHOT_STEP)
        self.step_spin.valueChanged.connect(self._scan_preview)
        src_grid.addWidget(self.step_spin, 1, 5)

        # Second defaults row
        src_grid.addWidget(QLabel("Handles:"), 2, 0)
        self.handles_spin = QSpinBox()
        self.handles_spin.setRange(0, 500)
        self.handles_spin.setValue(int(default_handles))
        src_grid.addWidget(self.handles_spin, 2, 1)

        src_grid.addWidget(QLabel("Status:"), 2, 2)
        self.status_cb = QComboBox()
        for st in _project_status_names(project):
            self.status_cb.addItem(st)
        src_grid.addWidget(self.status_cb, 2, 3)

        self.skip_existing_chk = QCheckBox("Skip clips already assigned")
        self.skip_existing_chk.setChecked(True)
        self.skip_existing_chk.toggled.connect(self._scan_preview)
        src_grid.addWidget(self.skip_existing_chk, 2, 4, 1, 2)

        layout.addWidget(src_card)

        # Selection toolbar
        sel_bar = QHBoxLayout()
        sel_bar.setSpacing(8)
        btn_all = QPushButton("Check All")
        btn_all.clicked.connect(lambda: self._set_all_checked(True))
        sel_bar.addWidget(btn_all)

        btn_none = QPushButton("Uncheck All")
        btn_none.clicked.connect(lambda: self._set_all_checked(False))
        sel_bar.addWidget(btn_none)

        self.sync_markers_chk = QCheckBox("Add / Update VFX Timeline Markers")
        self.sync_markers_chk.setChecked(True)
        sel_bar.addWidget(self.sync_markers_chk)

        marker_fields_btn = QPushButton("Fields...")
        marker_fields_btn.setToolTip("Choose which shot info is logged in Resolve timeline markers")
        marker_fields_btn.clicked.connect(
            lambda: ResolveMarkerFieldsDialog(self, project=self.project).exec_()
        )
        sel_bar.addWidget(marker_fields_btn)

        sel_bar.addStretch()
        self.summary_lbl = QLabel("")
        self.summary_lbl.setStyleSheet("color: #9B9A97;")
        sel_bar.addWidget(self.summary_lbl)
        layout.addLayout(sel_bar)

        # Preview table
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["Include", "Shot ID", "Clip Name", "Start TC", "Duration", "Notes / Marker", "Action"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self._on_table_item_changed)
        layout.addWidget(self.table, 1)

        # Footer buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        self.assign_btn = QPushButton("Assign Selected Shots")
        self.assign_btn.setObjectName("primary_btn")
        self.assign_btn.clicked.connect(self.accept)
        btn_box.addWidget(self.assign_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_box.addWidget(cancel_btn)
        layout.addLayout(btn_box)

        self._populate_timelines()

    def _populate_timelines(self):
        r_proj = get_current_project(self.resolve)
        self._timelines = list_timelines(r_proj) if r_proj else []
        curr_tl = r_proj.GetCurrentTimeline() if r_proj else None
        curr_name = curr_tl.GetName() if curr_tl else ""

        self.timeline_cb.blockSignals(True)
        self.timeline_cb.clear()
        curr_idx = 0
        for idx, tl in enumerate(self._timelines):
            t_name = tl.GetName() or f"Timeline {idx + 1}"
            self.timeline_cb.addItem(t_name, idx)
            if t_name == curr_name:
                curr_idx = idx
        self.timeline_cb.setCurrentIndex(curr_idx)
        self.timeline_cb.blockSignals(False)
        self._on_timeline_changed()

    def _active_timeline(self):
        idx = self.timeline_cb.currentIndex()
        if 0 <= idx < len(self._timelines):
            return self._timelines[idx]
        return None

    def _on_timeline_changed(self):
        tl = self._active_timeline()
        self.track_cb.blockSignals(True)
        self.track_cb.clear()
        if tl:
            t_count = int(tl.GetTrackCount("video") or 0)
            for trk in range(1, t_count + 1):
                t_name = tl.GetTrackName("video", trk) or f"Video {trk}"
                self.track_cb.addItem(f"V{trk} ({t_name})", trk)
        self.track_cb.blockSignals(False)
        self._scan_preview()

    def _on_source_changed(self):
        is_markers = (self.source_cb.currentData() or "track") == "markers"
        self.sub_lbl.setText("Marker Color:" if is_markers else "Video Track:")
        self.track_cb.setVisible(not is_markers)
        self.marker_color_cb.setVisible(is_markers)
        self._scan_preview()

    def _existing_shot_by_tc(self, start_tc: str) -> Optional[Shot]:
        if not self.vfx_project or not start_tc:
            return None
        for s in self.vfx_project.all_shots():
            if s.start_tc and s.start_tc == start_tc:
                return s
        return None

    def _scan_preview(self):
        tl = self._active_timeline()
        is_markers = (self.source_cb.currentData() or "track") == "markers"
        if not tl:
            self._rows = []
            self._render_table()
            return

        if is_markers:
            color_f = self.marker_color_cb.currentText()
            raw_items = collect_timeline_markers(tl, color_filter=color_f)
        else:
            trk = int(self.track_cb.currentData() or 1)
            raw_items = collect_timeline_clips(tl, trk)

        seq = self.seq_edit.text().strip() or DEFAULT_SEQ_PREFIX
        next_num = self.start_spin.value()
        step = self.step_spin.value()
        skip_existing = self.skip_existing_chk.isChecked()

        self._rows = []
        for item in raw_items:
            existing = self._existing_shot_by_tc(item["start_tc"])
            if existing:
                shot_id = existing.shot_id
                action = "Skip (Assigned)" if skip_existing else "Update"
                checked = not skip_existing
                shot_num = next_num
            else:
                shot_num = next_num
                shot_id = format_shot_id(seq, shot_num)
                while self.vfx_project and self.vfx_project.get_shot(shot_id):
                    shot_num += step
                    shot_id = format_shot_id(seq, shot_num)
                next_num = shot_num + step
                action = "New"
                checked = True

            note = item.get("marker_note") or ""
            self._rows.append(
                {
                    "checked": checked,
                    "shot_id": shot_id,
                    "seq": seq,
                    "shot_num": shot_num,
                    "clip_info": item,
                    "notes": note,
                    "action": action,
                }
            )
        self._render_table()

    def _render_table(self):
        self._updating_table = True
        try:
            self.table.setRowCount(len(self._rows))
            for r, row in enumerate(self._rows):
                info = row["clip_info"]

                chk_item = QTableWidgetItem()
                chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                chk_item.setCheckState(Qt.Checked if row["checked"] else Qt.Unchecked)
                self.table.setItem(r, 0, chk_item)

                id_item = QTableWidgetItem(row["shot_id"])
                id_item.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                id_item.setForeground(QColor("#4DAB9A"))
                self.table.setItem(r, 1, id_item)

                cname_item = QTableWidgetItem(info["clip_name"])
                cname_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(r, 2, cname_item)

                tc_item = QTableWidgetItem(info["start_tc"])
                tc_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(r, 3, tc_item)

                dur_item = QTableWidgetItem(f"{info['dur_f']}f")
                dur_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(r, 4, dur_item)

                note_item = QTableWidgetItem(row["notes"])
                note_item.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(r, 5, note_item)

                act_item = QTableWidgetItem(row["action"])
                act_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if row["action"] == "New":
                    act_item.setForeground(QColor("#4DAB9A"))
                elif row["action"] == "Update":
                    act_item.setForeground(QColor("#FFA344"))
                else:
                    act_item.setForeground(QColor("#9B9A97"))
                self.table.setItem(r, 6, act_item)

            self._update_summary()
        finally:
            self._updating_table = False

    def _on_table_item_changed(self, item: QTableWidgetItem):
        if self._updating_table:
            return
        r = item.row()
        c = item.column()
        if not (0 <= r < len(self._rows)):
            return
        if c == 0:
            self._rows[r]["checked"] = item.checkState() == Qt.Checked
            self._update_summary()
        elif c == 1:
            val = item.text().strip()
            if val:
                self._rows[r]["shot_id"] = val
        elif c == 5:
            self._rows[r]["notes"] = item.text().strip()

    def _set_all_checked(self, state: bool):
        self._updating_table = True
        try:
            for r, row in enumerate(self._rows):
                row["checked"] = state
                it = self.table.item(r, 0)
                if it:
                    it.setCheckState(Qt.Checked if state else Qt.Unchecked)
            self._update_summary()
        finally:
            self._updating_table = False

    def _update_summary(self):
        checked_n = sum(1 for r in self._rows if r["checked"])
        self.summary_lbl.setText(f"{checked_n} of {len(self._rows)} clip(s) selected to assign")
        self.assign_btn.setEnabled(checked_n > 0)

    def get_selected_assignments(self) -> List[Dict[str, Any]]:
        """Return normalized assignment records for all checked rows."""
        handles = self.handles_spin.value()
        status = self.status_cb.currentText()
        sync_marker = self.sync_markers_chk.isChecked()
        selected: List[Dict[str, Any]] = []
        for row in self._rows:
            if not row["checked"]:
                continue
            info = row["clip_info"]
            selected.append(
                {
                    "shot_id": row["shot_id"],
                    "seq": row["seq"],
                    "shot_num": row["shot_num"],
                    "clip_name": info["clip_name"],
                    "timeline": info["timeline"],
                    "start_tc": info["start_tc"],
                    "end_tc": info["end_tc"],
                    "dur_f": info["dur_f"],
                    "fps": info["fps"],
                    "handles": handles,
                    "status": status,
                    "notes": row["notes"],
                    "sync_marker": sync_marker,
                }
            )
        return selected


# =============================================================================
# 3. RESOLVE EXPORT DIALOG (PLATES & VFX COMPS)
# =============================================================================


class ResolveExportDialog(QDialog):
    """
    Export Plates (Individual Clips mode, soloed tracks) OR
    Render VFX Comps (Single Clip mode, all active tracks composited + scene backup)
    directly into <vfx_root>/<SEQ>/<SHOT>/media/<identifier>/<version>/.
    """

    def __init__(
        self,
        parent,
        hub,
        target_shots: List[Shot],
        scope_label: str = "All Shots",
        default_mode: str = "plate",
    ):
        super().__init__(parent)
        self.hub = hub
        self.shots = list(target_shots)
        self.scope_label = scope_label
        self.export_mode = "comp" if default_mode == "comp" else "plate"
        self.analyzed_data: List[Dict[str, Any]] = []
        self.shot_overrides: Dict[str, Dict[str, str]] = {}
        self.excluded_keys: Set[str] = set()
        self._updating_table = False

        self.setWindowIcon(get_app_icon())
        self.setStyleSheet(get_active_stylesheet())
        self.resize(1140, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.header_lbl = QLabel()
        self.header_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #FFA344;")
        layout.addWidget(self.header_lbl)

        # Top Settings Bar
        settings_bar = QHBoxLayout()
        settings_bar.setSpacing(10)

        settings_bar.addWidget(QLabel("Export Mode:"))
        self.mode_cb = QComboBox()
        self.mode_cb.addItem("Export Plates (Individual Clips — Solos Tracks)", "plate")
        self.mode_cb.addItem("Render as Comp (Single Clip — All Active Tracks Composited)", "comp")
        self.mode_cb.setCurrentIndex(1 if self.export_mode == "comp" else 0)
        self.mode_cb.currentIndexChanged.connect(self._on_mode_changed)
        settings_bar.addWidget(self.mode_cb)

        settings_bar.addWidget(QLabel("Render Preset:"))
        self.preset_cb = QComboBox()
        r_proj = get_current_project(self.hub.resolve)
        presets = r_proj.GetRenderPresetList() if r_proj else []
        for p in presets:
            self.preset_cb.addItem(p)
        idx = self.preset_cb.findText(DEFAULT_RENDER_PRESET)
        if idx >= 0:
            self.preset_cb.setCurrentIndex(idx)
        settings_bar.addWidget(self.preset_cb)

        self.auto_ver_chk = QCheckBox("Auto-Increment Version")
        self.auto_ver_chk.setChecked(True)
        self.auto_ver_chk.toggled.connect(self._on_auto_ver_toggled)
        settings_bar.addWidget(self.auto_ver_chk)

        self.overwrite_chk = QCheckBox("Overwrite Existing")
        settings_bar.addWidget(self.overwrite_chk)

        settings_bar.addStretch()
        layout.addLayout(settings_bar)

        # Comp-specific Options Bar
        self.comp_opts_bar = QFrame()
        self.comp_opts_bar.setObjectName("card_frame")
        comp_opts_layout = QHBoxLayout(self.comp_opts_bar)
        comp_opts_layout.setContentsMargins(10, 6, 10, 6)
        comp_opts_layout.setSpacing(16)

        comp_badge = QLabel("COMP RENDER OPTIONS:")
        comp_badge.setStyleSheet("color: #529CCA; font-weight: bold;")
        comp_opts_layout.addWidget(comp_badge)

        self.save_scene_chk = QCheckBox("Save Timeline (.drt) & Fusion (.comp) to scenes/Compositing/")
        self.save_scene_chk.setChecked(True)
        self.save_scene_chk.setToolTip(
            "Automatically backs up the Resolve Timeline (.drt) and Fusion comp (.comp) into scenes/Compositing/"
        )
        comp_opts_layout.addWidget(self.save_scene_chk)

        self.renumber_1001_chk = QCheckBox(f"Normalize Sequence Frames to {DEFAULT_START_FRAME}+")
        self.renumber_1001_chk.setChecked(True)
        self.renumber_1001_chk.setToolTip(
            "Renames rendered image sequence frames to start at 1001 (matching Nuke / DJV convention)"
        )
        comp_opts_layout.addWidget(self.renumber_1001_chk)

        self.comp_handles_chk = QCheckBox("Include +/- Handles in Comp")
        self.comp_handles_chk.setChecked(False)
        self.comp_handles_chk.toggled.connect(self._on_analyze_timeline)
        comp_opts_layout.addWidget(self.comp_handles_chk)

        comp_opts_layout.addStretch()
        layout.addWidget(self.comp_opts_bar)

        # Toolbar above table
        tbl_bar = QHBoxLayout()
        tbl_bar.setSpacing(8)

        self.analyze_btn = QPushButton("1. Analyze Timeline")
        self.analyze_btn.setObjectName("primary_btn")
        self.analyze_btn.clicked.connect(self._on_analyze_timeline)
        tbl_bar.addWidget(self.analyze_btn)

        self.remove_btn = QPushButton("Remove Selected from Batch")
        self.remove_btn.clicked.connect(self._on_remove_selected)
        tbl_bar.addWidget(self.remove_btn)

        tbl_bar.addStretch()

        self.count_lbl = QLabel(f"{len(self.shots)} shot(s) in batch")
        self.count_lbl.setStyleSheet("color: #9B9A97; font-weight: bold;")
        tbl_bar.addWidget(self.count_lbl)
        layout.addLayout(tbl_bar)

        # Preview table
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ["Shot ID", "Identifier", "Version", "Track", "Clip Name", "Cut Frames", "Handles", "Destination", "Status"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked | QAbstractItemView.EditKeyPressed
        )
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self._on_table_item_changed)
        layout.addWidget(self.table, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_lbl = QLabel(
            "Double-click Identifier or Version in any row to customize. Click '1. Analyze Timeline' to refresh clips."
        )
        self.status_lbl.setStyleSheet("color: #9B9A97;")
        layout.addWidget(self.status_lbl)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        self.start_btn = QPushButton("2. Start Export")
        self.start_btn.setObjectName("warn_btn")
        self.start_btn.clicked.connect(self._on_start_export)
        btn_box.addWidget(self.start_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_box.addWidget(close_btn)
        layout.addLayout(btn_box)

        self._apply_mode_ui()
        self._on_analyze_timeline()

    @property
    def is_comp_mode(self) -> bool:
        return (self.mode_cb.currentData() or self.export_mode) == "comp"

    def _on_mode_changed(self):
        self.export_mode = self.mode_cb.currentData() or "plate"
        self.shot_overrides.clear()
        self._apply_mode_ui()
        self._on_analyze_timeline()

    def _apply_mode_ui(self):
        if self.is_comp_mode:
            self.setWindowTitle(f"Render VFX Comp from Resolve — {self.scope_label}")
            self.header_lbl.setText(
                f"RENDER VFX COMP TO SHOT FOLDER ({self.scope_label.upper()} — {len(self.shots)} SHOTS | SINGLE CLIP COMPOSITE)"
            )
            self.header_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #529CCA;")
            self.start_btn.setText("2. Start Comp Render")
            self.start_btn.setObjectName("success_btn")
            self.comp_opts_bar.setVisible(True)
        else:
            self.setWindowTitle(f"Export Plates from Resolve — {self.scope_label}")
            self.header_lbl.setText(
                f"EXPORT PLATES FROM RESOLVE ({self.scope_label.upper()} — {len(self.shots)} SHOTS | INDIVIDUAL CLIPS)"
            )
            self.header_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #FFA344;")
            self.start_btn.setText("2. Start Plate Export")
            self.start_btn.setObjectName("warn_btn")
            self.comp_opts_bar.setVisible(False)
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete and self.table.hasFocus():
            self._on_remove_selected()
        else:
            super().keyPressEvent(event)

    @staticmethod
    def _get_default_identifier(clip_idx: int, total_clips: int) -> str:
        if total_clips <= 1:
            return DEFAULT_PLATE_IDENTIFIER
        if clip_idx == 0:
            return "bg"
        if clip_idx == 1:
            return "fg"
        return f"fg{clip_idx:02d}"

    def _on_auto_ver_toggled(self, _checked: bool):
        for key in self.shot_overrides:
            self.shot_overrides[key].pop("version", None)
        self._on_analyze_timeline()

    def _on_remove_selected(self):
        selected_rows = sorted({idx.row() for idx in self.table.selectionModel().selectedRows()}, reverse=True)
        if not selected_rows:
            return
        for r in selected_rows:
            if 0 <= r < len(self.analyzed_data):
                info = self.analyzed_data[r]
                self.excluded_keys.add(info["unique_key"])
                del self.analyzed_data[r]

        remaining_shot_ids = {c["shot"].shot_id for c in self.analyzed_data}
        self.shots = [s for s in self.shots if s.shot_id in remaining_shot_ids]
        self._render_table_from_analyzed_data()

    def _on_table_item_changed(self, item: QTableWidgetItem):
        if self._updating_table:
            return
        col = item.column()
        row = item.row()
        if col not in (1, 2) or not (0 <= row < len(self.analyzed_data)):
            return

        shot_info = self.analyzed_data[row]
        shot = shot_info["shot"]
        unique_key = shot_info["unique_key"]
        new_val = item.text().strip()

        if col == 1:
            eff_ident = new_val or shot_info["ident"] or DEFAULT_PLATE_IDENTIFIER
            shot_info["ident"] = eff_ident
            if self.auto_ver_chk.isChecked():
                eff_ver = self.hub.repo.get_next_media_version(self.hub.project, shot.shot_id, "media", eff_ident)
                shot_info["version"] = eff_ver
                self._updating_table = True
                ver_item = self.table.item(row, 2)
                if ver_item:
                    ver_item.setText(eff_ver)
                self._updating_table = False
            else:
                eff_ver = shot_info["version"]
        else:
            eff_ident = shot_info["ident"]
            eff_ver = new_val or DEFAULT_VERSION
            if not eff_ver.startswith("v"):
                eff_ver = f"v{eff_ver}"
            shot_info["version"] = eff_ver
            self._updating_table = True
            item.setText(eff_ver)
            self._updating_table = False

        self.shot_overrides.setdefault(unique_key, {})["ident"] = eff_ident
        self.shot_overrides[unique_key]["version"] = eff_ver

        out_dir, custom_prefix = self.hub.repo.get_plate_export_dir_and_name(
            self.hub.project, shot.shot_id, eff_ident, eff_ver
        )
        out_dir = out_dir or "-"
        custom_name = custom_prefix if custom_prefix else f"{shot.shot_id}_{eff_ident}_{eff_ver}"
        shot_info["out_dir"] = out_dir
        shot_info["custom_name"] = custom_name

        self._updating_table = True
        dest_item = self.table.item(row, 7)
        if dest_item:
            dest_item.setText(out_dir)
        self._updating_table = False

    def _on_analyze_timeline(self):
        r_proj = get_current_project(self.hub.resolve)
        curr_tl = r_proj.GetCurrentTimeline() if r_proj else None
        auto_inc = self.auto_ver_chk.isChecked()
        comp_mode = self.is_comp_mode

        self.analyzed_data.clear()

        for shot in self.shots:
            tl_name = self.hub.get_shot_timeline_name(shot)
            tl = (get_timeline_by_name(r_proj, tl_name) if r_proj else None) or curr_tl
            fps = get_timeline_fps(tl) if tl else (self.hub.project.fps if self.hub.project else 24.0)
            cut_start_f = timecode_to_frame(shot.start_tc, fps) if shot.start_tc else -1
            shot_clips = find_shot_clips_on_timeline(tl, shot, fps) if tl else []

            if comp_mode:
                _, primary_item = shot_clips[0] if shot_clips else (0, None)
                unique_key = f"{shot.shot_id}_COMP"
                if unique_key in self.excluded_keys:
                    continue

                s_ovr = self.shot_overrides.get(unique_key) or self.shot_overrides.get(shot.shot_id) or {}
                eff_ident = s_ovr.get("ident") or DEFAULT_COMP_IDENTIFIER
                if "version" in s_ovr:
                    eff_ver = s_ovr["version"]
                elif auto_inc:
                    eff_ver = self.hub.repo.get_next_media_version(self.hub.project, shot.shot_id, "media", eff_ident)
                else:
                    eff_ver = DEFAULT_VERSION

                out_dir, custom_prefix = self.hub.repo.get_plate_export_dir_and_name(
                    self.hub.project, shot.shot_id, eff_ident, eff_ver
                )
                out_dir = out_dir or "-"
                custom_name = custom_prefix if custom_prefix else f"{shot.shot_id}_{eff_ident}_{eff_ver}"

                has_valid_range = (primary_item is not None) or (cut_start_f >= 0 and shot.duration_int > 0)
                status = "Ready" if has_valid_range else "Clip Not Found"
                clip_name = (
                    f"[Timeline Composite: {len(shot_clips)} layer(s)]"
                    if len(shot_clips) > 1
                    else (primary_item.GetName() if primary_item else shot.name)
                )
                try:
                    dur_frames = int(primary_item.GetDuration()) if primary_item else (shot.duration_int or 0)
                except Exception:
                    dur_frames = shot.duration_int or 0
                dur_str = f"{dur_frames}f" if dur_frames else (shot.duration or "-")
                eff_handles = shot.handles_int if self.comp_handles_chk.isChecked() else 0

                self.analyzed_data.append(
                    {
                        "shot": shot,
                        "item": primary_item,
                        "all_items": [it for _, it in shot_clips],
                        "track": 0,
                        "unique_key": unique_key,
                        "timeline": tl,
                        "fps": fps,
                        "ident": eff_ident,
                        "version": eff_ver,
                        "out_dir": out_dir,
                        "custom_name": custom_name,
                        "handles": eff_handles,
                        "status": status,
                        "clip_name": clip_name,
                        "dur_str": dur_str,
                        "cut_start_f": int(primary_item.GetStart()) if primary_item else cut_start_f,
                        "cut_dur_f": dur_frames,
                    }
                )
            else:
                candidate_items = shot_clips if shot_clips else [(1, None)]
                total_found = len([c for c in candidate_items if c[1] is not None])

                for idx, (found_trk, found_item) in enumerate(candidate_items):
                    unique_key = f"{shot.shot_id}_V{found_trk}" if found_item else f"{shot.shot_id}_none"
                    if unique_key in self.excluded_keys:
                        continue

                    default_ident = self._get_default_identifier(idx, total_found)
                    s_ovr = self.shot_overrides.get(unique_key) or {}
                    if not s_ovr and total_found <= 1:
                        s_ovr = self.shot_overrides.get(shot.shot_id) or {}

                    eff_ident = s_ovr.get("ident") or default_ident
                    if "version" in s_ovr:
                        eff_ver = s_ovr["version"]
                    elif auto_inc:
                        eff_ver = self.hub.repo.get_next_media_version(
                            self.hub.project, shot.shot_id, "media", eff_ident
                        )
                    else:
                        eff_ver = DEFAULT_VERSION

                    out_dir, custom_prefix = self.hub.repo.get_plate_export_dir_and_name(
                        self.hub.project, shot.shot_id, eff_ident, eff_ver
                    )
                    out_dir = out_dir or "-"
                    custom_name = custom_prefix if custom_prefix else f"{shot.shot_id}_{eff_ident}_{eff_ver}"

                    status = "Ready" if found_item else "Clip Not Found"
                    clip_name = found_item.GetName() if found_item else shot.name
                    try:
                        dur_frames = int(found_item.GetDuration()) if found_item else (shot.duration_int or 0)
                    except Exception:
                        dur_frames = shot.duration_int or 0
                    dur_str = f"{dur_frames}f" if dur_frames else (shot.duration or "-")

                    self.analyzed_data.append(
                        {
                            "shot": shot,
                            "item": found_item,
                            "all_items": [found_item] if found_item else [],
                            "track": found_trk,
                            "unique_key": unique_key,
                            "timeline": tl,
                            "fps": fps,
                            "ident": eff_ident,
                            "version": eff_ver,
                            "out_dir": out_dir,
                            "custom_name": custom_name,
                            "handles": shot.handles_int,
                            "status": status,
                            "clip_name": clip_name,
                            "dur_str": dur_str,
                            "cut_start_f": int(found_item.GetStart()) if found_item else cut_start_f,
                            "cut_dur_f": dur_frames,
                        }
                    )

        self._render_table_from_analyzed_data()

    def _render_table_from_analyzed_data(self):
        self._updating_table = True
        try:
            self.table.setRowCount(len(self.analyzed_data))
            ready_count = 0
            for r, info in enumerate(self.analyzed_data):
                shot = info["shot"]
                found_item = info["item"]
                found_trk = info["track"]
                status = info["status"]
                if status == "Ready":
                    ready_count += 1

                if self.is_comp_mode:
                    trk_str = "ALL (Comp)" if status == "Ready" else "-"
                else:
                    trk_str = f"V{found_trk}" if found_item else "-"

                it_id = QTableWidgetItem(shot.shot_id)
                it_id.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                self.table.setItem(r, 0, it_id)

                it_ident = QTableWidgetItem(info["ident"])
                it_ident.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable)
                it_ident.setForeground(QColor("#529CCA"))
                it_ident.setToolTip("Double-click to edit Identifier")
                self.table.setItem(r, 1, it_ident)

                it_ver = QTableWidgetItem(info["version"])
                it_ver.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable)
                it_ver.setForeground(QColor("#FFA344"))
                it_ver.setToolTip("Double-click to edit Version")
                self.table.setItem(r, 2, it_ver)

                for c_idx, txt in (
                    (3, trk_str),
                    (4, info["clip_name"]),
                    (5, info["dur_str"]),
                    (6, f"+/- {info['handles']}f"),
                    (7, info["out_dir"]),
                ):
                    it = QTableWidgetItem(str(txt))
                    it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                    self.table.setItem(r, c_idx, it)

                st_item = QTableWidgetItem(status)
                st_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                st_item.setForeground(QColor("#4DAB9A") if status == "Ready" else QColor("#FF7369"))
                self.table.setItem(r, 8, st_item)

            item_word = "comp(s)" if self.is_comp_mode else "plate(s)"
            self.count_lbl.setText(f"{len(self.analyzed_data)} {item_word} in batch ({ready_count} ready)")
            self.status_lbl.setText(
                f"Analysis complete: {ready_count} of {len(self.analyzed_data)} {item_word} ready. "
                "Double-click Identifier or Version in any row to customize."
            )
        finally:
            self._updating_table = False

    def _on_start_export(self):
        if not self.analyzed_data:
            self._on_analyze_timeline()

        ready_items = [c for c in self.analyzed_data if c["status"] == "Ready"]
        if not ready_items:
            QMessageBox.warning(self, "Export Error", "No ready shots found on the active timeline to export.")
            return

        r_proj = get_current_project(self.hub.resolve)
        if not r_proj:
            QMessageBox.warning(self, "Export Error", "No active DaVinci Resolve project.")
            return

        preset = self.preset_cb.currentText().strip()
        comp_mode = self.is_comp_mode
        artist = load_user_settings().get("artist_name", "")

        self.start_btn.setEnabled(False)
        self.analyze_btn.setEnabled(False)
        self.remove_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(ready_items))
        self.progress_bar.setValue(0)

        by_tl = defaultdict(list)
        for c in ready_items:
            tl_name = c["timeline"].GetName() if c["timeline"] else "Default"
            by_tl[tl_name].append(c)

        rendered_count = 0
        cancel_batch = False

        for _tl_name, clips_to_render in by_tl.items():
            if cancel_batch:
                break
            tl = clips_to_render[0]["timeline"]
            if not tl:
                continue
            curr_tl = r_proj.GetCurrentTimeline()
            if not curr_tl or curr_tl.GetName() != tl.GetName():
                r_proj.SetCurrentTimeline(tl)

            track_count = int(tl.GetTrackCount("video") or 0)
            orig_track_states = {
                trk_idx: bool(tl.GetIsTrackEnabled("video", trk_idx))
                for trk_idx in range(1, track_count + 1)
            }

            try:
                for c in clips_to_render:
                    if cancel_batch:
                        break
                    s_rec = c["shot"]
                    self.hub.repo.ensure_shot(
                        self.hub.project,
                        s_rec.shot_id,
                        start_frame=DEFAULT_START_FRAME,
                        handles=s_rec.handles_int,
                    )

                    ok, msg = render_shot_job(
                        resolve=self.hub.resolve,
                        resolve_project=r_proj,
                        timeline=tl,
                        job_info=c,
                        comp_mode=comp_mode,
                        preset=preset,
                        track_count=track_count,
                        orig_track_states=orig_track_states,
                        on_tick=QApplication.processEvents,
                    )
                    if not ok:
                        QMessageBox.warning(self, "Render Failed", f"{msg}\nStopping batch.")
                        cancel_batch = True
                        break

                    if comp_mode:
                        normalize_comp_render_files(
                            c["out_dir"],
                            c["custom_name"],
                            start_frame=DEFAULT_START_FRAME,
                            renumber_sequence=self.renumber_1001_chk.isChecked(),
                        )
                        if self.save_scene_chk.isChecked():
                            save_resolve_comp_scenefiles(
                                resolve=self.hub.resolve,
                                repo=self.hub.repo,
                                vfx_project=self.hub.project,
                                shot=s_rec,
                                timeline=tl,
                                items_list=c.get("all_items", []),
                                version=c["version"],
                                artist=artist,
                            )
                        s_rec.status = "Review"
                    else:
                        normalize_plate_render_files(c["out_dir"], c["custom_name"])
                        s_rec.status = "WIP"

                    rendered_count += 1
                    self.progress_bar.setValue(rendered_count)
                    self.status_lbl.setText(
                        f"Exported {rendered_count}/{len(ready_items)}: {s_rec.shot_id} ({c['ident']} {c['version']})"
                    )
                    QApplication.processEvents()
            finally:
                for trk_idx, was_enabled in orig_track_states.items():
                    tl.SetTrackEnable("video", trk_idx, was_enabled)

            done_thumbs: Set[str] = set()
            for c in clips_to_render:
                s_rec = c["shot"]
                if s_rec.shot_id not in done_thumbs:
                    capture_shot_thumbnail_to_vfx(
                        self.hub.resolve,
                        self.hub.project,
                        s_rec,
                        timeline_name=self.hub.get_shot_timeline_name(s_rec),
                        resolve_project=r_proj,
                    )
                    done_thumbs.add(s_rec.shot_id)

        self.hub.refresh_table(scan_vfx=True)
        self.hub.save_session(write_excel=True)
        self.accept()


# =============================================================================
# 4. RESOLVE LOAD COMPS DIALOG
# =============================================================================


class ResolveLoadCompsDialog(QDialog):
    """Dialog for loading rendered VFX comps onto the DaVinci Resolve timeline with handle trimming."""

    def __init__(self, parent, hub, target_shots: List[Shot], scope_label: str = "All Shots"):
        super().__init__(parent)
        self.hub = hub
        self.shots = list(target_shots)
        self.scope_label = scope_label
        self._updating_combos = False

        self.setWindowTitle(f"Load Comps to Timeline — {scope_label}")
        self.setWindowIcon(get_app_icon())
        self.setStyleSheet(get_active_stylesheet())
        self.resize(1020, 620)

        self.hub.scan_vfx_for_shots()

        ident_options = [DEFAULT_COMP_IDENTIFIER]
        for p_state in self.hub.vfx_shot_state.values():
            for ident in p_state.get("renders_by_id", {}).keys():
                if ident not in ident_options:
                    ident_options.append(ident)
        self.ident_options = ident_options

        track_options = ["Auto Top Track (Create if needed)"]
        r_proj = get_current_project(self.hub.resolve)
        tl = r_proj.GetCurrentTimeline() if r_proj else None
        if tl:
            t_count = int(tl.GetTrackCount("video") or 0)
            for idx in range(t_count, 0, -1):
                t_name = tl.GetTrackName("video", idx) or f"Video {idx}"
                track_options.append(f"V{idx} ({t_name})")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header_lbl = QLabel(f"LOAD VFX COMPS TO TIMELINE ({scope_label.upper()} — {len(self.shots)} SHOTS)")
        header_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #529CCA;")
        layout.addWidget(header_lbl)

        # Top controls
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        top_bar.addWidget(QLabel("Default Identifier:"))
        self.default_ident_cb = QComboBox()
        for ident in ident_options:
            self.default_ident_cb.addItem(ident)
        top_bar.addWidget(self.default_ident_cb)

        apply_all_btn = QPushButton("Apply to All")
        apply_all_btn.clicked.connect(self._on_apply_ident_all)
        top_bar.addWidget(apply_all_btn)

        top_bar.addWidget(QLabel("Target Track:"))
        self.track_cb = QComboBox()
        for topt in track_options:
            self.track_cb.addItem(topt)
        top_bar.addWidget(self.track_cb)

        self.trim_chk = QCheckBox("Trim Handles on Timeline")
        self.trim_chk.setChecked(True)
        top_bar.addWidget(self.trim_chk)

        top_bar.addStretch()
        layout.addLayout(top_bar)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["Shot ID", "Clip Name", "Cut Frames", "Handles", "Identifier", "Timeline Ver", "Selected Version", "Render File"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._refresh_override_combos)
        layout.addWidget(self.table, 1)

        # Per-shot override bar
        override_box = QHBoxLayout()
        override_box.setSpacing(10)

        self.override_lbl = QLabel("Selected Shot Override:")
        self.override_lbl.setStyleSheet("font-weight: bold; color: #FFA344;")
        override_box.addWidget(self.override_lbl)

        override_box.addWidget(QLabel("Identifier:"))
        self.shot_ident_cb = QComboBox()
        self.shot_ident_cb.currentIndexChanged.connect(self._on_shot_ident_changed)
        override_box.addWidget(self.shot_ident_cb)

        override_box.addWidget(QLabel("Version:"))
        self.shot_ver_cb = QComboBox()
        self.shot_ver_cb.currentIndexChanged.connect(self._on_shot_ver_changed)
        override_box.addWidget(self.shot_ver_cb)

        override_box.addStretch()
        layout.addLayout(override_box)

        self.summary_lbl = QLabel("Select row(s) above to override Identifier or Version per shot.")
        self.summary_lbl.setStyleSheet("color: #9B9A97;")
        layout.addWidget(self.summary_lbl)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        self.load_btn = QPushButton("Load / Update Comps to Timeline")
        self.load_btn.setObjectName("success_btn")
        self.load_btn.clicked.connect(self._on_execute_load)
        btn_box.addWidget(self.load_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_box.addWidget(close_btn)
        layout.addLayout(btn_box)

        self._populate_table()
        self._refresh_override_combos()

    def _format_row_fields(self, shot: Shot) -> Tuple[str, str, str, str]:
        p_state = self.hub.vfx_shot_state.get(shot.shot_id, {})
        ident = p_state.get("identifier", DEFAULT_COMP_IDENTIFIER)
        tl_ver = p_state.get("tl_comp", "None") or "None"
        versions: List[MediaItem] = p_state.get("versions", [])
        sel_item: Optional[MediaItem] = p_state.get("selected_item")
        mode = p_state.get("mode", "latest")
        if sel_item and versions:
            latest_v = versions[-1].version
            if mode == "manual":
                sel_ver_str = f"{sel_item.version} (Selected, Latest: {latest_v})"
            else:
                sel_ver_str = f"Latest ({sel_item.version})"
            file_str = sel_item.display_file or os.path.basename(sel_item.first_file_path)
        else:
            sel_ver_str = "No Renders Found"
            file_str = "-"
        return ident, tl_ver, sel_ver_str, file_str

    def _populate_table(self):
        self.table.setRowCount(len(self.shots))
        ready_count = 0
        for r, shot in enumerate(self.shots):
            ident, tl_ver, sel_ver_str, file_str = self._format_row_fields(shot)
            if sel_ver_str != "No Renders Found":
                ready_count += 1
            for c_idx, val in enumerate(
                (shot.shot_id, shot.name, f"{shot.duration_int}f", f"+/- {shot.handles_int}f", ident, tl_ver, sel_ver_str, file_str)
            ):
                it = QTableWidgetItem(str(val))
                it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                self.table.setItem(r, c_idx, it)

        self.summary_lbl.setText(
            f"{ready_count} / {len(self.shots)} shot(s) have comp renders ready to load. "
            "Select row(s) above to override Identifier or Version per shot."
        )

    def _get_selected_rows(self) -> List[int]:
        return [idx.row() for idx in self.table.selectionModel().selectedRows()]

    def _refresh_override_combos(self):
        self._updating_combos = True
        try:
            self.shot_ident_cb.clear()
            self.shot_ver_cb.clear()

            rows = self._get_selected_rows()
            if not rows:
                if self.shots:
                    rows = [0]
                else:
                    return

            shot = self.shots[rows[0]]
            p_state = self.hub.vfx_shot_state.get(shot.shot_id, {})
            renders_by_id = p_state.get("renders_by_id", {})
            cur_ident = p_state.get("identifier", DEFAULT_COMP_IDENTIFIER)

            shot_idents = list(self.ident_options)
            for k in renders_by_id.keys():
                if k not in shot_idents:
                    shot_idents.append(k)

            for id_name in shot_idents:
                self.shot_ident_cb.addItem(id_name)
            idx = self.shot_ident_cb.findText(cur_ident)
            if idx >= 0:
                self.shot_ident_cb.setCurrentIndex(idx)

            versions: List[MediaItem] = p_state.get("versions", [])
            if not versions:
                self.shot_ver_cb.addItem("No Renders Found")
            else:
                latest_ver = versions[-1].version
                self.shot_ver_cb.addItem(f"Latest ({latest_ver})")
                for v in reversed(versions):
                    self.shot_ver_cb.addItem(v.version)

                cur_mode = p_state.get("mode", "latest")
                sel_item: Optional[MediaItem] = p_state.get("selected_item")
                if cur_mode == "manual" and sel_item:
                    v_idx = self.shot_ver_cb.findText(sel_item.version)
                    if v_idx >= 0:
                        self.shot_ver_cb.setCurrentIndex(v_idx)

            self.override_lbl.setText(
                f"Override for {shot.shot_id}:" if len(rows) == 1 else f"Override for {len(rows)} Selected Shots:"
            )
        finally:
            self._updating_combos = False

    def _apply_shot_ident_and_version(self, shot_id: str, chosen_ident: str, chosen_ver_text: Optional[str] = None):
        p_state = self.hub.vfx_shot_state.get(shot_id)
        if not p_state:
            return
        renders_by_id: Dict[str, List[MediaItem]] = p_state.get("renders_by_id", {})
        versions = renders_by_id.get(chosen_ident, [])
        p_state["identifier"] = chosen_ident
        p_state["versions"] = versions

        comp_json = self.hub.project.comp_versions if self.hub.project else {}
        shot_cfg = comp_json.setdefault(shot_id, {})
        shot_cfg["active_identifier"] = chosen_ident
        ident_cfg = shot_cfg.get(chosen_ident, {})

        if not versions:
            p_state["selected_item"] = None
            p_state["mode"] = "latest"
            p_state["comp_display"] = "-"
        else:
            latest_item = versions[-1]
            if chosen_ver_text is None:
                saved_mode = ident_cfg.get("mode", "latest")
                saved_ver = ident_cfg.get("published_version", "")
                if saved_mode == "manual" and saved_ver:
                    v_match = next((v for v in versions if v.version == saved_ver), None)
                    p_state["selected_item"] = v_match or latest_item
                    p_state["mode"] = "manual" if v_match else "latest"
                else:
                    p_state["selected_item"] = latest_item
                    p_state["mode"] = "latest"
            else:
                if chosen_ver_text.lower().startswith("latest"):
                    p_state["selected_item"] = latest_item
                    p_state["mode"] = "latest"
                else:
                    v_match = next((v for v in versions if v.version == chosen_ver_text), None)
                    p_state["selected_item"] = v_match or latest_item
                    p_state["mode"] = "manual" if v_match else "latest"

            sel_item: MediaItem = p_state["selected_item"]
            tl_ver = p_state.get("tl_comp", "")
            if tl_ver == "None":
                tl_ver = ""
            p_state["comp_display"] = self.hub.format_comp_display(
                sel_item.version, latest_item.version, tl_ver, p_state["mode"], chosen_ident
            )
            shot_cfg[chosen_ident] = {
                "mode": p_state["mode"],
                "published_version": sel_item.version,
                "latest_version": latest_item.version,
                "available_versions": [v.version for v in versions],
            }

        if self.hub.project:
            self.hub.repo.save(self.hub.project)

    def _update_table_rows(self, rows: List[int]):
        for r in rows:
            if 0 <= r < len(self.shots):
                ident, tl_ver, sel_ver_str, file_str = self._format_row_fields(self.shots[r])
                for c_idx, val in ((4, ident), (5, tl_ver), (6, sel_ver_str), (7, file_str)):
                    it = QTableWidgetItem(str(val))
                    it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                    self.table.setItem(r, c_idx, it)

    def _on_apply_ident_all(self):
        chosen_ident = self.default_ident_cb.currentText().strip() or DEFAULT_COMP_IDENTIFIER
        for shot in self.shots:
            self._apply_shot_ident_and_version(shot.shot_id, chosen_ident, None)
        self._populate_table()
        self._refresh_override_combos()
        self.hub.refresh_table(scan_vfx=False)

    def _on_shot_ident_changed(self):
        if self._updating_combos:
            return
        chosen_ident = self.shot_ident_cb.currentText().strip()
        if not chosen_ident:
            return
        rows = self._get_selected_rows() or ([0] if self.shots else [])
        for r in rows:
            self._apply_shot_ident_and_version(self.shots[r].shot_id, chosen_ident, None)
        self._update_table_rows(rows)
        self._refresh_override_combos()
        self.hub.refresh_table(scan_vfx=False)

    def _on_shot_ver_changed(self):
        if self._updating_combos:
            return
        chosen_ver_text = self.shot_ver_cb.currentText().strip()
        if not chosen_ver_text or chosen_ver_text == "No Renders Found":
            return
        rows = self._get_selected_rows() or ([0] if self.shots else [])
        for r in rows:
            s = self.shots[r]
            cur_ident = self.hub.vfx_shot_state.get(s.shot_id, {}).get("identifier", DEFAULT_COMP_IDENTIFIER)
            self._apply_shot_ident_and_version(s.shot_id, cur_ident, chosen_ver_text)
        self._update_table_rows(rows)
        self.hub.refresh_table(scan_vfx=False)

    def _on_execute_load(self):
        r_proj = get_current_project(self.hub.resolve)
        if not r_proj:
            QMessageBox.warning(self, "Load Error", "No active DaVinci Resolve project.")
            return

        trim_handles = self.trim_chk.isChecked()
        trk_choice = self.track_cb.currentText()
        loaded_count = 0

        for shot in self.shots:
            p_state = self.hub.vfx_shot_state.get(shot.shot_id)
            sel_item: Optional[MediaItem] = p_state.get("selected_item") if p_state else None
            if not sel_item:
                continue

            tl_name = self.hub.get_shot_timeline_name(shot)
            tl = get_timeline_by_name(r_proj, tl_name) or r_proj.GetCurrentTimeline()
            if not tl:
                continue

            track_count = int(tl.GetTrackCount("video") or 0)
            if trk_choice.startswith("V") and " " in trk_choice:
                target_trk = int(trk_choice.split(" ")[0][1:])
            else:
                if p_state.get("existing_trk", -1) > 1:
                    target_trk = p_state["existing_trk"]
                elif track_count >= 2:
                    target_trk = track_count
                else:
                    tl.AddTrack("video")
                    target_trk = int(tl.GetTrackCount("video") or 2)

            versions = p_state.get("versions", [])
            is_latest = bool(versions and sel_item.version == versions[-1].version)
            if load_media_item_to_timeline(
                resolve=self.hub.resolve,
                timeline=tl,
                shot=shot,
                media_item=sel_item,
                target_trk=target_trk,
                trim_handles=trim_handles,
                is_latest=is_latest,
            ):
                loaded_count += 1

        self.hub.refresh_table(scan_vfx=True)
        self.hub.statusBar().showMessage(f"Loaded {loaded_count} comp(s) onto Resolve timeline.")
        self.accept()
