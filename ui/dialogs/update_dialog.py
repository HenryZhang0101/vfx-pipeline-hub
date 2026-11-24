"""GitHub Releases Update Dialog & background check/apply workers."""

import webbrowser
from typing import Optional

from qtpy.QtCore import Qt, QThread, Signal
from qtpy.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from config import APP_VERSION
from core.updater import (
    UpdateInfo,
    check_for_updates,
    download_and_apply_update,
    relaunch_application,
)
from ui.theme import get_active_stylesheet, get_app_icon


class CheckUpdateWorker(QThread):
    """Non-blocking background worker that queries GitHub's Latest Release API."""

    finished_info = Signal(object)  # UpdateInfo

    def __init__(self, parent=None, repo: str = "", current_version: str = APP_VERSION):
        super().__init__(parent)
        self._repo = repo
        self._current_version = current_version

    def run(self) -> None:
        info = check_for_updates(repo=self._repo, current_version=self._current_version)
        self.finished_info.emit(info)


class ApplyUpdateWorker(QThread):
    """Non-blocking background worker that downloads and applies a release zipball."""

    progress_changed = Signal(int, int, str)  # done_bytes, total_bytes, status_text
    apply_finished = Signal(bool, str)  # ok, message

    def __init__(self, parent=None, update_info: Optional[UpdateInfo] = None):
        super().__init__(parent)
        self._info = update_info

    def run(self) -> None:
        if not self._info:
            self.apply_finished.emit(False, "No update metadata provided.")
            return
        ok, msg = download_and_apply_update(
            self._info,
            progress_cb=lambda d, t, m: self.progress_changed.emit(d, t, m),
        )
        self.apply_finished.emit(ok, msg)


