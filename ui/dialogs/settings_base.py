"""Reusable Notion-style left-sidebar settings dialog base class."""

from typing import Dict, List, Optional, Tuple

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.theme import get_active_stylesheet


class SidebarSettingsDialog(QDialog):
    """
    Minimalist Notion-Dark settings dialog with a left navigation sidebar,
    a stacked content pane on the right, and a hairline-separated footer.
    """

    def __init__(
        self,
        parent=None,
        title: str = "Settings",
        save_label: str = "Save",
        width: int = 760,
        height: int = 500,
    ):
        super().__init__(parent)
        self._page_indices: Dict[str, int] = {}

        self.setWindowTitle(title)
        self.resize(width, height)
        self.setStyleSheet(get_active_stylesheet())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Body: Left Sidebar | Right Stacked Pages
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("settings_sidebar")
        self.sidebar.setFixedWidth(180)
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sidebar.currentRowChanged.connect(self._on_sidebar_row_changed)
        body.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        # Footer Bar
        footer = QFrame()
        footer.setObjectName("settings_footer")
        f_lay = QHBoxLayout(footer)
        f_lay.setContentsMargins(14, 10, 14, 10)
        f_lay.setSpacing(8)
        f_lay.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        f_lay.addWidget(self.cancel_btn)

        self.save_btn = QPushButton(save_label)
        self.save_btn.setObjectName("primary_btn")
        self.save_btn.clicked.connect(self._on_save_clicked)
        f_lay.addWidget(self.save_btn)

        outer.addWidget(footer)

    def add_page(self, name: str, subtitle: str = "") -> Tuple[QWidget, QVBoxLayout]:
        """Register a named page in the sidebar and return (page_widget, content_layout)."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title_lbl = QLabel(name)
        title_lbl.setObjectName("settings_page_title")
        layout.addWidget(title_lbl)

        if subtitle:
            sub_lbl = QLabel(subtitle)
            sub_lbl.setObjectName("settings_page_subtitle")
            sub_lbl.setWordWrap(True)
            layout.addWidget(sub_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background-color: #2F2F2F; max-height: 1px; border: none;")
        layout.addWidget(sep)

        content_lay = QVBoxLayout()
        content_lay.setContentsMargins(0, 4, 0, 0)
        content_lay.setSpacing(10)
        layout.addLayout(content_lay, 1)

        idx = self.stack.addWidget(page)
        self._page_indices[name.lower()] = idx
        item = QListWidgetItem(name)
        self.sidebar.addItem(item)

        if self.sidebar.count() == 1:
            self.sidebar.setCurrentRow(0)

        return page, content_lay

    def select_page(self, name_or_index) -> None:
        """Switch to a page by index or case-insensitive title."""
        if isinstance(name_or_index, int):
            if 0 <= name_or_index < self.sidebar.count():
                self.sidebar.setCurrentRow(name_or_index)
            return
        key = str(name_or_index or "").strip().lower()
        if key in self._page_indices:
            self.sidebar.setCurrentRow(self._page_indices[key])

    def _on_sidebar_row_changed(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)

    def _on_save_clicked(self) -> None:
        if self.save_settings():
            self.accept()

    def save_settings(self) -> bool:
        """Override in subclasses to validate & persist settings. Return True to close dialog."""
        return True
