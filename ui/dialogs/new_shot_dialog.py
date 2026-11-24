"""Dialog for creating a new Sequence & VFX Shot."""

from typing import List, Optional, Tuple

from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from config import DEFAULT_HANDLES, DEFAULT_START_FRAME
from core.models.resolution import ResolutionPreset, default_resolution_presets
from core.timecode import format_shot_id, split_shot_id
from ui.theme import get_active_stylesheet
from ui.widgets.spec_widgets import ResolutionPickerWidget


class NewShotDialog(QDialog):
    """Modal dialog for creating a new Sequence / VFX Shot."""

    def __init__(
        self,
        parent,
        default_shot_id: str = "R01_0010",
        existing_sequences: Optional[List[str]] = None,
        default_start_frame: int = DEFAULT_START_FRAME,
        default_handles: int = DEFAULT_HANDLES,
        project_resolution: Optional[Tuple[float, float]] = None,
        resolution_presets: Optional[List[ResolutionPreset]] = None,
    ):
        super().__init__(parent)
        self.seq = ""
        self.shot = ""
        self.start_frame = int(default_start_frame)
        self.end_frame = int(default_start_frame) + 64
        self.handles = int(default_handles)
        self.resolution: Optional[Tuple[float, float]] = None
        self.notes = ""

        def_seq, def_shot = split_shot_id(default_shot_id)
        if not def_seq:
            def_seq = "R01"
        if not def_shot:
            def_shot = "0010"

        self.setWindowTitle("New Sequence / Shot")
        self.setStyleSheet(get_active_stylesheet())
        self.resize(480, 310)

        lay = QVBoxLayout(self)
        g = QGridLayout()

        g.addWidget(QLabel("Sequence:"), 0, 0)
        self.seq_combo = QComboBox()
        self.seq_combo.setEditable(True)
        seqs = list(existing_sequences or [])
        if def_seq and def_seq not in seqs:
            seqs.insert(0, def_seq)
        for s in seqs:
            self.seq_combo.addItem(s)
        self.seq_combo.setCurrentText(def_seq)
        g.addWidget(self.seq_combo, 0, 1)

        g.addWidget(QLabel("Shot Code:"), 1, 0)
        self.shot_edit = QLineEdit(def_shot)
        self.shot_edit.setPlaceholderText("e.g. 0010, SH010")
        g.addWidget(self.shot_edit, 1, 1)

        g.addWidget(QLabel("Start Frame:"), 2, 0)
        self.sf_spin = QSpinBox()
        self.sf_spin.setRange(0, 999999)
        self.sf_spin.setValue(self.start_frame)
        g.addWidget(self.sf_spin, 2, 1)

        g.addWidget(QLabel("End Frame:"), 3, 0)
        self.ef_spin = QSpinBox()
        self.ef_spin.setRange(0, 999999)
        self.ef_spin.setValue(self.end_frame)
        g.addWidget(self.ef_spin, 3, 1)

        g.addWidget(QLabel("Handles:"), 4, 0)
        self.handles_spin = QSpinBox()
        self.handles_spin.setRange(0, 200)
        self.handles_spin.setValue(self.handles)
        g.addWidget(self.handles_spin, 4, 1)

        g.addWidget(QLabel("Resolution:"), 5, 0)
        self.res_picker = ResolutionPickerWidget(
            presets=resolution_presets or default_resolution_presets(),
            resolution=None,
            project_default=project_resolution or (1920.0, 1080.0),
        )
        g.addWidget(self.res_picker, 5, 1)

        g.addWidget(QLabel("Notes:"), 6, 0)
        self.notes_edit = QLineEdit()
        g.addWidget(self.notes_edit, 6, 1)
        lay.addLayout(g)

        btns = QHBoxLayout()
        btns.addStretch()
        ok_b = QPushButton("Create Shot")
        ok_b.setObjectName("primary_btn")
        ok_b.clicked.connect(self._on_accept)
        ca_b = QPushButton("Cancel")
        ca_b.clicked.connect(self.reject)
        btns.addWidget(ok_b)
        btns.addWidget(ca_b)
        lay.addLayout(btns)

    def _on_accept(self):
        seq = self.seq_combo.currentText().strip()
        shot = self.shot_edit.text().strip()
        if not seq or not shot:
            QMessageBox.warning(self, "Invalid Shot ID", "Both Sequence and Shot Code are required (e.g. R01 and 0010).")
            return
        full_sid = format_shot_id(seq, shot)
        seq_p, shot_p = split_shot_id(full_sid)
        if not seq_p or not shot_p:
            QMessageBox.warning(self, "Invalid Shot ID", "Use valid sequence & shot names, e.g. R01 and 0010.")
            return
        self.seq = seq_p
        self.shot = shot_p
        self.start_frame = self.sf_spin.value()
        self.end_frame = max(self.start_frame, self.ef_spin.value())
        self.handles = self.handles_spin.value()
        self.resolution = self.res_picker.resolution_value()
        self.notes = self.notes_edit.text().strip()
        self.accept()
