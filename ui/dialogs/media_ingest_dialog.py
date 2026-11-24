"""Modal dialog wrapper around the embeddable MediaIngestWidget."""

from typing import List

from qtpy.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout

from core.models import Project
from core.storage import ProjectRepository
from ui.theme import get_active_stylesheet
from ui.widgets.media_ingest_widget import MediaIngestWidget


class MediaIngestDialog(QDialog):
    """Modal dialog for ingesting plates, image sequences, movies, or 3D assets."""

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
        layout.addWidget(self.widget, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ingest_btn = QPushButton("Start Ingestion")
        ingest_btn.setObjectName("primary_btn")
        ingest_btn.clicked.connect(self._on_run)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ingest_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    @property
    def ingested_shot_id(self) -> str:
        return self.widget.ingested_shot_id

    def _on_run(self):
        if self.widget.run_ingest():
            self.accept()
