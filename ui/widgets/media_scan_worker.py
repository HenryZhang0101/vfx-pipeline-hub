"""Background workers for non-blocking shot media scanning and Excel export."""

import copy
import queue
import threading
from typing import Dict, List, Optional, Tuple

from qtpy.QtCore import QObject, QThread, Signal

from core.io.excel import save_shots_to_excel
from core.models import MediaItem, Project, Shot
from core.storage import ProjectRepository


class MediaScanWorker(QThread):
    """
    Dedicated background thread for scanning shot media/assets without blocking the Qt UI.
    Uses a monotonic generation counter so rapid shot selection immediately cancels stale scans.
    """

    scan_finished = Signal(int, list, list)  # generation_id, shot_ids, List[MediaItem]

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stop_flag = False
        self._generation_id = 0
        self._pending_request: Optional[Tuple[int, Project, ProjectRepository, List[str], bool]] = None

    def request_scan(
        self,
        project: Project,
        repo: ProjectRepository,
        shot_ids: List[str],
        use_cache: bool = True,
    ) -> int:
        """Queues a new scan request, superseding any in-flight or queued scan. Returns generation_id."""
        with self._cond:
            self._generation_id += 1
            gen = self._generation_id
            self._pending_request = (gen, project, repo, list(shot_ids), use_cache)
            self._cond.notify()
            return gen

    def cancel_pending(self) -> int:
        """Invalidates any in-flight scan so its results are dropped."""
        with self._cond:
            self._generation_id += 1
            self._pending_request = None
            return self._generation_id

    def request_stop(self) -> None:
        with self._cond:
            self._stop_flag = True
            self._generation_id += 1
            self._pending_request = None
            self._cond.notify_all()

    def _is_stale(self, gen: int) -> bool:
        with self._lock:
            return self._stop_flag or gen != self._generation_id

    def run(self) -> None:
        while True:
            with self._cond:
                while not self._stop_flag and self._pending_request is None:
                    self._cond.wait()
                if self._stop_flag:
                    return
                req = self._pending_request
                self._pending_request = None

            if req is None:
                continue

            gen, project, repo, shot_ids, use_cache = req
            if self._is_stale(gen):
                continue

            aggregated: List[MediaItem] = []
            seen = set()
            aborted = False
            try:
                for sid in shot_ids:
                    if self._is_stale(gen):
                        aborted = True
                        break
                    if not sid or sid in seen:
                        continue
                    seen.add(sid)
                    aggregated.extend(repo.scan_all_shot_media(project, sid, use_cache=use_cache))
            except Exception as e:
                print(f"Warning in MediaScanWorker: {e}")

            if not aborted and not self._is_stale(gen):
                self.scan_finished.emit(gen, shot_ids, aggregated)


class ExcelExportWorker(QThread):
    """Background worker for writing <Project>_ShotList.xlsx without freezing the UI."""

    export_finished = Signal(bool, str, int)  # ok, timestamp, shot_count

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._queue: "queue.Queue[Optional[Tuple]]" = queue.Queue()

    def enqueue_export(self, project: Project, timestamp: str, file_path: str = "") -> None:
        """Snapshots project shot data on the main thread and queues a background Excel write."""
        target_path = (file_path or "").strip() or (project.default_excel_path if project else "")
        if not project or not target_path:
            return
        shots_snapshot = [copy.deepcopy(s) for s in project.all_shots()]
        payload = (
            target_path,
            shots_snapshot,
            project.thumbnails_dir,
            project.name,
            timestamp,
            list(project.shot_custom_fields),
            dict(project.get_status_property().color_map()),
        )
        # Drop any older queued export so we only write the latest state
        while not self._queue.empty():
            try:
                old = self._queue.get_nowait()
                if old is None:
                    self._queue.put(None)
                    return
            except queue.Empty:
                break
        self._queue.put(payload)

    def request_stop(self) -> None:
        self._queue.put(None)

    def run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            (
                excel_path,
                shots_snapshot,
                thumb_dir,
                project_name,
                timestamp,
                custom_fields,
                status_colors,
            ) = item
            try:
                ok = save_shots_to_excel(
                    excel_path,
                    shots_snapshot,
                    thumb_dir=thumb_dir,
                    project_name=project_name,
                    last_modified=timestamp,
                    custom_field_names=custom_fields,
                    status_colors=status_colors,
                )
            except Exception as e:
                print(f"Warning in ExcelExportWorker: {e}")
                ok = False
            self.export_finished.emit(ok, timestamp, len(shots_snapshot))
