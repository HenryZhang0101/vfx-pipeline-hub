"""Per-Project Settings dialog (General, Shot Defaults, Shot Properties, Artists, Cloud Sync)."""

import os
from typing import List, Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_EXPORT_FOLDER, PIPELINE_OCIO_DIR, get_ocio_config, open_path_in_os
from core.models import (
    Artist,
    Project,
    PropertyOption,
    PropertyType,
    ResolutionPreset,
    ShotProperty,
    default_resolution_presets,
    pick_palette_color,
    slugify_property_key,
)
from ui.dialogs.settings_base import SidebarSettingsDialog
from ui.widgets.property_widgets import ColorSwatchButton
from ui.widgets.spec_widgets import FpsComboBox, ResolutionPickerWidget


class ProjectSettingsDialog(SidebarSettingsDialog):
    """Per-project configuration saved to <vfx_root>/project.json."""

    def __init__(self, parent, project: Project, initial_page: str = ""):
        super().__init__(
            parent=parent,
            title=f"Project Settings — {project.name}",
            save_label="Save Project Settings",
            width=860,
            height=560,
        )
        self.project = project
        # Working copies so Cancel never mutates live project state
        self._props: List[ShotProperty] = [ShotProperty.from_dict(p.to_dict()) for p in project.shot_properties]
        # Track original names of existing custom properties to migrate renamed keys cleanly
        for orig_p, work_p in zip(project.shot_properties, self._props):
            setattr(work_p, "_orig_name", orig_p.name)
        self._artists: List[Artist] = [Artist.from_dict(a.to_dict()) for a in project.list_artists()]
        self._res_presets: List[ResolutionPreset] = [
            ResolutionPreset(p.name, p.width, p.height)
            for p in (project.resolution_presets or default_resolution_presets())
        ]
        self._loading_prop_inspector = False
        self._loading_artists_table = False

        self._build_general_page()
        self._build_defaults_page()
        self._build_shot_properties_page()
        self._build_artists_page()
        self._build_cloud_sync_page()

        if initial_page:
            target = "Shot Properties" if initial_page.strip().lower() == "custom fields" else initial_page
            self.select_page(target)

    @staticmethod
    def _wrap_centered(widget: QWidget, h_pad: int = 0, v_pad: int = 0) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(h_pad, v_pad, h_pad, v_pad)
        lay.setAlignment(Qt.AlignCenter)
        lay.addWidget(widget)
        return wrap

    # ------------------------------------------------------------------
    # Page 1: General
    # ------------------------------------------------------------------
    def _build_general_page(self) -> None:
        _, lay = self.add_page(
            "General",
            "Core project identity, frame rate, and default delivery resolution.",
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        grid.addWidget(QLabel("Project Name:"), 0, 0)
        self.name_edit = QLineEdit(self.project.name)
        grid.addWidget(self.name_edit, 0, 1, 1, 2)

        grid.addWidget(QLabel("VFX Project Root:"), 1, 0)
        self.root_edit = QLineEdit(os.path.normpath(self.project.vfx_root))
        self.root_edit.setReadOnly(True)
        self.root_edit.setStyleSheet("color: #9B9A97;")
        grid.addWidget(self.root_edit, 1, 1)
        open_btn = QPushButton("Open Folder")
        open_btn.clicked.connect(lambda: open_path_in_os(self.project.vfx_root))
        grid.addWidget(open_btn, 1, 2)

        grid.addWidget(QLabel("Client Export Folder:"), 2, 0)
        self.export_folder_edit = QLineEdit(self.project.export_folder or DEFAULT_EXPORT_FOLDER)
        self.export_folder_edit.setPlaceholderText(f"Subfolder inside VFX Root (default: {DEFAULT_EXPORT_FOLDER})")
        grid.addWidget(self.export_folder_edit, 2, 1)
        open_exp_btn = QPushButton("Open Folder")
        open_exp_btn.clicked.connect(self._on_open_exports_folder)
        grid.addWidget(open_exp_btn, 2, 2)

        grid.addWidget(QLabel("FPS:"), 3, 0)
        self.fps_combo = FpsComboBox(fps=float(self.project.fps or 24.0))
        grid.addWidget(self.fps_combo, 3, 1, 1, 2)

        grid.addWidget(QLabel("Resolution:"), 4, 0)
        self.res_picker = ResolutionPickerWidget(
            presets=self._res_presets,
            resolution=self.project.resolution,
        )
        grid.addWidget(self.res_picker, 4, 1, 1, 2)

        grid.addWidget(QLabel("OCIO Config:"), 5, 0)
        self.ocio_edit = QLineEdit(self.project.ocio_config or "")
        self.ocio_edit.setPlaceholderText("Blank = use pipeline default (ACES 1.3 Studio)")
        grid.addWidget(self.ocio_edit, 5, 1)
        ocio_btns = QHBoxLayout()
        ocio_btns.setSpacing(4)
        browse_ocio_btn = QPushButton("Browse...")
        browse_ocio_btn.clicked.connect(self._on_browse_ocio_config)
        ocio_btns.addWidget(browse_ocio_btn)
        clear_ocio_btn = QPushButton("Reset")
        clear_ocio_btn.clicked.connect(lambda: self.ocio_edit.clear())
        ocio_btns.addWidget(clear_ocio_btn)
        grid.addLayout(ocio_btns, 5, 2)

        eff_ocio = get_ocio_config(self.project.ocio_config)
        ocio_hint = QLabel(f"Active: {os.path.basename(eff_ocio) if eff_ocio else 'None'}")
        ocio_hint.setToolTip(eff_ocio or "")
        ocio_hint.setStyleSheet("color: #9B9A97; font-size: 11px;")
        grid.addWidget(ocio_hint, 6, 1, 1, 2)

        lay.addLayout(grid)

        # Resolution Presets Manager
        presets_hdr = QHBoxLayout()
        presets_lbl = QLabel("Resolution Presets")
        presets_lbl.setStyleSheet("color: #9B9A97; font-weight: 600; margin-top: 6px;")
        presets_hdr.addWidget(presets_lbl)
        presets_hdr.addStretch()

        add_preset_btn = QPushButton("+ Add Preset")
        add_preset_btn.setObjectName("accent_btn")
        add_preset_btn.clicked.connect(self._on_add_res_preset)
        presets_hdr.addWidget(add_preset_btn)
        lay.addLayout(presets_hdr)

        self.res_presets_table = QTableWidget(0, 4)
        self.res_presets_table.setHorizontalHeaderLabels(["Preset Name", "Width", "Height", ""])
        self.res_presets_table.verticalHeader().setVisible(False)
        self.res_presets_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.res_presets_table.setSelectionMode(QAbstractItemView.SingleSelection)
        rh = self.res_presets_table.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.Stretch)
        rh.setSectionResizeMode(1, QHeaderView.Fixed)
        self.res_presets_table.setColumnWidth(1, 110)
        rh.setSectionResizeMode(2, QHeaderView.Fixed)
        self.res_presets_table.setColumnWidth(2, 110)
        rh.setSectionResizeMode(3, QHeaderView.Fixed)
        self.res_presets_table.setColumnWidth(3, 52)
        lay.addWidget(self.res_presets_table, 1)

        self._populate_res_presets_table()

    def _populate_res_presets_table(self) -> None:
        self.res_presets_table.setRowCount(0)
        for idx, pr in enumerate(self._res_presets):
            self.res_presets_table.insertRow(idx)
            self.res_presets_table.setRowHeight(idx, 34)

            name_edit = QLineEdit(pr.name)
            name_edit.setStyleSheet("border: none; background: transparent; padding: 2px 6px;")
            name_edit.textChanged.connect(lambda txt, p=pr: self._on_res_preset_edited(p, name=txt))
            self.res_presets_table.setCellWidget(idx, 0, name_edit)

            w_spin = QDoubleSpinBox()
            w_spin.setRange(1.0, 65536.0)
            w_spin.setDecimals(2)
            w_spin.setValue(float(pr.width))
            w_spin.setStyleSheet("border: none; background: transparent;")
            w_spin.valueChanged.connect(lambda val, p=pr: self._on_res_preset_edited(p, width=val))
            self.res_presets_table.setCellWidget(idx, 1, w_spin)

            h_spin = QDoubleSpinBox()
            h_spin.setRange(1.0, 65536.0)
            h_spin.setDecimals(2)
            h_spin.setValue(float(pr.height))
            h_spin.setStyleSheet("border: none; background: transparent;")
            h_spin.valueChanged.connect(lambda val, p=pr: self._on_res_preset_edited(p, height=val))
            self.res_presets_table.setCellWidget(idx, 2, h_spin)

            del_btn = QPushButton("✕")
            del_btn.setToolTip("Delete resolution preset")
            del_btn.setStyleSheet("padding: 2px 8px; color: #9B9A97;")
            del_btn.clicked.connect(lambda _=False, i=idx: self._on_delete_res_preset(i))
            self.res_presets_table.setCellWidget(idx, 3, del_btn)

    def _on_res_preset_edited(
        self,
        preset: ResolutionPreset,
        name: Optional[str] = None,
        width: Optional[float] = None,
        height: Optional[float] = None,
    ) -> None:
        if name is not None:
            preset.name = name.strip() or preset.name
        if width is not None:
            preset.width = round(float(width), 4)
        if height is not None:
            preset.height = round(float(height), 4)
        self.res_picker.set_presets(self._res_presets)

    def _on_add_res_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Resolution Preset", "Preset Name (e.g. Anamorphic 2.8K):")
        name = (name or "").strip()
        if not ok or not name:
            return
        cur_w, cur_h = self.res_picker.resolution_value() or self.project.resolution
        self._res_presets.append(ResolutionPreset(name=name, width=cur_w, height=cur_h))
        self._populate_res_presets_table()
        self.res_picker.set_presets(self._res_presets)
        self.res_presets_table.selectRow(len(self._res_presets) - 1)

    def _on_delete_res_preset(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._res_presets):
            return
        if len(self._res_presets) <= 1:
            QMessageBox.information(self, "Preset Required", "At least one resolution preset must remain.")
            return
        self._res_presets.pop(idx)
        self._populate_res_presets_table()
        self.res_picker.set_presets(self._res_presets)

    def _on_open_exports_folder(self) -> None:
        folder = (self.export_folder_edit.text().strip() or DEFAULT_EXPORT_FOLDER)
        exp_dir = folder if os.path.isabs(folder) else os.path.join(self.project.vfx_root, folder)
        os.makedirs(exp_dir, exist_ok=True)
        open_path_in_os(exp_dir)

    def _on_browse_ocio_config(self) -> None:
        start = self.ocio_edit.text().strip() or PIPELINE_OCIO_DIR
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Select Project OCIO Config",
            start,
            "OCIO Configs (*.ocio);;All Files (*.*)",
        )
        if chosen:
            self.ocio_edit.setText(os.path.normpath(chosen))

    # ------------------------------------------------------------------
    # Page 2: Shot Defaults
    # ------------------------------------------------------------------
    def _build_defaults_page(self) -> None:
        _, lay = self.add_page(
            "Shot Defaults",
            "Default frame range and handles applied when creating new shots in this project.",
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        grid.addWidget(QLabel("Default Start Frame:"), 0, 0)
        self.start_frame_spin = QSpinBox()
        self.start_frame_spin.setRange(0, 999999)
        self.start_frame_spin.setValue(int(self.project.default_start_frame))
        grid.addWidget(self.start_frame_spin, 0, 1)

        grid.addWidget(QLabel("Default Handles:"), 1, 0)
        self.handles_spin = QSpinBox()
        self.handles_spin.setRange(0, 200)
        self.handles_spin.setSuffix("f")
        self.handles_spin.setValue(int(self.project.default_handles))
        grid.addWidget(self.handles_spin, 1, 1)

        lay.addLayout(grid)
        lay.addStretch()

    # ------------------------------------------------------------------
    # Page 3: Shot Properties (Typed Notion-Style Schema Editor)
    # ------------------------------------------------------------------
    def _build_shot_properties_page(self) -> None:
        _, lay = self.add_page(
            "Shot Properties",
            "Configure built-in and custom shot properties, types, dropdown options, and badge colors.",
        )

        split = QHBoxLayout()
        split.setSpacing(12)

        # Left column: ordered property list + add/remove/reorder
        left_col = QVBoxLayout()
        left_col.setSpacing(6)

        self.prop_list = QListWidget()
        self.prop_list.setMinimumWidth(205)
        self.prop_list.setMaximumWidth(240)
        self.prop_list.currentRowChanged.connect(self._on_prop_selected)
        left_col.addWidget(self.prop_list, 1)

        left_btns = QHBoxLayout()
        left_btns.setSpacing(4)
        add_prop_btn = QPushButton("+ Add")
        add_prop_btn.setObjectName("accent_btn")
        add_prop_btn.clicked.connect(self._on_add_property)
        left_btns.addWidget(add_prop_btn)

        self.rm_prop_btn = QPushButton("Remove")
        self.rm_prop_btn.setObjectName("warn_btn")
        self.rm_prop_btn.clicked.connect(self._on_remove_property)
        left_btns.addWidget(self.rm_prop_btn)
        left_col.addLayout(left_btns)

        order_btns = QHBoxLayout()
        order_btns.setSpacing(4)
        up_btn = QPushButton("Move Up")
        up_btn.clicked.connect(lambda: self._move_property(-1))
        order_btns.addWidget(up_btn)
        dn_btn = QPushButton("Move Down")
        dn_btn.clicked.connect(lambda: self._move_property(1))
        order_btns.addWidget(dn_btn)
        left_col.addLayout(order_btns)

        split.addLayout(left_col)

        # Right column: Property Inspector card
        insp_card = QFrame()
        insp_card.setObjectName("card_frame")
        insp_lay = QVBoxLayout(insp_card)
        insp_lay.setContentsMargins(12, 10, 12, 12)
        insp_lay.setSpacing(10)

        meta_grid = QGridLayout()
        meta_grid.setHorizontalSpacing(10)
        meta_grid.setVerticalSpacing(8)

        meta_grid.addWidget(QLabel("Name:"), 0, 0)
        self.prop_name_edit = QLineEdit()
        self.prop_name_edit.textChanged.connect(self._on_prop_name_changed)
        meta_grid.addWidget(self.prop_name_edit, 0, 1)

        meta_grid.addWidget(QLabel("Type:"), 1, 0)
        self.prop_type_combo = QComboBox()
        for pt in PropertyType.ALL:
            self.prop_type_combo.addItem(PropertyType.label(pt), pt)
        self.prop_type_combo.currentIndexChanged.connect(self._on_prop_type_changed)
        meta_grid.addWidget(self.prop_type_combo, 1, 1)

        self.prop_show_table_chk = QCheckBox("Show as column in Shot Explorer")
        self.prop_show_table_chk.toggled.connect(self._on_prop_flags_changed)
        meta_grid.addWidget(self.prop_show_table_chk, 2, 0, 1, 2)

        self.prop_multi_chk = QCheckBox("Allow assigning multiple people")
        self.prop_multi_chk.toggled.connect(self._on_prop_flags_changed)
        meta_grid.addWidget(self.prop_multi_chk, 3, 0, 1, 2)

        insp_lay.addLayout(meta_grid)

        # People info shortcut
        self.people_hint_row = QWidget()
        ph_lay = QHBoxLayout(self.people_hint_row)
        ph_lay.setContentsMargins(0, 2, 0, 2)
        ph_lbl = QLabel("People options & badge colors come from the project Artists roster.")
        ph_lbl.setStyleSheet("color: #9B9A97; font-size: 11px;")
        ph_lbl.setWordWrap(True)
        ph_lay.addWidget(ph_lbl, 1)
        go_art_btn = QPushButton("Edit Artists →")
        go_art_btn.clicked.connect(lambda: self.select_page("Artists"))
        ph_lay.addWidget(go_art_btn)
        insp_lay.addWidget(self.people_hint_row)

        # Options & Colors editor (for status / select / multi_select)
        self.options_section = QWidget()
        opt_lay = QVBoxLayout(self.options_section)
        opt_lay.setContentsMargins(0, 4, 0, 0)
        opt_lay.setSpacing(6)

        opt_hdr = QHBoxLayout()
        opt_title = QLabel("Options & Colors")
        opt_title.setStyleSheet("color: #9B9A97; font-weight: 600;")
        opt_hdr.addWidget(opt_title)
        opt_hdr.addStretch()
        add_opt_btn = QPushButton("+ Add Option")
        add_opt_btn.setObjectName("accent_btn")
        add_opt_btn.clicked.connect(self._on_add_option)
        opt_hdr.addWidget(add_opt_btn)
        opt_lay.addLayout(opt_hdr)

        self.options_table = QTableWidget(0, 3)
        self.options_table.setHorizontalHeaderLabels(["Color", "Option Name", ""])
        self.options_table.verticalHeader().setVisible(False)
        self.options_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.options_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        oh = self.options_table.horizontalHeader()
        oh.setSectionResizeMode(0, QHeaderView.Fixed)
        self.options_table.setColumnWidth(0, 46)
        oh.setSectionResizeMode(1, QHeaderView.Stretch)
        oh.setSectionResizeMode(2, QHeaderView.Fixed)
        self.options_table.setColumnWidth(2, 68)
        opt_lay.addWidget(self.options_table, 1)

        insp_lay.addWidget(self.options_section, 1)
        insp_lay.addStretch(0)

        split.addWidget(insp_card, 1)
        lay.addLayout(split, 1)

        self._refresh_prop_list(select_row=0)

    def _current_prop(self) -> Optional[ShotProperty]:
        row = self.prop_list.currentRow()
        if 0 <= row < len(self._props):
            return self._props[row]
        return None

    def _format_prop_list_label(self, prop: ShotProperty) -> str:
        tag = PropertyType.label(prop.prop_type)
        lock = "  [Built-in]" if prop.builtin else ""
        return f"{prop.name}  ({tag}){lock}"

    def _refresh_prop_list(self, select_row: int = 0) -> None:
        self.prop_list.blockSignals(True)
        self.prop_list.clear()
        for prop in self._props:
            self.prop_list.addItem(QListWidgetItem(self._format_prop_list_label(prop)))
        self.prop_list.blockSignals(False)
        if self._props:
            idx = max(0, min(select_row, len(self._props) - 1))
            self.prop_list.setCurrentRow(idx)
            self._on_prop_selected(idx)

    def _on_prop_selected(self, row: int) -> None:
        self._loading_prop_inspector = True
        prop = self._props[row] if 0 <= row < len(self._props) else None
        if prop is None:
            self._loading_prop_inspector = False
            return

        self.prop_name_edit.setText(prop.name)
        self.prop_name_edit.setEnabled(not prop.builtin)

        t_idx = self.prop_type_combo.findData(prop.prop_type)
        if t_idx >= 0:
            self.prop_type_combo.setCurrentIndex(t_idx)
        self.prop_type_combo.setEnabled(not prop.builtin)

        self.prop_show_table_chk.setChecked(prop.show_in_table)
        self.prop_multi_chk.setChecked(prop.allow_multiple)
        self.rm_prop_btn.setEnabled(not prop.builtin)

        is_people = prop.prop_type == PropertyType.PEOPLE
        is_opt_based = prop.prop_type in PropertyType.OPTION_BASED
        self.prop_multi_chk.setVisible(is_people)
        self.people_hint_row.setVisible(is_people)
        self.options_section.setVisible(is_opt_based)

        if is_opt_based:
            self._populate_options_table(prop)

        self._loading_prop_inspector = False

    def _populate_options_table(self, prop: ShotProperty) -> None:
        self.options_table.setRowCount(0)
        for idx, opt in enumerate(prop.options):
            self.options_table.insertRow(idx)
            self.options_table.setRowHeight(idx, 34)

            # Col 0: Color swatch
            swatch = ColorSwatchButton(opt.color)
            swatch.color_changed.connect(lambda new_hex, o=opt: setattr(o, "color", new_hex))
            self.options_table.setCellWidget(idx, 0, self._wrap_centered(swatch, 4, 2))

            # Col 1: Option Name
            name_edit = QLineEdit(opt.name)
            name_edit.setStyleSheet("border: none; background: transparent; padding: 2px 6px;")
            name_edit.textChanged.connect(lambda txt, o=opt: setattr(o, "name", txt.strip()))
            self.options_table.setCellWidget(idx, 1, name_edit)

            # Col 2: Delete button
            del_btn = QPushButton("✕")
            del_btn.setToolTip("Delete option")
            del_btn.setStyleSheet("padding: 2px 8px; color: #9B9A97;")
            del_btn.clicked.connect(lambda _=False, i=idx: self._on_delete_option(i))
            self.options_table.setCellWidget(idx, 2, del_btn)

    def _on_prop_name_changed(self, text: str) -> None:
        if self._loading_prop_inspector:
            return
        prop = self._current_prop()
        if not prop or prop.builtin:
            return
        prop.name = text.strip() or prop.name
        item = self.prop_list.currentItem()
        if item:
            item.setText(self._format_prop_list_label(prop))

    def _on_prop_type_changed(self) -> None:
        if self._loading_prop_inspector:
            return
        prop = self._current_prop()
        if not prop or prop.builtin:
            return
        new_type = self.prop_type_combo.currentData()
        if not new_type:
            return
        prop.prop_type = new_type
        item = self.prop_list.currentItem()
        if item:
            item.setText(self._format_prop_list_label(prop))
        is_people = prop.prop_type == PropertyType.PEOPLE
        is_opt_based = prop.prop_type in PropertyType.OPTION_BASED
        self.prop_multi_chk.setVisible(is_people)
        self.people_hint_row.setVisible(is_people)
        self.options_section.setVisible(is_opt_based)
        if is_opt_based:
            self._populate_options_table(prop)

    def _on_prop_flags_changed(self) -> None:
        if self._loading_prop_inspector:
            return
        prop = self._current_prop()
        if not prop:
            return
        prop.show_in_table = self.prop_show_table_chk.isChecked()
        prop.allow_multiple = self.prop_multi_chk.isChecked()

    def _on_add_property(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Shot Property", "Property Name (e.g. Priority, Due Date, Vendor):")
        name = (name or "").strip()
        if not ok or not name:
            return
        if any(p.name.lower() == name.lower() for p in self._props):
            QMessageBox.information(self, "Duplicate Property", f"A property named '{name}' already exists.")
            return
        new_prop = ShotProperty(
            key=slugify_property_key(name),
            name=name,
            prop_type=PropertyType.SELECT,
            options=[],
            show_in_table=True,
        )
        self._props.append(new_prop)
        self._refresh_prop_list(select_row=len(self._props) - 1)

    def _on_remove_property(self) -> None:
        row = self.prop_list.currentRow()
        if row < 0 or row >= len(self._props):
            return
        if self._props[row].builtin:
            return
        self._props.pop(row)
        self._refresh_prop_list(select_row=max(0, row - 1))

    def _move_property(self, delta: int) -> None:
        row = self.prop_list.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= len(self._props):
            return
        self._props[row], self._props[target] = self._props[target], self._props[row]
        self._refresh_prop_list(select_row=target)

    def _on_add_option(self) -> None:
        prop = self._current_prop()
        if not prop:
            return
        opt_name, ok = QInputDialog.getText(self, f"Add Option — {prop.name}", "Option Name:")
        opt_name = (opt_name or "").strip()
        if not ok or not opt_name:
            return
        if any(o.name.lower() == opt_name.lower() for o in prop.options):
            QMessageBox.information(self, "Duplicate Option", f"'{opt_name}' already exists in {prop.name}.")
            return
        color = pick_palette_color(len(prop.options))
        prop.options.append(PropertyOption(name=opt_name, color=color))
        self._populate_options_table(prop)

    def _on_delete_option(self, opt_idx: int) -> None:
        prop = self._current_prop()
        if not prop or opt_idx < 0 or opt_idx >= len(prop.options):
            return
        if prop.key == "status" and len(prop.options) <= 1:
            QMessageBox.information(self, "Status Required", "At least one Status option must remain.")
            return
        prop.options.pop(opt_idx)
        self._populate_options_table(prop)

    # ------------------------------------------------------------------
    # Page 4: Artists (with per-artist badge colors)
    # ------------------------------------------------------------------
    def _build_artists_page(self) -> None:
        _, lay = self.add_page(
            "Artists",
            "Project artist roster and badge colors used for People / Artist properties.",
        )

        self.artist_table = QTableWidget(0, 4)
        self.artist_table.setHorizontalHeaderLabels(["Color", "Artist Name", "Role", "Active"])
        self.artist_table.verticalHeader().setVisible(False)
        self.artist_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.artist_table.setSelectionMode(QAbstractItemView.SingleSelection)
        ah = self.artist_table.horizontalHeader()
        ah.setSectionResizeMode(0, QHeaderView.Fixed)
        self.artist_table.setColumnWidth(0, 48)
        ah.setSectionResizeMode(1, QHeaderView.Stretch)
        ah.setSectionResizeMode(2, QHeaderView.Stretch)
        ah.setSectionResizeMode(3, QHeaderView.Fixed)
        self.artist_table.setColumnWidth(3, 62)
        self.artist_table.itemChanged.connect(self._on_artist_item_changed)
        lay.addWidget(self.artist_table, 1)

        self._populate_artists_table()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        add_btn = QPushButton("+ Add Artist...")
        add_btn.setObjectName("accent_btn")
        add_btn.clicked.connect(self._on_add_artist)
        btn_row.addWidget(add_btn)

        rm_btn = QPushButton("Remove")
        rm_btn.setObjectName("warn_btn")
        rm_btn.clicked.connect(self._on_remove_artist)
        btn_row.addWidget(rm_btn)

        btn_row.addStretch()
        lay.addLayout(btn_row)

    def _populate_artists_table(self) -> None:
        self._loading_artists_table = True
        self.artist_table.setRowCount(0)
        for idx, artist in enumerate(self._artists):
            self.artist_table.insertRow(idx)
            self.artist_table.setRowHeight(idx, 34)

            # Col 0: Color swatch
            swatch = ColorSwatchButton(artist.color)
            swatch.color_changed.connect(lambda new_hex, a=artist: setattr(a, "color", new_hex))
            self.artist_table.setCellWidget(idx, 0, self._wrap_centered(swatch, 4, 2))

            # Col 1: Name
            self.artist_table.setItem(idx, 1, QTableWidgetItem(artist.name))

            # Col 2: Role
            self.artist_table.setItem(idx, 2, QTableWidgetItem(artist.role or ""))

            # Col 3: Active checkbox
            chk = QCheckBox()
            chk.setChecked(artist.active)
            chk.toggled.connect(lambda checked, a=artist: setattr(a, "active", checked))
            self.artist_table.setCellWidget(idx, 3, self._wrap_centered(chk))

        self._loading_artists_table = False

    def _on_artist_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading_artists_table:
            return
        row = item.row()
        if row < 0 or row >= len(self._artists):
            return
        artist = self._artists[row]
        if item.column() == 1:
            txt = item.text().strip()
            if txt:
                artist.name = txt
        elif item.column() == 2:
            artist.role = item.text().strip()

    def _on_add_artist(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Project Artist", "Artist Name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        if any(a.name.lower() == name.lower() for a in self._artists):
            return
        color = pick_palette_color(len(self._artists))
        self._artists.append(Artist(name=name, color=color))
        self._populate_artists_table()
        self.artist_table.selectRow(len(self._artists) - 1)

    def _on_remove_artist(self) -> None:
        row = self.artist_table.currentRow()
        if 0 <= row < len(self._artists):
            self._artists.pop(row)
            self._populate_artists_table()

    # ------------------------------------------------------------------
    # Page 5: Cloud Sync
    # ------------------------------------------------------------------
    def _build_cloud_sync_page(self) -> None:
        _, lay = self.add_page(
            "Cloud Sync",
            "Connect this project to a Google Sheet Apps Script webhook for bidirectional shot sync.",
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        grid.addWidget(QLabel("Google Sheet Webhook:"), 0, 0)
        self.gsheet_edit = QLineEdit(self.project.google_sheet_url or "")
        self.gsheet_edit.setPlaceholderText("https://script.google.com/macros/s/.../exec")
        grid.addWidget(self.gsheet_edit, 0, 1)

        grid.addWidget(QLabel("Last Synced:"), 1, 0)
        sync_lbl = QLabel(self.project.last_sync_time or "Never")
        sync_lbl.setStyleSheet("color: #9B9A97;")
        grid.addWidget(sync_lbl, 1, 1)

        lay.addLayout(grid)
        lay.addStretch()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save_settings(self) -> bool:
        new_name = self.name_edit.text().strip()
        if new_name:
            self.project.name = new_name

        self.project.fps = self.fps_combo.fps_value()
        self.project.resolution = self.res_picker.resolution_value() or self.project.resolution
        cleaned_res_presets = [
            ResolutionPreset(p.name, p.width, p.height)
            for p in self._res_presets
            if (p.name or "").strip() and p.width > 0 and p.height > 0
        ]
        self.project.resolution_presets = cleaned_res_presets or default_resolution_presets()
        self.project.default_start_frame = int(self.start_frame_spin.value())
        self.project.default_handles = int(self.handles_spin.value())
        self.project.export_folder = (self.export_folder_edit.text().strip() or DEFAULT_EXPORT_FOLDER)
        ocio_val = self.ocio_edit.text().strip()
        self.project.ocio_config = os.path.normpath(ocio_val) if ocio_val else ""
        if self.project.exports_dir:
            os.makedirs(self.project.exports_dir, exist_ok=True)

        # Clean & persist Shot Properties (preserve order & deduplicate empty option names)
        cleaned_props: List[ShotProperty] = []
        seen_names = set()
        renamed_map = {}
        for prop in self._props:
            pname = (prop.name or "").strip()
            if not pname or pname.lower() in seen_names:
                continue
            seen_names.add(pname.lower())
            prop.options = [o for o in prop.options if (o.name or "").strip()]
            cleaned_props.append(prop)
            if not prop.builtin:
                orig_name = getattr(prop, "_orig_name", "")
                if orig_name and orig_name != pname:
                    renamed_map[orig_name] = pname

        active_custom_names = {p.name for p in cleaned_props if not p.builtin and p.name}
        for shot in self.project.all_shots():
            for old_n, new_n in renamed_map.items():
                if old_n in shot.custom_fields:
                    val = shot.custom_fields.pop(old_n)
                    if new_n not in shot.custom_fields:
                        shot.custom_fields[new_n] = val
            # Prune both newly removed and any previously orphaned custom_fields keys
            stale_keys = [k for k in shot.custom_fields.keys() if k not in active_custom_names]
            for stale_k in stale_keys:
                shot.custom_fields.pop(stale_k, None)

        self.project.shot_properties = cleaned_props
        self.project._ensure_builtin_properties()

        # Persist Artists (preserving color, role, departments, email, active)
        updated_artists = {}
        for artist in self._artists:
            aname = (artist.name or "").strip()
            if aname:
                updated_artists[aname] = artist
        self.project.artists = updated_artists

        self.project.google_sheet_url = self.gsheet_edit.text().strip()
        return True
