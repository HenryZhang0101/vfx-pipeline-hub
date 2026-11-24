"""Confirmation dialog for removing selected shots with optional disk folder deletion."""

from typing import List

from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ui.theme import get_active_stylesheet


class RemoveShotsDialog(QDialog):
    """Modal confirmation dialog asking whether to keep shot files on disk or delete the entire shot folder."""

    def __init__(self, parent, shot_ids: List[str]):
        super().__init__(parent)
        self.shot_ids = list(shot_ids or [])
        count = len(self.shot_ids)

        self.setWindowTitle("Remove Selected Shot(s)" if count > 1 else "Remove Shot")
        self.setStyleSheet(get_active_stylesheet())
        self.setMinimumWidth(440)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        summary = ", ".join(self.shot_ids[:6]) + (f" (+{count - 6} more)" if count > 6 else "")
        title_text = (
            f"Remove {count} selected shots?"
            if count > 1
            else f"Remove shot {self.shot_ids[0] if self.shot_ids else ''}?"
        )
        title_lbl = QLabel(title_text)
        title_lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        lay.addWidget(title_lbl)

        desc_lbl = QLabel(
            f"<b>Shots:</b> {summary}<br>"
            "Selected shot(s) will be removed from <code>project.json</code> and the shot list."
        )
        desc_lbl.setWordWrap(True)
        lay.addWidget(desc_lbl)

        self.delete_disk_chk = QCheckBox(
            "Also delete entire shot folder and all media, assets, and scene files on disk"
        )
        self.delete_disk_chk.setChecked(False)
        self.delete_disk_chk.toggled.connect(self._on_delete_toggled)
        lay.addWidget(self.delete_disk_chk)

        self.hint_lbl = QLabel("")
        self.hint_lbl.setWordWrap(True)
        lay.addWidget(self.hint_lbl)

        btns = QHBoxLayout()
        btns.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        btns.addWidget(self.cancel_btn)

        self.confirm_btn = QPushButton("Remove from Project")
        self.confirm_btn.clicked.connect(self.accept)
        btns.addWidget(self.confirm_btn)

        lay.addLayout(btns)

        self._on_delete_toggled(False)
        self.cancel_btn.setDefault(True)
        self.cancel_btn.setFocus()

    @property
    def delete_from_disk(self) -> bool:
        return bool(self.delete_disk_chk.isChecked())

    def _on_delete_toggled(self, checked: bool) -> None:
        if checked:
            self.hint_lbl.setText(
                "Warning: Permanently deletes the entire shot directory (media, assets, scenes, "
                "and thumbnails) from disk. This action cannot be undone."
            )
            self.hint_lbl.setStyleSheet("color: #E05252; font-size: 11px;")
            self.confirm_btn.setText("Remove && Delete Folder(s)")
            self.confirm_btn.setObjectName("warn_btn")
        else:
            self.hint_lbl.setText(
                "Shot folders, media, assets, and scene files on disk will be kept untouched."
            )
            self.hint_lbl.setStyleSheet("color: #9B9A97; font-size: 11px;")
            self.confirm_btn.setText("Remove from Project")
            self.confirm_btn.setObjectName("primary_btn")

        style = self.confirm_btn.style()
        if style is not None:
            style.unpolish(self.confirm_btn)
            style.polish(self.confirm_btn)
