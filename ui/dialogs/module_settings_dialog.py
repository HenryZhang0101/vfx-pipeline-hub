"""Module & executable settings dialog (DCCs, Tools, Theme Accent & Cloud Sync)."""

import os
import sys
from typing import Dict, Optional, Tuple

from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from config import load_user_settings, save_user_settings
from dccs import DCCRegistry
from tools import ToolRegistry
from ui.theme import (
    ACCENT_PRESETS,
    DEFAULT_ACCENT_COLOR,
    build_notion_dark_qss,
    get_active_stylesheet,
)


class ModuleSettingsDialog(QDialog):
    """
    Allows the user to enable/disable individual DCC plugins (Nuke, Houdini, Blender)
    and Pipeline Tools (DJV Player, Media Ingester), and configure executable paths.
    """

    def __init__(self, parent, dcc_registry: DCCRegistry, tool_registry: ToolRegistry, current_gsheet_url: str = ""):
        super().__init__(parent)
        self.dcc_registry = dcc_registry
        self.tool_registry = tool_registry
        self.gsheet_url_result = current_gsheet_url

        self.setWindowTitle("Modular Pipeline Settings — DCCs, Tools & Cloud Sync")
        self.resize(740, 520)
        self.setStyleSheet(get_active_stylesheet())

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Artist & Cloud Settings
        gen_grp = QGroupBox("1. Artist & Cloud Sync Settings")
        gen_grid = QGridLayout(gen_grp)
        settings = load_user_settings()

        from config import get_default_artist_name

        gen_grid.addWidget(QLabel("Artist Name:"), 0, 0)
        self.artist_edit = QLineEdit(settings.get("artist_name", get_default_artist_name()))
        gen_grid.addWidget(self.artist_edit, 0, 1)

        gen_grid.addWidget(QLabel("Google Sheet Webhook:"), 1, 0)
        self.gsheet_edit = QLineEdit(current_gsheet_url)
        self.gsheet_edit.setPlaceholderText("https://script.google.com/macros/s/.../exec")
        gen_grid.addWidget(self.gsheet_edit, 1, 1)

        gen_grid.addWidget(QLabel("Accent Color:"), 2, 0)
        self.accent_color = settings.get("accent_color", DEFAULT_ACCENT_COLOR)
        accent_row = QHBoxLayout()
        accent_row.setSpacing(6)
        self.accent_preview_lbl = QLabel(self.accent_color)
        self.accent_preview_lbl.setStyleSheet(
            f"padding: 2px 8px; border-radius: 4px; background-color: {self.accent_color}; color: #ffffff; font-weight: 600;"
        )
        accent_row.addWidget(self.accent_preview_lbl)
        for preset_name, preset_hex in ACCENT_PRESETS:
            swatch_btn = QPushButton("")
            swatch_btn.setFixedSize(22, 22)
            swatch_btn.setToolTip(f"{preset_name} ({preset_hex})")
            swatch_btn.setStyleSheet(
                f"background-color: {preset_hex}; border: 1px solid #3E3E3E; border-radius: 4px; padding: 0;"
            )
            swatch_btn.clicked.connect(lambda checked=False, c=preset_hex: self._set_accent_color(c))
            accent_row.addWidget(swatch_btn)
        custom_accent_btn = QPushButton("Custom...")
        custom_accent_btn.clicked.connect(self._pick_custom_accent)
        accent_row.addWidget(custom_accent_btn)
        accent_row.addStretch()
        gen_grid.addLayout(accent_row, 2, 1)
        layout.addWidget(gen_grp)

        # DCC Modules
        dcc_grp = QGroupBox("2. Modular DCC Integrations (Uncheck modules you don't need)")
        dcc_grid = QGridLayout(dcc_grp)
        self.dcc_Widgets: Dict[str, Tuple[QCheckBox, QLineEdit]] = {}

        for row_idx, plugin in enumerate(self.dcc_registry.all_plugins()):
            chk = QCheckBox(f"{plugin.display_name} ({', '.join(plugin.extensions)})")
            chk.setChecked(self.dcc_registry.is_enabled(plugin.dcc_id))
            exe_edit = QLineEdit(plugin.get_executable())
            exe_edit.setPlaceholderText(f"Path to {plugin.display_name} executable...")
            browse_btn = QPushButton("Browse...")
            browse_btn.clicked.connect(lambda checked=False, le=exe_edit, name=plugin.display_name: self._browse_exe(le, name))

            dcc_grid.addWidget(chk, row_idx, 0)
            dcc_grid.addWidget(exe_edit, row_idx, 1)
            dcc_grid.addWidget(browse_btn, row_idx, 2)
            self.dcc_Widgets[plugin.dcc_id] = (chk, exe_edit)

        layout.addWidget(dcc_grp)

        # Pipeline Tool Modules
        tool_grp = QGroupBox("3. Modular Pipeline Toolset")
        tool_grid = QGridLayout(tool_grp)
        self.tool_widgets: Dict[str, Tuple[QCheckBox, Optional[QLineEdit]]] = {}

        for row_idx, tool in enumerate(self.tool_registry.all_tools()):
            chk = QCheckBox(f"{tool.display_name} [{tool.category}]")
            chk.setChecked(self.tool_registry.is_enabled(tool.tool_id))
            chk.setToolTip(tool.description)
            tool_grid.addWidget(chk, row_idx, 0)

            if tool.requires_executable:
                exe_edit = QLineEdit(tool.get_executable())
                exe_edit.setPlaceholderText(f"Path to {tool.display_name} executable...")
                browse_btn = QPushButton("Browse...")
                browse_btn.clicked.connect(lambda checked=False, le=exe_edit, name=tool.display_name: self._browse_exe(le, name))
                tool_grid.addWidget(exe_edit, row_idx, 1)
                tool_grid.addWidget(browse_btn, row_idx, 2)
                self.tool_widgets[tool.tool_id] = (chk, exe_edit)
            else:
                desc_lbl = QLabel(tool.description)
                desc_lbl.setStyleSheet("color: #7f848e; font-style: italic;")
                tool_grid.addWidget(desc_lbl, row_idx, 1, 1, 2)
                self.tool_widgets[tool.tool_id] = (chk, None)

        layout.addWidget(tool_grp)
        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save Module Settings")
        save_btn.setObjectName("success_btn")
        save_btn.clicked.connect(self._on_save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _set_accent_color(self, hex_color: str):
        self.accent_color = hex_color
        self.accent_preview_lbl.setText(hex_color)
        self.accent_preview_lbl.setStyleSheet(
            f"padding: 2px 8px; border-radius: 4px; background-color: {hex_color}; color: #ffffff; font-weight: 600;"
        )
        self.setStyleSheet(build_notion_dark_qss(hex_color))

    def _pick_custom_accent(self):
        chosen = QColorDialog.getColor(QColor(self.accent_color), self, "Choose Accent Color")
        if chosen.isValid():
            self._set_accent_color(chosen.name())

    def _browse_exe(self, line_edit: QLineEdit, title: str):
        exe_filter = "Executables (*.exe);;All Files (*.*)" if sys.platform == "win32" else "All Files (*)"
        chosen, _ = QFileDialog.getOpenFileName(self, f"Select {title} Executable", line_edit.text(), exe_filter)
        if chosen:
            line_edit.setText(os.path.normpath(chosen))

    def _on_save(self):
        enabled_dccs = []
        dcc_paths = {}
        for dcc_id, (chk, le) in self.dcc_Widgets.items():
            if chk.isChecked():
                enabled_dccs.append(dcc_id)
            if le.text().strip():
                dcc_paths[dcc_id] = os.path.normpath(le.text().strip())

        enabled_tools = []
        tool_paths = {}
        for tool_id, (chk, le) in self.tool_widgets.items():
            if chk.isChecked():
                enabled_tools.append(tool_id)
            if le and le.text().strip():
                tool_paths[tool_id] = os.path.normpath(le.text().strip())

        save_user_settings({
            "artist_name": self.artist_edit.text().strip() or "Artist",
            "accent_color": self.accent_color,
            "enabled_dccs": enabled_dccs,
            "dcc_paths": dcc_paths,
            "enabled_tools": enabled_tools,
            "tool_paths": tool_paths,
        })
        self.dcc_registry.reload_from_settings()
        self.tool_registry.reload_from_settings()
        self.gsheet_url_result = self.gsheet_edit.text().strip()
        self.accept()
