"""Reusable Notion-Dark Resolution (float W x H + presets) and FPS dropdown widgets."""

import re
from typing import List, Optional, Tuple

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QWidget

from config import COMMON_FPS_PRESETS
from core.models.resolution import (
    ResolutionPreset,
    default_resolution_presets,
    format_resolution,
    parse_resolution,
)


class FpsComboBox(QComboBox):
    """
    Editable frame-rate dropdown listing all common film/broadcast/VFX frame rates,
    with optional 'Project Default (XX fps)' mode for shot-level overrides.
    """

    fps_changed = Signal(float)

    def __init__(
        self,
        fps: float = 24.0,
        project_default_fps: Optional[float] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._project_default_fps = float(project_default_fps) if project_default_fps is not None else None
        self._proj_default_label = (
            f"Project Default ({self._project_default_fps:g} fps)"
            if self._project_default_fps is not None
            else ""
        )
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self._populate()
        self.set_fps(fps)
        self.currentTextChanged.connect(lambda _txt: self.fps_changed.emit(self.fps_value()))

    def _populate(self) -> None:
        self.blockSignals(True)
        self.clear()
        if self._project_default_fps is not None:
            self.addItem(self._proj_default_label, 0.0)
        for label, val in COMMON_FPS_PRESETS:
            self.addItem(label, float(val))
        self.blockSignals(False)

    def fps_value(self) -> float:
        """Return parsed float FPS (or 0.0 when set to Project Default in shot-override mode)."""
        txt = (self.currentText() or "").strip()
        if self._project_default_fps is not None:
            if not txt or txt == self._proj_default_label or txt.lower().startswith("project default"):
                return 0.0
        idx = self.findText(txt, Qt.MatchFixedString)
        if idx >= 0:
            data = self.itemData(idx)
            if isinstance(data, (int, float)):
                return round(float(data), 3)
        m = re.search(r"(\d+(?:\.\d+)?)", txt)
        if m:
            try:
                val = float(m.group(1))
                if val > 0.0:
                    return round(val, 3)
            except ValueError:
                pass
        return 0.0 if self._project_default_fps is not None else 24.0

    def set_fps(self, fps: float) -> None:
        """Select matching common FPS preset, Project Default (when <= 0), or display custom float."""
        self.blockSignals(True)
        val = float(fps or 0.0)
        if self._project_default_fps is not None and val <= 0.0:
            self.setCurrentIndex(0)
            self.blockSignals(False)
            return

        if val <= 0.0:
            val = 24.0

        matched = -1
        for i in range(self.count()):
            data = self.itemData(i)
            if isinstance(data, (int, float)) and data > 0.0 and abs(float(data) - val) < 1e-3:
                matched = i
                break
        if matched >= 0:
            self.setCurrentIndex(matched)
        else:
            self.setCurrentText(f"{val:g} fps")
        self.blockSignals(False)


class ResolutionPickerWidget(QWidget):
    """
    Composite resolution selector: [ Preset Dropdown ]  [ Width float ] x [ Height float ].
    Supports optional 'Project Default' inheritance mode for shot-level overrides.
    """

    resolution_changed = Signal(object)  # emits Optional[Tuple[float, float]]

    _DATA_PROJECT_DEFAULT = "__PROJECT_DEFAULT__"
    _DATA_CUSTOM = "__CUSTOM__"

    def __init__(
        self,
        presets: Optional[List[ResolutionPreset]] = None,
        resolution: Optional[Tuple[float, float]] = None,
        project_default: Optional[Tuple[float, float]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._presets: List[ResolutionPreset] = list(presets) if presets else default_resolution_presets()
        self._project_default: Optional[Tuple[float, float]] = parse_resolution(project_default)
        self._syncing = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.preset_combo = QComboBox()
        self.preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        lay.addWidget(self.preset_combo, 2)

        self.w_spin = QDoubleSpinBox()
        self.w_spin.setRange(1.0, 65536.0)
        self.w_spin.setDecimals(2)
        self.w_spin.setSingleStep(1.0)
        self.w_spin.setToolTip("Width (float)")
        self.w_spin.valueChanged.connect(self._on_dims_edited)
        lay.addWidget(self.w_spin, 1)

        x_lbl = QLabel("×")
        x_lbl.setStyleSheet("color: #9B9A97; font-weight: 600;")
        lay.addWidget(x_lbl)

        self.h_spin = QDoubleSpinBox()
        self.h_spin.setRange(1.0, 65536.0)
        self.h_spin.setDecimals(2)
        self.h_spin.setSingleStep(1.0)
        self.h_spin.setToolTip("Height (float)")
        self.h_spin.valueChanged.connect(self._on_dims_edited)
        lay.addWidget(self.h_spin, 1)

        self._rebuild_preset_combo()
        self.set_resolution(resolution)

    def _rebuild_preset_combo(self) -> None:
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        if self._project_default is not None:
            self.preset_combo.addItem(
                f"Project Default ({format_resolution(self._project_default)})",
                self._DATA_PROJECT_DEFAULT,
            )
        for pr in self._presets:
            self.preset_combo.addItem(pr.display_label, pr.dims)
        self.preset_combo.addItem("Custom", self._DATA_CUSTOM)
        self.preset_combo.blockSignals(False)

    def set_presets(self, presets: List[ResolutionPreset]) -> None:
        """Refresh the preset list while keeping the currently chosen resolution."""
        cur = self.resolution_value()
        self._presets = list(presets) if presets else default_resolution_presets()
        self._rebuild_preset_combo()
        self.set_resolution(cur)

    def resolution_value(self) -> Optional[Tuple[float, float]]:
        """Return (width: float, height: float), or None when set to Project Default."""
        if self._project_default is not None:
            if self.preset_combo.currentData() == self._DATA_PROJECT_DEFAULT:
                return None
        return (round(float(self.w_spin.value()), 4), round(float(self.h_spin.value()), 4))

    def set_resolution(self, resolution: Optional[Tuple[float, float]]) -> None:
        """Set the active resolution (or None for Project Default when enabled)."""
        self._syncing = True
        parsed = parse_resolution(resolution)
        if parsed is None and self._project_default is not None:
            self.preset_combo.setCurrentIndex(0)
            self.w_spin.setValue(self._project_default[0])
            self.h_spin.setValue(self._project_default[1])
            self.w_spin.setEnabled(False)
            self.h_spin.setEnabled(False)
            self._syncing = False
            return

        w, h = parsed or self._project_default or (1920.0, 1080.0)
        self.w_spin.setEnabled(True)
        self.h_spin.setEnabled(True)
        self.w_spin.setValue(w)
        self.h_spin.setValue(h)
        self._select_matching_preset(w, h)
        self._syncing = False

    def _select_matching_preset(self, w: float, h: float) -> None:
        self.preset_combo.blockSignals(True)
        matched = -1
        for i in range(self.preset_combo.count()):
            data = self.preset_combo.itemData(i)
            if isinstance(data, tuple) and len(data) == 2:
                if abs(float(data[0]) - w) < 1e-3 and abs(float(data[1]) - h) < 1e-3:
                    matched = i
                    break
        if matched >= 0:
            self.preset_combo.setCurrentIndex(matched)
        else:
            custom_idx = self.preset_combo.findData(self._DATA_CUSTOM)
            if custom_idx >= 0:
                self.preset_combo.setCurrentIndex(custom_idx)
        self.preset_combo.blockSignals(False)

    def _on_preset_selected(self, idx: int) -> None:
        if self._syncing or idx < 0:
            return
        self._syncing = True
        data = self.preset_combo.itemData(idx)
        if data == self._DATA_PROJECT_DEFAULT and self._project_default is not None:
            self.w_spin.setValue(self._project_default[0])
            self.h_spin.setValue(self._project_default[1])
            self.w_spin.setEnabled(False)
            self.h_spin.setEnabled(False)
        elif isinstance(data, tuple) and len(data) == 2:
            self.w_spin.setEnabled(True)
            self.h_spin.setEnabled(True)
            self.w_spin.setValue(float(data[0]))
            self.h_spin.setValue(float(data[1]))
        else:
            self.w_spin.setEnabled(True)
            self.h_spin.setEnabled(True)
        self._syncing = False
        self.resolution_changed.emit(self.resolution_value())

    def _on_dims_edited(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        self._select_matching_preset(float(self.w_spin.value()), float(self.h_spin.value()))
        self._syncing = False
        self.resolution_changed.emit(self.resolution_value())
