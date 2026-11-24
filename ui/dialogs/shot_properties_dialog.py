"""Notion-style modal dialog for viewing & editing Shot properties, timing, and notes."""

import os
from typing import Callable, Dict, List, Tuple

from qtpy.QtCore import Qt
from qtpy.QtGui import QPixmap
from qtpy.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_HANDLES
from core.models import Project, Shot, ShotProperty, format_resolution
from core.storage import ProjectRepository
from core.thumbnails import generate_shot_thumbnail, set_shot_thumbnail_from_image
from ui.theme import get_active_stylesheet
from ui.widgets.property_widgets import create_property_editor
from ui.widgets.spec_widgets import FpsComboBox, ResolutionPickerWidget
from ui.widgets.thumbnail_worker import clear_thumbnail_pixmap_cache, render_thumbnail_pixmap


class ShotPropertiesDialog(QDialog):
    """
    Notion-Dark Shot Properties modal:
      - Header Card: 16:9 Thumbnail preview + Shot ID + live calculated duration/specs
      - Properties Card: Dynamic 2-column grid built from Project.shot_properties (typed widgets + colors)
      - Timing & Technical Card: Frame range, Handles, FPS, Resolution presets, Start/End TC
      - Notes Card: Multiline QPlainTextEdit
    """

    def __init__(self, parent, project: Project, shot: Shot):
        super().__init__(parent)
        self.project = project
        self.shot = shot
        self.schema_modified = False
        self.thumb_modified = False
        self._prop_getters: Dict[str, Callable[[], str]] = {}

        self.setWindowTitle(f"Shot Properties — {shot.shot_id}")
        self.setStyleSheet(get_active_stylesheet())
        self.resize(680, 720)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scrollable Body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body_widget = QWidget()
        body = QVBoxLayout(body_widget)
        body.setContentsMargins(16, 16, 16, 14)
        body.setSpacing(12)

        # ------------------------------------------------------------------
        # 1. Shot Header Card (Thumbnail + Shot ID + Live Summary)
        # ------------------------------------------------------------------
        header_card = QFrame()
        header_card.setObjectName("card_frame")
        h_lay = QHBoxLayout(header_card)
        h_lay.setContentsMargins(12, 10, 12, 10)
        h_lay.setSpacing(14)

        thumb_col = QVBoxLayout()
        thumb_col.setSpacing(4)
        self.thumb_lbl = QLabel()
        self.thumb_lbl.setFixedSize(128, 72)
        self.thumb_lbl.setAlignment(Qt.AlignCenter)
        self._load_thumbnail()
        thumb_col.addWidget(self.thumb_lbl)

        thumb_btn_row = QHBoxLayout()
        thumb_btn_row.setContentsMargins(0, 0, 0, 0)
        thumb_btn_row.setSpacing(4)
        btn_from_media = QPushButton("From Media")
        btn_from_media.setToolTip("Generate shot thumbnail from latest playable media")
        btn_from_media.setStyleSheet("padding: 2px 6px; font-size: 11px;")
        btn_from_media.clicked.connect(self._on_generate_thumb_from_media)
        thumb_btn_row.addWidget(btn_from_media)

        btn_browse_thumb = QPushButton("Browse...")
        btn_browse_thumb.setToolTip("Pick a custom thumbnail image file")
        btn_browse_thumb.setStyleSheet("padding: 2px 6px; font-size: 11px;")
        btn_browse_thumb.clicked.connect(self._on_browse_custom_thumb)
        thumb_btn_row.addWidget(btn_browse_thumb)
        thumb_col.addLayout(thumb_btn_row)
        h_lay.addLayout(thumb_col)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)

        id_row = QHBoxLayout()
        id_row.setSpacing(8)
        id_lbl = QLabel(shot.shot_id)
        id_lbl.setStyleSheet("font-size: 18px; font-weight: 700; color: #E3E2E0;")
        id_row.addWidget(id_lbl)

        if shot.seq:
            seq_pill = QLabel(shot.seq)
            seq_pill.setStyleSheet(
                "background-color: #262626; border: 1px solid #3A3A3A; border-radius: 4px; "
                "padding: 2px 7px; color: #9B9A97; font-size: 11px; font-weight: 600;"
            )
            id_row.addWidget(seq_pill)
        id_row.addStretch()
        title_col.addLayout(id_row)

        self.live_summary_lbl = QLabel("")
        self.live_summary_lbl.setStyleSheet("color: #9B9A97; font-size: 12px;")
        self.live_summary_lbl.setWordWrap(True)
        title_col.addWidget(self.live_summary_lbl)

        title_col.addStretch()
        h_lay.addLayout(title_col, 1)
        body.addWidget(header_card)

        # ------------------------------------------------------------------
        # 2. Unified Properties Card (Dynamic Typed Grid from Project Schema)
        # ------------------------------------------------------------------
        props_card = QFrame()
        props_card.setObjectName("card_frame")
        props_outer = QVBoxLayout(props_card)
        props_outer.setContentsMargins(12, 10, 12, 12)
        props_outer.setSpacing(8)

        props_hdr = QHBoxLayout()
        props_title = QLabel("Properties")
        props_title.setStyleSheet("font-size: 12px; font-weight: 600; color: #9B9A97;")
        props_hdr.addWidget(props_title)
        props_hdr.addStretch()

        manage_props_btn = QPushButton("Manage Properties...")
        manage_props_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #9B9A97; "
            "font-size: 11px; padding: 2px 6px; }"
            "QPushButton:hover { color: #E3E2E0; background-color: #2C2C2C; border-radius: 4px; }"
        )
        manage_props_btn.clicked.connect(self._on_manage_properties)
        props_hdr.addWidget(manage_props_btn)
        props_outer.addLayout(props_hdr)

        self._props_grid_host = QWidget()
        self._props_grid_host.setStyleSheet("background: transparent;")
        self._props_grid = QGridLayout(self._props_grid_host)
        self._props_grid.setContentsMargins(0, 0, 0, 0)
        self._props_grid.setHorizontalSpacing(12)
        self._props_grid.setVerticalSpacing(8)
        props_outer.addWidget(self._props_grid_host)

        self.clip_edit = QLineEdit(shot.name or "")
        self.clip_edit.setPlaceholderText(f"{shot.shot_id}_plate")
        self._rebuild_properties_grid()

        body.addWidget(props_card)

        # ------------------------------------------------------------------
        # 3. Timing & Technical Card
        # ------------------------------------------------------------------
        tech_card = QFrame()
        tech_card.setObjectName("card_frame")
        tech_outer = QVBoxLayout(tech_card)
        tech_outer.setContentsMargins(12, 10, 12, 12)
        tech_outer.setSpacing(8)

        tech_title = QLabel("Timing & Technical")
        tech_title.setStyleSheet("font-size: 12px; font-weight: 600; color: #9B9A97;")
        tech_outer.addWidget(tech_title)

        # Row 1: Start Frame | End Frame | Handles
        frame_row = QHBoxLayout()
        frame_row.setSpacing(10)

        sf_lbl = QLabel("Start Frame:")
        sf_lbl.setStyleSheet("color: #9B9A97;")
        self.sf_spin = QSpinBox()
        self.sf_spin.setRange(0, 999999)
        self.sf_spin.setValue(shot.start_frame)
        self.sf_spin.valueChanged.connect(self._update_live_summary)

        ef_lbl = QLabel("End Frame:")
        ef_lbl.setStyleSheet("color: #9B9A97;")
        self.ef_spin = QSpinBox()
        self.ef_spin.setRange(0, 999999)
        self.ef_spin.setValue(shot.end_frame)
        self.ef_spin.valueChanged.connect(self._update_live_summary)

        h_lbl = QLabel("Handles:")
        h_lbl.setStyleSheet("color: #9B9A97;")
        self.handles_spin = QSpinBox()
        self.handles_spin.setRange(0, 200)
        self.handles_spin.setSuffix("f")
        self.handles_spin.setValue(shot.handles_int if shot.handles else DEFAULT_HANDLES)
        self.handles_spin.valueChanged.connect(self._update_live_summary)

        frame_row.addWidget(sf_lbl)
        frame_row.addWidget(self.sf_spin, 1)
        frame_row.addWidget(ef_lbl)
        frame_row.addWidget(self.ef_spin, 1)
        frame_row.addWidget(h_lbl)
        frame_row.addWidget(self.handles_spin, 1)
        tech_outer.addLayout(frame_row)

        # Row 2, 3 & 4: Resolution / FPS + Start TC / End TC
        tech_grid = QGridLayout()
        tech_grid.setHorizontalSpacing(12)
        tech_grid.setVerticalSpacing(8)

        res_lbl = QLabel("Resolution:")
        res_lbl.setStyleSheet("color: #9B9A97;")
        self.res_picker = ResolutionPickerWidget(
            presets=project.resolution_presets,
            resolution=shot.resolution,
            project_default=project.resolution,
        )
        self.res_picker.resolution_changed.connect(self._update_live_summary)

        fps_lbl = QLabel("FPS:")
        fps_lbl.setStyleSheet("color: #9B9A97;")
        self.fps_combo = FpsComboBox(
            fps=shot.fps if shot.fps > 0 else 0.0,
            project_default_fps=float(project.fps or 24.0),
        )
        self.fps_combo.fps_changed.connect(self._update_live_summary)

        stc_lbl = QLabel("Start TC:")
        stc_lbl.setStyleSheet("color: #9B9A97;")
        self.start_tc_edit = QLineEdit(shot.start_tc or "")
        self.start_tc_edit.setPlaceholderText("01:00:00:00")

        etc_lbl = QLabel("End TC:")
        etc_lbl.setStyleSheet("color: #9B9A97;")
        self.end_tc_edit = QLineEdit(shot.end_tc or "")
        self.end_tc_edit.setPlaceholderText("01:00:02:17")

        tech_grid.addWidget(res_lbl, 0, 0)
        tech_grid.addWidget(self.res_picker, 0, 1, 1, 3)

        tech_grid.addWidget(fps_lbl, 1, 0)
        tech_grid.addWidget(self.fps_combo, 1, 1, 1, 3)

        tech_grid.addWidget(stc_lbl, 2, 0)
        tech_grid.addWidget(self.start_tc_edit, 2, 1)
        tech_grid.addWidget(etc_lbl, 2, 2)
        tech_grid.addWidget(self.end_tc_edit, 2, 3)

        tech_grid.setColumnStretch(1, 1)
        tech_grid.setColumnStretch(3, 1)
        tech_outer.addLayout(tech_grid)
        body.addWidget(tech_card)

        # ------------------------------------------------------------------
        # 4. Notes Card (Multiline)
        # ------------------------------------------------------------------
        notes_card = QFrame()
        notes_card.setObjectName("card_frame")
        notes_lay = QVBoxLayout(notes_card)
        notes_lay.setContentsMargins(12, 10, 12, 12)
        notes_lay.setSpacing(6)

        notes_title = QLabel("Notes")
        notes_title.setStyleSheet("font-size: 12px; font-weight: 600; color: #9B9A97;")
        notes_lay.addWidget(notes_title)

        self.notes_edit = QPlainTextEdit(shot.notes or "")
        self.notes_edit.setPlaceholderText("Add creative notes, client feedback, or technical instructions...")
        self.notes_edit.setMinimumHeight(82)
        self.notes_edit.setMaximumHeight(130)
        notes_lay.addWidget(self.notes_edit)
        body.addWidget(notes_card)

        body.addStretch()
        scroll.setWidget(body_widget)
        outer.addWidget(scroll, 1)

        # ------------------------------------------------------------------
        # Footer Bar (Matches Notion-style Settings Dialogs)
        # ------------------------------------------------------------------
        footer = QFrame()
        footer.setObjectName("settings_footer")
        f_lay = QHBoxLayout(footer)
        f_lay.setContentsMargins(16, 10, 16, 10)
        f_lay.setSpacing(8)
        f_lay.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        f_lay.addWidget(cancel_btn)

        save_btn = QPushButton("Save Shot")
        save_btn.setObjectName("primary_btn")
        save_btn.clicked.connect(self._on_accept)
        f_lay.addWidget(save_btn)
        outer.addWidget(footer)

        self._update_live_summary()

    # ------------------------------------------------------------------
    # Dynamic Properties Grid
    # ------------------------------------------------------------------
    def _rebuild_properties_grid(self, preserved_values: Dict[str, str] = None) -> None:
        preserved = preserved_values or {}
        # Clear existing grid widgets (keep self.clip_edit alive by detaching)
        while self._props_grid.count():
            item = self._props_grid.takeAt(0)
            w = item.widget()
            if w is not None and w is not self.clip_edit:
                w.deleteLater()

        self._prop_getters.clear()
        prop_items: List[Tuple[str, QWidget]] = []

        schema_props: List[ShotProperty] = list(self.project.shot_properties)

        inserted_clip = False
        for prop in schema_props:
            skey = prop.storage_key
            val = preserved.get(skey, self.shot.get_field(skey))
            widget, getter, _ = create_property_editor(prop, self.project, val, compact=False)
            self._prop_getters[skey] = getter
            prop_items.append((prop.name, widget))
            # Place Clip Name cleanly right after the built-in properties
            if prop.key == "assign" and not inserted_clip:
                prop_items.append(("Clip Name", self.clip_edit))
                inserted_clip = True

        if not inserted_clip:
            prop_items.append(("Clip Name", self.clip_edit))

        for idx, (label_text, widget) in enumerate(prop_items):
            r = idx // 2
            c = (idx % 2) * 2
            lbl = QLabel(f"{label_text}:")
            lbl.setStyleSheet("color: #9B9A97;")
            self._props_grid.addWidget(lbl, r, c)
            self._props_grid.addWidget(widget, r, c + 1)

        self._props_grid.setColumnStretch(1, 1)
        self._props_grid.setColumnStretch(3, 1)

    def _on_manage_properties(self) -> None:
        from ui.dialogs.project_settings_dialog import ProjectSettingsDialog

        snapshot = {skey: getter() for skey, getter in self._prop_getters.items()}
        dlg = ProjectSettingsDialog(self, self.project, initial_page="Shot Properties")
        if dlg.exec_() == QDialog.Accepted:
            self.schema_modified = True
            self._rebuild_properties_grid(preserved_values=snapshot)
            self.res_picker.set_presets(self.project.resolution_presets)
            self._update_live_summary()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _load_thumbnail(self) -> None:
        t_dir = self.project.thumbnails_dir if self.project else ""
        t_path = (
            self.shot.thumb
            if (self.shot.thumb and os.path.isfile(self.shot.thumb))
            else os.path.join(t_dir, f"{self.shot.shot_id}.png")
        )
        self.thumb_lbl.setPixmap(
            render_thumbnail_pixmap(t_path, 128, 72, placeholder_text="No Preview", radius=5.0)
        )

    def _on_generate_thumb_from_media(self) -> None:
        repo = ProjectRepository()
        out = generate_shot_thumbnail(self.project, repo, self.shot, force=True)
        if out:
            clear_thumbnail_pixmap_cache(out)
            self.thumb_modified = True
            self._load_thumbnail()
        else:
            QMessageBox.information(
                self,
                "No Playable Media",
                f"No playable media was found for '{self.shot.shot_id}' to generate a thumbnail.",
            )

    def _on_browse_custom_thumb(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Select Shot Thumbnail Image",
            self.project.vfx_root if self.project else "",
            "Images & Media (*.png *.jpg *.jpeg *.tif *.tiff *.exr *.dpx *.mov *.mp4);;All Files (*)",
        )
        if not chosen:
            return
        out = set_shot_thumbnail_from_image(self.project, self.shot, chosen)
        if out:
            clear_thumbnail_pixmap_cache(out)
            self.thumb_modified = True
            self._load_thumbnail()
        else:
            QMessageBox.warning(
                self,
                "Thumbnail Error",
                f"Could not import thumbnail from:\n{chosen}",
            )

    def _update_live_summary(self) -> None:
        sf = self.sf_spin.value()
        ef = max(sf, self.ef_spin.value())
        dur = ef - sf + 1
        fps_override = self.fps_combo.fps_value()
        eff_fps = fps_override if fps_override > 0 else float(self.project.fps or 24.0)
        sec = dur / max(1.0, eff_fps)
        handles = self.handles_spin.value()
        res_override = self.res_picker.resolution_value()
        res_disp = format_resolution(res_override) if res_override else self.project.resolution_str
        self.live_summary_lbl.setText(
            f"{sf}–{ef}  ·  {dur}f ({sec:.2f}s)  ·  +{handles}f handles  ·  {res_disp}  ·  {eff_fps:g} fps"
        )

    def _on_accept(self) -> None:
        sf = self.sf_spin.value()
        ef = max(sf, self.ef_spin.value())
        dur = ef - sf + 1
        fps_override = self.fps_combo.fps_value()
        eff_fps = fps_override if fps_override > 0 else float(self.project.fps or 24.0)

        for skey, getter in self._prop_getters.items():
            self.shot.set_field(skey, getter())

        self.shot.name = self.clip_edit.text().strip()
        self.shot.start_frame = sf
        self.shot.duration = f"{dur} ({dur / max(1.0, eff_fps):.2f}s)"
        self.shot.handles = str(self.handles_spin.value())
        self.shot.fps = round(fps_override, 3) if fps_override > 0 else 0.0
        self.shot.resolution = self.res_picker.resolution_value()
        self.shot.start_tc = self.start_tc_edit.text().strip()
        self.shot.end_tc = self.end_tc_edit.text().strip()
        self.shot.notes = self.notes_edit.toPlainText().strip()
        self.accept()