class UpdateDialog(QDialog):
    """Notion Dark dialog for checking, downloading, and installing GitHub Releases updates."""

    def __init__(
        self,
        parent=None,
        initial_info: Optional[UpdateInfo] = None,
        entry_script: str = "project_manager.py",
        relaunch_args: Optional[list] = None,
    ):
        super().__init__(parent)
        self._entry_script = entry_script
        self._relaunch_args = list(relaunch_args or [])
        self._info: Optional[UpdateInfo] = initial_info
        self._check_worker: Optional[CheckUpdateWorker] = None
        self._apply_worker: Optional[ApplyUpdateWorker] = None
        self._update_installed: bool = False

        self.setWindowTitle("Software Update — VFX Pipeline Hub")
        self.setWindowIcon(get_app_icon())
        self.resize(560, 400)
        self.setStyleSheet(get_active_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        # Header card
        hdr = QFrame()
        hdr.setObjectName("card_frame")
        hdr_lay = QVBoxLayout(hdr)
        hdr_lay.setContentsMargins(12, 10, 12, 10)
        hdr_lay.setSpacing(4)

        self.status_title = QLabel("Checking for updates...")
        self.status_title.setStyleSheet("font-size: 14px; font-weight: 600; color: #E3E2E0;")
        hdr_lay.addWidget(self.status_title)

        self.version_lbl = QLabel(f"Installed version: v{APP_VERSION}")
        self.version_lbl.setStyleSheet("color: #9B9A97; font-size: 12px;")
        hdr_lay.addWidget(self.version_lbl)
        layout.addWidget(hdr)

        # Release notes viewer
        notes_lbl = QLabel("Release Notes")
        notes_lbl.setStyleSheet("color: #9B9A97; font-weight: 600;")
        layout.addWidget(notes_lbl)

        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setReadOnly(True)
        self.notes_edit.setPlaceholderText("Release notes will appear here when an update is found.")
        layout.addWidget(self.notes_edit, 1)

        # Progress bar + status
        self.progress_lbl = QLabel("")
        self.progress_lbl.setStyleSheet("color: #9B9A97; font-size: 11px;")
        self.progress_lbl.setVisible(False)
        layout.addWidget(self.progress_lbl)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.check_btn = QPushButton("Check Again")
        self.check_btn.clicked.connect(self.start_check)
        btn_row.addWidget(self.check_btn)

        self.github_btn = QPushButton("View on GitHub")
        self.github_btn.setVisible(False)
        self.github_btn.clicked.connect(self._on_open_github)
        btn_row.addWidget(self.github_btn)

        btn_row.addStretch(1)

        self.install_btn = QPushButton("Download & Install Update")
        self.install_btn.setObjectName("primary_btn")
        self.install_btn.setVisible(False)
        self.install_btn.clicked.connect(self._on_install_update)
        btn_row.addWidget(self.install_btn)

        self.restart_btn = QPushButton("Restart Now")
        self.restart_btn.setObjectName("success_btn")
        self.restart_btn.setVisible(False)
        self.restart_btn.clicked.connect(self._on_restart_now)
        btn_row.addWidget(self.restart_btn)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.close_btn)

        layout.addLayout(btn_row)

        if initial_info is not None:
            self._render_info(initial_info)
        else:
            self.start_check()

    def start_check(self) -> None:
        if self._check_worker and self._check_worker.isRunning():
            return
        self.status_title.setText("Checking GitHub for updates...")
        self.version_lbl.setText(f"Installed version: v{APP_VERSION}")
        self.notes_edit.clear()
        self.install_btn.setVisible(False)
        self.github_btn.setVisible(False)
        self.restart_btn.setVisible(False)
        self.check_btn.setEnabled(False)
        self.progress_lbl.setText("Contacting GitHub Releases...")
        self.progress_lbl.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)

        self._check_worker = CheckUpdateWorker(self)
        self._check_worker.finished_info.connect(self._on_check_finished)
        self._check_worker.start()

    def _on_check_finished(self, info: UpdateInfo) -> None:
        self._info = info
        self.check_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_lbl.setVisible(False)
        self._render_info(info)

    def _render_info(self, info: UpdateInfo) -> None:
        self.github_btn.setVisible(bool(info.html_url))
        if info.error:
            self.status_title.setText("Could not check for updates")
            self.version_lbl.setText(f"Installed version: v{info.current_version}")
            self.notes_edit.setPlainText(info.error)
            self.install_btn.setVisible(False)
            return

        if info.available:
            self.status_title.setText(f"Update available: {info.release_name or info.tag_name}")
            pub = f"  •  Published {info.published_at[:10]}" if info.published_at else ""
            self.version_lbl.setText(
                f"Installed: v{info.current_version}   →   Latest: v{info.latest_version}{pub}"
            )
            self.notes_edit.setPlainText(info.release_notes or "(No release notes provided.)")
            self.install_btn.setVisible(True)
            self.install_btn.setEnabled(True)
        else:
            self.status_title.setText("You're up to date")
            self.version_lbl.setText(
                f"Installed: v{info.current_version}   •   Latest release: v{info.latest_version or info.current_version}"
            )
            self.notes_edit.setPlainText(
                info.release_notes or f"VFX Pipeline Hub v{info.current_version} is the latest release."
            )
            self.install_btn.setVisible(False)

    def _on_open_github(self) -> None:
        if self._info and self._info.html_url:
            webbrowser.open(self._info.html_url)

    def _on_install_update(self) -> None:
        if not self._info or (self._apply_worker and self._apply_worker.isRunning()):
            return
        self.install_btn.setEnabled(False)
        self.check_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.progress_lbl.setText("Starting download...")
        self.progress_lbl.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)

        self._apply_worker = ApplyUpdateWorker(self, update_info=self._info)
        self._apply_worker.progress_changed.connect(self._on_apply_progress)
        self._apply_worker.apply_finished.connect(self._on_apply_finished)
        self._apply_worker.start()

    def _on_apply_progress(self, done: int, total: int, msg: str) -> None:
        self.progress_lbl.setText(msg)
        if total > 0:
            self.progress_bar.setRange(0, 100)
            pct = max(0, min(100, int(round((done / float(total)) * 100))))
            self.progress_bar.setValue(pct)
        else:
            self.progress_bar.setRange(0, 0)

    def _on_apply_finished(self, ok: bool, msg: str) -> None:
        self.close_btn.setEnabled(True)
        self.check_btn.setEnabled(True)
        if ok:
            self._update_installed = True
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100)
            self.progress_lbl.setText(msg)
            self.status_title.setText("Update installed — restart required")
            self.install_btn.setVisible(False)
            self.restart_btn.setVisible(True)
        else:
            self.progress_bar.setVisible(False)
            self.progress_lbl.setText("")
            self.install_btn.setEnabled(True)
            QMessageBox.warning(self, "Update Failed", msg)

    def _on_restart_now(self) -> None:
        if relaunch_application(self._entry_script, extra_args=self._relaunch_args):
            self.accept()
            app = QApplication.instance()
            if app:
                app.quit()
        else:
            QMessageBox.information(
                self,
                "Restart Manually",
                "Update is installed. Please close and reopen Project Manager to load the new version.",
            )

    def closeEvent(self, event) -> None:
        if self._apply_worker and self._apply_worker.isRunning():
            event.ignore()
            return
        if self._check_worker and self._check_worker.isRunning():
            self._check_worker.wait(1000)
        super().closeEvent(event)
