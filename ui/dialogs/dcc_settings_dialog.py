"""DCC Settings dialog for DCC Launcher (Plugins & Paths, Launch Env & Args, Launcher Defaults)."""

import os
import sys
from typing import Dict, Tuple

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from config import load_user_settings, save_user_settings
from dccs import DCCRegistry
from ui.dialogs.settings_base import SidebarSettingsDialog


class DCCSettingsDialog(SidebarSettingsDialog):
    """DCC plugin toggles, executable paths, launch env/args, and launcher defaults."""

    def __init__(self, parent, dcc_registry: DCCRegistry, initial_page: str = ""):
        super().__init__(
            parent=parent,
            title="DCC Settings",
            save_label="Save DCC Settings",
            width=800,
            height=520,
        )
        self.dcc_registry = dcc_registry
        self._settings = load_user_settings()

        # Working copies of per-DCC env & args edited across combo switches
        raw_env = self._settings.get("dcc_env", {})
        self._dcc_env: Dict[str, Dict[str, str]] = {
            k: dict(v) for k, v in raw_env.items() if isinstance(v, dict)
        }
        raw_args = self._settings.get("dcc_args", {})
        self._dcc_args: Dict[str, str] = {
            k: (v if isinstance(v, str) else " ".join(v))
            for k, v in raw_args.items()
            if isinstance(v, (str, list))
        }
        self._active_env_dcc_id: str = ""
        self._dcc_widgets: Dict[str, Tuple[QCheckBox, QLineEdit, QLabel]] = {}

        self._build_plugins_page()
        self._build_env_args_page()
        self._build_defaults_page()

        if initial_page:
            self.select_page(initial_page)

    # ------------------------------------------------------------------
    # Page 1: DCC Plugins & Paths
    # ------------------------------------------------------------------
    def _build_plugins_page(self) -> None:
        _, lay = self.add_page(
            "DCC Plugins & Paths",
            "Enable DCC integrations, configure executable paths, or auto-detect local installations.",
        )

        for plugin in self.dcc_registry.all_plugins():
            card = QFrame()
            card.setObjectName("card_frame")
            c_lay = QVBoxLayout(card)
            c_lay.setContentsMargins(10, 8, 10, 8)
            c_lay.setSpacing(6)

            top_row = QHBoxLayout()
            top_row.setSpacing(8)
            chk = QCheckBox(f"{plugin.display_name}  ({', '.join(plugin.extensions)})")
            chk.setChecked(self.dcc_registry.is_enabled(plugin.dcc_id))
            top_row.addWidget(chk)
            top_row.addStretch()

            status_lbl = QLabel()
            status_lbl.setAlignment(Qt.AlignCenter)
            top_row.addWidget(status_lbl)
            c_lay.addLayout(top_row)

            path_row = QHBoxLayout()
            path_row.setSpacing(6)
            exe_edit = QLineEdit(plugin.get_executable())
            exe_edit.setPlaceholderText(f"Path to {plugin.display_name} executable...")
            exe_edit.textChanged.connect(
                lambda text, lbl=status_lbl: self._update_status_badge(lbl, text)
            )
            self._update_status_badge(status_lbl, exe_edit.text())
            path_row.addWidget(exe_edit, 1)

            browse_btn = QPushButton("Browse...")
            browse_btn.clicked.connect(
                lambda _=False, le=exe_edit, name=plugin.display_name: self._browse_exe(le, name)
            )
            path_row.addWidget(browse_btn)

            detect_btn = QPushButton("Auto-Detect")
            detect_btn.clicked.connect(
                lambda _=False, p=plugin, le=exe_edit: self._auto_detect_dcc(p, le)
            )
            path_row.addWidget(detect_btn)
            c_lay.addLayout(path_row)

            lay.addWidget(card)
            self._dcc_widgets[plugin.dcc_id] = (chk, exe_edit, status_lbl)

        lay.addStretch()

    @staticmethod
    def _update_status_badge(label: QLabel, path_text: str) -> None:
        exists = bool(path_text.strip() and os.path.isfile(path_text.strip()))
        if exists:
            label.setText("Detected")
            label.setStyleSheet(
                "background-color: rgba(77, 171, 154, 0.18); color: #4DAB9A; "
                "border: 1px solid rgba(77, 171, 154, 0.4); border-radius: 4px; "
                "padding: 2px 6px; font-size: 11px; font-weight: 600;"
            )
        else:
            label.setText("Missing")
            label.setStyleSheet(
                "background-color: rgba(235, 87, 87, 0.14); color: #EB5757; "
                "border: 1px solid rgba(235, 87, 87, 0.35); border-radius: 4px; "
                "padding: 2px 6px; font-size: 11px; font-weight: 600;"
            )

    def _auto_detect_dcc(self, plugin, line_edit: QLineEdit) -> None:
        found = plugin.auto_detect_executables()
        if found:
            line_edit.setText(os.path.normpath(found[0]))

    def _browse_exe(self, line_edit: QLineEdit, title: str) -> None:
        exe_filter = "Executables (*.exe);;All Files (*.*)" if sys.platform == "win32" else "All Files (*)"
        chosen, _ = QFileDialog.getOpenFileName(self, f"Select {title} Executable", line_edit.text(), exe_filter)
        if chosen:
            line_edit.setText(os.path.normpath(chosen))

    # ------------------------------------------------------------------
    # Page 2: Launch Env & Args
    # ------------------------------------------------------------------
    def _build_env_args_page(self) -> None:
        _, lay = self.add_page(
            "Launch Env & Args",
            "Optional per-DCC environment variables and extra CLI arguments injected on launch.",
        )

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("DCC Plugin:"))
        self.env_dcc_combo = QComboBox()
        for plugin in self.dcc_registry.all_plugins():
            self.env_dcc_combo.addItem(plugin.display_name, plugin.dcc_id)
        self.env_dcc_combo.currentIndexChanged.connect(self._on_env_dcc_changed)
        top_row.addWidget(self.env_dcc_combo, 1)
        lay.addLayout(top_row)

        args_row = QHBoxLayout()
        args_row.addWidget(QLabel("Extra CLI Args:"))
        self.args_edit = QLineEdit()
        self.args_edit.setPlaceholderText("e.g. --nc or --nukex (space-separated)")
        args_row.addWidget(self.args_edit, 1)
        lay.addLayout(args_row)

        self.env_table = QTableWidget(0, 2)
        self.env_table.setHorizontalHeaderLabels(["Variable Name", "Value"])
        self.env_table.verticalHeader().setVisible(False)
        self.env_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.env_table.setSelectionMode(QAbstractItemView.SingleSelection)
        hdr = self.env_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        self.env_table.setColumnWidth(0, 190)
        lay.addWidget(self.env_table, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        add_var_btn = QPushButton("+ Add Variable")
        add_var_btn.setObjectName("accent_btn")
        add_var_btn.clicked.connect(self._on_add_env_row)
        btn_row.addWidget(add_var_btn)

        rm_var_btn = QPushButton("Remove Variable")
        rm_var_btn.setObjectName("warn_btn")
        rm_var_btn.clicked.connect(self._on_remove_env_row)
        btn_row.addWidget(rm_var_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        if self.env_dcc_combo.count() > 0:
            self._load_env_for_dcc(self.env_dcc_combo.currentData())

    def _stash_current_env_args(self) -> None:
        if not self._active_env_dcc_id:
            return
        env_map: Dict[str, str] = {}
        for r in range(self.env_table.rowCount()):
            k_item = self.env_table.item(r, 0)
            v_item = self.env_table.item(r, 1)
            k = k_item.text().strip() if k_item else ""
            v = v_item.text().strip() if v_item else ""
            if k:
                env_map[k] = v
        if env_map:
            self._dcc_env[self._active_env_dcc_id] = env_map
        else:
            self._dcc_env.pop(self._active_env_dcc_id, None)

        args_txt = self.args_edit.text().strip()
        if args_txt:
            self._dcc_args[self._active_env_dcc_id] = args_txt
        else:
            self._dcc_args.pop(self._active_env_dcc_id, None)

    def _load_env_for_dcc(self, dcc_id: str) -> None:
        self._active_env_dcc_id = dcc_id or ""
        self.args_edit.setText(self._dcc_args.get(self._active_env_dcc_id, ""))
        self.env_table.setRowCount(0)
        for k, v in self._dcc_env.get(self._active_env_dcc_id, {}).items():
            r = self.env_table.rowCount()
            self.env_table.insertRow(r)
            self.env_table.setItem(r, 0, QTableWidgetItem(k))
            self.env_table.setItem(r, 1, QTableWidgetItem(v))

    def _on_env_dcc_changed(self, idx: int) -> None:
        self._stash_current_env_args()
        if idx >= 0:
            self._load_env_for_dcc(self.env_dcc_combo.itemData(idx))

    def _on_add_env_row(self) -> None:
        r = self.env_table.rowCount()
        self.env_table.insertRow(r)
        self.env_table.setItem(r, 0, QTableWidgetItem("MY_VAR"))
        self.env_table.setItem(r, 1, QTableWidgetItem(""))
        self.env_table.setCurrentCell(r, 0)
        self.env_table.editItem(self.env_table.item(r, 0))

    def _on_remove_env_row(self) -> None:
        r = self.env_table.currentRow()
        if r >= 0:
            self.env_table.removeRow(r)

    # ------------------------------------------------------------------
    # Page 3: Launcher Defaults
    # ------------------------------------------------------------------
    def _build_defaults_page(self) -> None:
        _, lay = self.add_page(
            "Launcher Defaults",
            "Default scene creation behavior when opening DCC Launcher.",
        )

        self.default_bootstrap_chk = QCheckBox("Auto-Bootstrap Scene by default")
        self.default_bootstrap_chk.setChecked(bool(self._settings.get("dcc_default_bootstrap", True)))
        lay.addWidget(self.default_bootstrap_chk)

        self.default_launch_chk = QCheckBox("Launch immediately after creating a scene by default")
        self.default_launch_chk.setChecked(bool(self._settings.get("dcc_default_launch", True)))
        lay.addWidget(self.default_launch_chk)

        lay.addStretch()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save_settings(self) -> bool:
        self._stash_current_env_args()

        enabled_dccs = []
        dcc_paths = {}
        for dcc_id, (chk, le, _) in self._dcc_widgets.items():
            if chk.isChecked():
                enabled_dccs.append(dcc_id)
            if le.text().strip():
                dcc_paths[dcc_id] = os.path.normpath(le.text().strip())

        save_user_settings({
            "enabled_dccs": enabled_dccs,
            "dcc_paths": dcc_paths,
            "dcc_env": self._dcc_env,
            "dcc_args": self._dcc_args,
            "dcc_default_bootstrap": self.default_bootstrap_chk.isChecked(),
            "dcc_default_launch": self.default_launch_chk.isChecked(),
        })
        self.dcc_registry.reload_from_settings()
        return True
