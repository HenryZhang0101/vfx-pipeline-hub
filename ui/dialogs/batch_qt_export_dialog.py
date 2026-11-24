"""Batch & single-clip QuickTime Review (Slate + H.264) and Delivery (ProRes 4444) dialog."""

import os
from typing import Any, Dict, List

from qtpy.QtCore import Qt
from qtpy.QtGui import QColor, QFont, QPixmap
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_EXPORT_FOLDER, open_path_in_os
from ui.theme import get_active_stylesheet


class BatchQTExportDialog(QDialog):
    r"""
    Batch & Single Clip QuickTime Generator integrating `vfx_slate` and bundled FFmpeg.
    Supports:
      - QT Review (Frame 1000 VFX Slate + H.264 .mov/.mp4) -> `<vfx_root>/_export/<Shot ID>`
      - QT Delivery (Apple ProRes 4444 .mov) -> `<vfx_root>/_export/<Shot ID>`
      - Per-clip Output Filename renaming (editable table column + inspector field)
      - Batch Pattern Rename (`{shot_id}_{ident}_{version}`, custom prefix/suffix, find/replace)
      - Live Frame 1000 Slate preview
    """

    def __init__(
        self,
        parent,
        slate_tool,
        clips: List[Dict[str, Any]],
        project_root: str = "",
        initial_mode: str = "qt_review",
        export_folder: str = DEFAULT_EXPORT_FOLDER,
        project=None,
        repo=None,
    ):
        super().__init__(parent)
        self.slate_tool = slate_tool
        self.clips: List[Dict[str, Any]] = clips
        self.project = project
        self.repo = repo
        self.project_root = project.vfx_root if (project and not project_root) else project_root
        self.export_folder = (
            (project.export_folder if project else export_folder) or DEFAULT_EXPORT_FOLDER
        ).strip() or DEFAULT_EXPORT_FOLDER
        self._custom_out_dir_chosen = False
        self._updating_row = False
        self._preview_png_path = ""

        self.setWindowTitle(
            "Generate QT Review (Slate + H.264) / QT Delivery (ProRes 4444)"
        )
        self.resize(1180, 740)
        self.setStyleSheet(get_active_stylesheet())

        root_layout = QVBoxLayout(self)
        root_layout.setSpacing(10)

        # 1. EXPORT MODE & DESTINATION BAR
        top_grp = QGroupBox("1. Export Preset, Slate Options & Destination Folder")
        top_grid = QGridLayout(top_grp)

        top_grid.addWidget(QLabel("Export Preset:"), 0, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("🎬 QT Review — Frame 1000 VFX Slate + H.264 (.mov)", "qt_review")
        self.mode_combo.addItem("📦 QT Delivery — Apple ProRes 4444 10-bit (.mov)", "qt_delivery")
        idx = self.mode_combo.findData(initial_mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        top_grid.addWidget(self.mode_combo, 0, 1)

        self.include_slate_chk = QCheckBox("Prepend 1-Frame VFX Review Slate (Frame 1000)")
        self.include_slate_chk.setChecked(initial_mode == "qt_review")
        top_grid.addWidget(self.include_slate_chk, 0, 2)

        top_grid.addWidget(QLabel("Container:"), 0, 3)
        self.ext_combo = QComboBox()
        self.ext_combo.addItem(".mov (QuickTime)", ".mov")
        self.ext_combo.addItem(".mp4 (MPEG-4 H.264)", ".mp4")
        top_grid.addWidget(self.ext_combo, 0, 4)

        top_grid.addWidget(QLabel("Output Root:"), 1, 0)
        default_dir = self.slate_tool.get_default_export_dir(
            self.project_root,
            mode=initial_mode,
            export_folder=self.export_folder,
        )
        self.out_dir_edit = QLineEdit(default_dir)
        self.out_dir_edit.textChanged.connect(self._on_out_dir_edited)
        top_grid.addWidget(self.out_dir_edit, 1, 1)

        exp_basename = os.path.basename(os.path.normpath(default_dir)) or self.export_folder
        self.shot_subfolder_chk = QCheckBox(f"Export under Shot Name folder ({exp_basename}/<Shot ID>/)")
        self.shot_subfolder_chk.setChecked(True)
        top_grid.addWidget(self.shot_subfolder_chk, 1, 2)

        browse_out_btn = QPushButton("Browse Folder...")
        browse_out_btn.clicked.connect(self._on_browse_out_dir)
        top_grid.addWidget(browse_out_btn, 1, 3)

        open_out_btn = QPushButton("📂 Open Folder")
        open_out_btn.clicked.connect(self._on_open_out_dir)
        top_grid.addWidget(open_out_btn, 1, 4)

        root_layout.addWidget(top_grp)

        # 2. BATCH CLIP RENAMER BAR
        rename_grp = QGroupBox("2. Output Clip Renaming (Double-click 'Output Clip Name' in table or use Batch Pattern below)")
        rename_layout = QHBoxLayout(rename_grp)

        rename_layout.addWidget(QLabel("Naming Pattern:"))
        self.pattern_combo = QComboBox()
        self.pattern_combo.setEditable(True)
        self.pattern_combo.setMinimumWidth(260)
        for pat in [
            "{shot_id}_{ident}_{version}",
            "{shot_id}_{version}",
            "{shot_id}_{ident}_{version}_review",
            "{shot_id}_{ident}_{version}_delivery",
            "{shot_id}_{date}_{version}",
        ]:
            self.pattern_combo.addItem(pat)
        rename_layout.addWidget(self.pattern_combo)

        apply_pat_btn = QPushButton("Apply Pattern to All")
        apply_pat_btn.clicked.connect(self._on_apply_batch_pattern)
        rename_layout.addWidget(apply_pat_btn)

        rename_layout.addSpacing(12)
        rename_layout.addWidget(QLabel("Find:"))
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("e.g. comp")
        self.find_edit.setMaximumWidth(110)
        rename_layout.addWidget(self.find_edit)

        rename_layout.addWidget(QLabel("Replace:"))
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("e.g. final")
        self.replace_edit.setMaximumWidth(110)
        rename_layout.addWidget(self.replace_edit)

        replace_btn = QPushButton("Replace in Names")
        replace_btn.clicked.connect(self._on_apply_find_replace)
        rename_layout.addWidget(replace_btn)

        rename_layout.addStretch()
        add_file_btn = QPushButton("+ Add External Clip / Sequence...")
        add_file_btn.clicked.connect(self._on_add_external_clip)
        rename_layout.addWidget(add_file_btn)

        root_layout.addWidget(rename_grp)

        # 3. MAIN SPLITTER: CLIP TABLE (LEFT) + SLATE INSPECTOR & PREVIEW (RIGHT)
        mid_splitter = QSplitter(Qt.Horizontal)

        left_w = QWidget()
        left_l = QVBoxLayout(left_w)
        left_l.setContentsMargins(0, 0, 0, 0)

        self.clip_table = QTableWidget(0, 7)
        self.clip_table.setHorizontalHeaderLabels([
            "Output Clip Name (Editable)",
            "Shot ID",
            "Slate Ver",
            "Type",
            "Frames",
            "Res / FPS",
            "Status",
        ])
        self.clip_table.verticalHeader().setVisible(False)
        self.clip_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.clip_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.clip_table.setAlternatingRowColors(True)
        ch = self.clip_table.horizontalHeader()
        ch.setSectionResizeMode(0, QHeaderView.Stretch)
        ch.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        ch.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        ch.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        ch.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        ch.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        ch.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.clip_table.itemSelectionChanged.connect(self._on_clip_row_selected)
        self.clip_table.itemChanged.connect(self._on_table_item_edited)
        left_l.addWidget(self.clip_table, 1)

        mid_splitter.addWidget(left_w)

        right_w = QWidget()
        right_l = QVBoxLayout(right_w)
        right_l.setContentsMargins(0, 0, 0, 0)

        meta_grp = QGroupBox("Selected Clip — Rename & Slate Metadata")
        meta_grid = QGridLayout(meta_grp)

        meta_grid.addWidget(QLabel("Output Filename:"), 0, 0)
        self.ed_output_name = QLineEdit()
        self.ed_output_name.textEdited.connect(lambda val: self._update_selected_clip_field("output_name", val))
        meta_grid.addWidget(self.ed_output_name, 0, 1, 1, 3)

        meta_grid.addWidget(QLabel("Shot ID:"), 1, 0)
        self.ed_shot_id = QLineEdit()
        self.ed_shot_id.textEdited.connect(lambda val: self._update_selected_clip_field("shot_id", val))
        meta_grid.addWidget(self.ed_shot_id, 1, 1)

        meta_grid.addWidget(QLabel("Slate Version:"), 1, 2)
        self.ed_version = QLineEdit()
        self.ed_version.textEdited.connect(lambda val: self._update_selected_clip_field("version", val))
        meta_grid.addWidget(self.ed_version, 1, 3)

        meta_grid.addWidget(QLabel("Date:"), 2, 0)
        self.ed_date = QLineEdit()
        self.ed_date.textEdited.connect(lambda val: self._update_selected_clip_field("date", val))
        meta_grid.addWidget(self.ed_date, 2, 1)

        meta_grid.addWidget(QLabel("Frames Text:"), 2, 2)
        self.ed_frames = QLineEdit()
        self.ed_frames.textEdited.connect(lambda val: self._update_selected_clip_field("frames_display", val))
        meta_grid.addWidget(self.ed_frames, 2, 3)

        meta_grid.addWidget(QLabel("Color Space:"), 3, 0)
        self.ed_colorspace = QComboBox()
        self.ed_colorspace.setEditable(True)
        for cs in ["Rec.709 / 2.4", "ACEScg / Linear", "sRGB", "Rec.2020 / PQ", "Arri LogC3", "Arri LogC4"]:
            self.ed_colorspace.addItem(cs)
        self.ed_colorspace.currentTextChanged.connect(lambda val: self._update_selected_clip_field("color_space", val))
        meta_grid.addWidget(self.ed_colorspace, 3, 1, 1, 3)

        right_l.addWidget(meta_grp)

        prev_grp = QGroupBox("Frame 1000 VFX Slate Preview")
        prev_l = QVBoxLayout(prev_grp)

        self.preview_lbl = QLabel("Click 'Render Slate Preview' to preview Frame 1000")
        self.preview_lbl.setAlignment(Qt.AlignCenter)
        self.preview_lbl.setMinimumSize(420, 236)
        self.preview_lbl.setStyleSheet("background-color: #0d0f12; border: 1px solid #2c313a; border-radius: 4px; color: #7f848e;")
        prev_l.addWidget(self.preview_lbl, 1)

        prev_btn = QPushButton("🖼 Render Frame 1000 Slate Preview")
        prev_btn.clicked.connect(self._on_render_slate_preview)
        prev_l.addWidget(prev_btn)

        right_l.addWidget(prev_grp, 1)
        mid_splitter.addWidget(right_w)
        mid_splitter.setStretchFactor(0, 6)
        mid_splitter.setStretchFactor(1, 5)

        root_layout.addWidget(mid_splitter, 1)

        # 4. PROGRESS & BOTTOM BUTTONS
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        root_layout.addWidget(self.progress_bar)

        btn_row = QHBoxLayout()
        self.summary_lbl = QLabel("")
        self.summary_lbl.setStyleSheet("color: #98c379; font-weight: bold;")
        btn_row.addWidget(self.summary_lbl)
        btn_row.addStretch()

        self.export_btn = QPushButton("🚀 Generate QuickTime(s)")
        self.export_btn.setObjectName("success_btn")
        self.export_btn.setMinimumHeight(34)
        self.export_btn.clicked.connect(self._on_run_batch_export)
        btn_row.addWidget(self.export_btn)

        close_btn = QPushButton("Close")
        close_btn.setMinimumHeight(34)
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        root_layout.addLayout(btn_row)

        self._populate_table()
        self._update_mode_ui()

    def _on_out_dir_edited(self, text: str):
        clean = (text or "").strip().rstrip("\\/")
        exp_basename = os.path.basename(clean) if clean else self.export_folder
        if exp_basename:
            self.shot_subfolder_chk.setText(f"Export under Shot Name folder ({exp_basename}/<Shot ID>/)")

    def _persist_export_folder_if_inside_project(self, out_dir: str):
        """If the user customized the export folder inside the VFX project root, persist it on the Project."""
        if not self.project or not self.project.vfx_root or not out_dir:
            return
        vfx_norm = os.path.normcase(os.path.normpath(self.project.vfx_root))
        out_norm = os.path.normpath(out_dir)
        out_case = os.path.normcase(out_norm)
        new_export_folder = ""
        if out_case.startswith(vfx_norm + os.sep):
            rel = os.path.relpath(out_norm, self.project.vfx_root)
            if rel and rel != ".":
                new_export_folder = rel
        else:
            new_export_folder = out_norm
        if new_export_folder and new_export_folder != self.project.export_folder:
            self.project.export_folder = new_export_folder
            self.export_folder = new_export_folder
            if self.repo:
                self.repo.save(self.project)

    def _on_mode_changed(self):
        mode = self.mode_combo.currentData()
        self.include_slate_chk.setChecked(mode == "qt_review")
        if mode == "qt_delivery":
            self.ext_combo.setCurrentIndex(0)
            self.ext_combo.setEnabled(False)
        else:
            self.ext_combo.setEnabled(True)

        if not self._custom_out_dir_chosen:
            self.out_dir_edit.setText(
                self.slate_tool.get_default_export_dir(
                    self.project_root,
                    mode=mode,
                    export_folder=self.export_folder,
                )
            )
        self._update_mode_ui()

    def _update_mode_ui(self):
        mode = self.mode_combo.currentData()
        count = len(self.clips)
        if mode == "qt_delivery":
            self.export_btn.setText(f"📦 Generate {count} ProRes 4444 Delivery QT(s)")
            self.summary_lbl.setText(f"Mode: Apple ProRes 4444 (10-bit 4:4:4:4)  |  {count} clip(s) queued")
        else:
            self.export_btn.setText(f"🎬 Generate {count} H.264 Review QT(s)")
            self.summary_lbl.setText(f"Mode: H.264 Review + Frame 1000 VFX Slate  |  {count} clip(s) queued")

    def _on_browse_out_dir(self):
        chosen = QFileDialog.getExistingDirectory(self, "Select Output Directory", self.out_dir_edit.text())
        if chosen:
            self._custom_out_dir_chosen = True
            self.out_dir_edit.setText(os.path.normpath(chosen))

    def _on_open_out_dir(self):
        d = self.out_dir_edit.text().strip()
        if not d:
            return
        row = self.clip_table.currentRow()
        if self.shot_subfolder_chk.isChecked() and 0 <= row < len(self.clips):
            sid = self.clips[row].get("shot_id", "").strip()
            if sid and os.path.basename(os.path.normpath(d)).lower() != sid.lower():
                shot_d = os.path.join(d, sid)
                if os.path.isdir(shot_d):
                    open_path_in_os(shot_d)
                    return
        os.makedirs(d, exist_ok=True)
        open_path_in_os(d)

    def _populate_table(self):
        self._updating_row = True
        self.clip_table.setRowCount(0)
        for r_idx, c in enumerate(self.clips):
            self.clip_table.insertRow(r_idx)

            out_item = QTableWidgetItem(c.get("output_name", "output_clip"))
            out_item.setFont(QFont("Segoe UI", 10, QFont.Bold))
            out_item.setForeground(QColor("#98c379"))
            self.clip_table.setItem(r_idx, 0, out_item)

            sid_item = QTableWidgetItem(c.get("shot_id", ""))
            sid_item.setFlags(sid_item.flags() & ~Qt.ItemIsEditable)
            self.clip_table.setItem(r_idx, 1, sid_item)

            ver_item = QTableWidgetItem(c.get("version", ""))
            ver_item.setFlags(ver_item.flags() & ~Qt.ItemIsEditable)
            self.clip_table.setItem(r_idx, 2, ver_item)

            t_str = f"SEQ ({c.get('ext', '')})" if c.get("is_sequence") else f"MOV ({c.get('ext', '')})"
            t_item = QTableWidgetItem(t_str)
            t_item.setFlags(t_item.flags() & ~Qt.ItemIsEditable)
            self.clip_table.setItem(r_idx, 3, t_item)

            f_item = QTableWidgetItem(c.get("frames_display", ""))
            f_item.setFlags(f_item.flags() & ~Qt.ItemIsEditable)
            self.clip_table.setItem(r_idx, 4, f_item)

            rf_item = QTableWidgetItem(f"{c.get('resolution', '')} @ {c.get('fps_display', '')}")
            rf_item.setFlags(rf_item.flags() & ~Qt.ItemIsEditable)
            self.clip_table.setItem(r_idx, 5, rf_item)

            st_item = QTableWidgetItem("Queued")
            st_item.setFlags(st_item.flags() & ~Qt.ItemIsEditable)
            st_item.setForeground(QColor("#61afef"))
            self.clip_table.setItem(r_idx, 6, st_item)

        self._updating_row = False
        if self.clips:
            self.clip_table.selectRow(0)
        self._update_mode_ui()

    def _on_clip_row_selected(self):
        row = self.clip_table.currentRow()
        if row < 0 or row >= len(self.clips):
            return
        c = self.clips[row]
        self._updating_row = True
        self.ed_output_name.setText(c.get("output_name", ""))
        self.ed_shot_id.setText(c.get("shot_id", ""))
        self.ed_version.setText(c.get("version", ""))
        self.ed_date.setText(c.get("date", ""))
        self.ed_frames.setText(c.get("frames_display", ""))
        self.ed_colorspace.setCurrentText(c.get("color_space", "Rec.709 / 2.4"))
        self._updating_row = False

    def _on_table_item_edited(self, item: QTableWidgetItem):
        if self._updating_row:
            return
        row = item.row()
        if row < 0 or row >= len(self.clips):
            return
        if item.column() == 0:
            new_name = os.path.splitext(item.text().strip())[0] or "clip"
            self.clips[row]["output_name"] = new_name
            if row == self.clip_table.currentRow():
                self._updating_row = True
                self.ed_output_name.setText(new_name)
                self._updating_row = False

    def _update_selected_clip_field(self, key: str, value: str):
        if self._updating_row:
            return
        row = self.clip_table.currentRow()
        if row < 0 or row >= len(self.clips):
            return
        val = value.strip()
        if key == "output_name":
            val = os.path.splitext(val)[0]
        self.clips[row][key] = val

        self._updating_row = True
        if key == "output_name" and self.clip_table.item(row, 0):
            self.clip_table.item(row, 0).setText(val)
        elif key == "shot_id" and self.clip_table.item(row, 1):
            self.clip_table.item(row, 1).setText(val)
        elif key == "version" and self.clip_table.item(row, 2):
            self.clip_table.item(row, 2).setText(val)
        elif key == "frames_display" and self.clip_table.item(row, 4):
            self.clip_table.item(row, 4).setText(val)
        self._updating_row = False

    def _on_apply_batch_pattern(self):
        pat = self.pattern_combo.currentText().strip()
        if not pat:
            return
        self._updating_row = True
        for r_idx, c in enumerate(self.clips):
            rendered_name = (
                pat.replace("{shot_id}", c.get("shot_id", "SHOT"))
                .replace("{ident}", c.get("identifier", "comp"))
                .replace("{version}", c.get("stem_version") or c.get("version", "v0001"))
                .replace("{date}", c.get("date", ""))
            )
            rendered_name = os.path.splitext(rendered_name)[0]
            c["output_name"] = rendered_name
            if self.clip_table.item(r_idx, 0):
                self.clip_table.item(r_idx, 0).setText(rendered_name)
        self._updating_row = False
        self._on_clip_row_selected()

    def _on_apply_find_replace(self):
        f_txt = self.find_edit.text()
        r_txt = self.replace_edit.text()
        if not f_txt:
            return
        self._updating_row = True
        for r_idx, c in enumerate(self.clips):
            new_n = c.get("output_name", "").replace(f_txt, r_txt)
            c["output_name"] = new_n
            if self.clip_table.item(r_idx, 0):
                self.clip_table.item(r_idx, 0).setText(new_n)
        self._updating_row = False
        self._on_clip_row_selected()

    def _on_add_external_clip(self):
        chosen, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Video File(s) or First Frame of Image Sequence(s)",
            "",
            "Media Files (*.mov *.mp4 *.mxf *.exr *.dpx *.png *.tif *.tiff *.jpg);;All Files (*.*)",
        )
        if not chosen:
            return
        for path in chosen:
            info = self.slate_tool.inspect_clip_source(path)
            self.clips.append(info)
        self._populate_table()

    def _on_render_slate_preview(self):
        row = self.clip_table.currentRow()
        if row < 0 or row >= len(self.clips):
            return
        clip = self.clips[row]
        self.preview_lbl.setText("Rendering Frame 1000 Slate preview...")
        QApplication.processEvents()
        try:
            import tempfile
            out_png = os.path.join(tempfile.gettempdir(), f"vfx_slate_ui_preview_{row}.png")
            self.slate_tool.generate_slate_preview_png(clip, out_png)
            self._preview_png_path = out_png
            pix = QPixmap(out_png)
            if not pix.isNull():
                scaled = pix.scaled(
                    self.preview_lbl.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                self.preview_lbl.setPixmap(scaled)
        except Exception as e:
            self.preview_lbl.setText(f"Preview Error:\n{e}")

    def _on_run_batch_export(self):
        if not self.clips:
            return
        mode = self.mode_combo.currentData() or "qt_review"
        include_slate = self.include_slate_chk.isChecked()
        ext = ".mov" if mode == "qt_delivery" else (self.ext_combo.currentData() or ".mov")
        out_dir = self.out_dir_edit.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "Output Folder", "Please specify an output folder.")
            return

        os.makedirs(out_dir, exist_ok=True)
        self._persist_export_folder_if_inside_project(out_dir)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(self.clips))
        self.progress_bar.setValue(0)
        self.export_btn.setEnabled(False)

        success_count = 0
        errors: List[str] = []
        exported_dirs: List[str] = []

        for idx, clip in enumerate(self.clips):
            st_item = self.clip_table.item(idx, 6)
            if st_item:
                st_item.setText("⏳ Encoding...")
                st_item.setForeground(QColor("#e5c07b"))
            self.clip_table.selectRow(idx)
            QApplication.processEvents()

            shot_id_folder = clip.get("shot_id", "").strip() or "SHOT"
            if self.shot_subfolder_chk.isChecked() and os.path.basename(os.path.normpath(out_dir)).lower() != shot_id_folder.lower():
                clip_out_dir = os.path.join(out_dir, shot_id_folder)
            else:
                clip_out_dir = out_dir
            os.makedirs(clip_out_dir, exist_ok=True)

            stem = clip.get("output_name", "").strip() or f"{shot_id_folder}_{clip.get('version', 'v0001')}"
            stem = os.path.splitext(stem)[0]
            out_path = os.path.join(clip_out_dir, f"{stem}{ext}")

            ok, msg = self.slate_tool.export_clip_qt(
                clip_info=clip,
                output_path=out_path,
                mode=mode,
                include_slate=include_slate,
            )
            if ok:
                success_count += 1
                if clip_out_dir not in exported_dirs:
                    exported_dirs.append(clip_out_dir)
                if st_item:
                    st_item.setText("✅ Done")
                    st_item.setForeground(QColor("#98c379"))
            else:
                errors.append(f"{stem}: {msg}")
                if st_item:
                    st_item.setText("❌ Failed")
                    st_item.setForeground(QColor("#e06c75"))

            self.progress_bar.setValue(idx + 1)
            QApplication.processEvents()

        self.export_btn.setEnabled(True)
        open_target_dir = exported_dirs[0] if len(exported_dirs) == 1 else out_dir
        if errors:
            QMessageBox.warning(
                self,
                "Batch Export Completed with Errors",
                f"Exported {success_count}/{len(self.clips)} clip(s) to:\n{open_target_dir}\n\nErrors:\n" + "\n\n".join(errors[:3]),
            )
        else:
            reply = QMessageBox.information(
                self,
                "QuickTime Generation Complete",
                f"Successfully generated {success_count} QuickTime file(s) in:\n{open_target_dir}\n\nOpen output folder now?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                open_path_in_os(open_target_dir)
