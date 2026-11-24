"""Dialog wrapper around the embeddable MediaIngestWidget (supports modal or non-modal async ingest)."""

from typing import List

from qtpy.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QPushButton, QVBoxLayout

from core.models import Project
from core.storage import ProjectRepository
from ui.theme import get_active_stylesheet
from ui.widgets.media_ingest_widget import MediaIngestWidget


class MediaIngestDialog(QDialog):
    """Dialog for ingesting plates, image sequences, movies, or 3D assets asynchronously."""

    def __init__(
        self,
        parent,
        project: Project,
        repo: ProjectRepository,
        ingester_tool,
        shot_ids: List[str],
        default_shot_id: str = "",
        new_shot_mode: bool = False,
    ):
        super().__init__(parent)
        self._close_after_cancel = False
        self.setWindowTitle("Ingest Media")
        self.resize(620, 480)
        self.setStyleSheet(get_active_stylesheet())

        layout = QVBoxLayout(self)
        self.widget = MediaIngestWidget(self)
        self.widget.set_project(
            project=project,
            repo=repo,
            ingester_tool=ingester_tool,
            shot_ids=shot_ids,
            default_shot_id=default_shot_id,
            new_shot_mode=new_shot_mode,
        )
        self.widget.ingest_started.connect(self._on_ingest_started)
        self.widget.ingest_finished.connect(self._on_ingest_finished)
        layout.addWidget(self.widget, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.ingest_btn = QPushButton("Start Ingestion")
        self.ingest_btn.setObjectName("primary_btn")
        self.ingest_btn.clicked.connect(self._on_run)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel_or_close)
        btn_row.addWidget(self.ingest_btn)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

    @property
    def ingested_shot_id(self) -> str:
        return self.widget.ingested_shot_id

    def _on_run(self):
        self._close_after_cancel = False
        self.widget.start_ingest()

    def _on_ingest_started(self):
        self.ingest_btn.setEnabled(False)
        self.cancel_btn.setText("Cancel Ingestion")
        self.cancel_btn.setObjectName("warn_btn")
        self.cancel_btn.style().unpolish(self.cancel_btn)
        self.cancel_btn.style().polish(self.cancel_btn)

    def _on_ingest_finished(self, ok: bool, _shot_id: str, _target_dir: str, _msg: str):
        self.ingest_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("Cancel")
        self.cancel_btn.setObjectName("")
        self.cancel_btn.style().unpolish(self.cancel_btn)
        self.cancel_btn.style().polish(self.cancel_btn)
        if self._close_after_cancel:
            self._close_after_cancel = False
            self.reject()
            return
        if ok:
            self.accept()

    def _on_cancel_or_close(self):
        if self.widget.is_ingesting():
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("Cancelling...")
            self.widget.cancel_ingest()
        else:
            self.reject()

    def closeEvent(self, event):
        if self.widget.is_ingesting():
            reply = QMessageBox.question(
                self,
                "Ingestion In Progress",
                "Media ingestion is currently running.\n\nCancel ingestion and close this window?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self._close_after_cancel = True
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("Cancelling...")
            self.widget.cancel_ingest()
            event.ignore()
            return
        if self.widget._analyze_worker and self.widget._analyze_worker.isRunning():
            self.widget._analyze_worker.wait(2000)
        super().closeEvent(event)
