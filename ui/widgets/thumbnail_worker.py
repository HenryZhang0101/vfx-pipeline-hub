"""
Async parallel thumbnail generation service & Notion-Dark rounded QPixmap LRU renderer/cache.
"""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import itertools
import os
import queue
import threading
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from qtpy.QtCore import QObject, QRectF, Qt, QThread, Signal
from qtpy.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

from core.models import MediaItem, Project, Shot
from core.thumbnails import (
    generate_media_thumbnail,
    generate_shot_thumbnail,
    get_media_thumbnail_path,
    get_shot_thumbnail_path,
)


# Bounded LRU QPixmap cache keyed by (norm_path, width, height, placeholder_text)
_PIXMAP_CACHE: "OrderedDict[Tuple[str, int, int, str], QPixmap]" = OrderedDict()
_ICON_CACHE: "OrderedDict[Tuple[str, int, int, str], QIcon]" = OrderedDict()
_MAX_CACHE_ENTRIES = 2048


def clear_thumbnail_pixmap_cache(path: str = "") -> None:
    """Clears the entire pixmap/icon cache or entries matching a specific file path."""
    if not path:
        _PIXMAP_CACHE.clear()
        _ICON_CACHE.clear()
        return
    norm = os.path.normcase(os.path.normpath(path))
    for cache in (_PIXMAP_CACHE, _ICON_CACHE):
        stale = [k for k in cache if k[0] and os.path.normcase(k[0]) == norm]
        for k in stale:
            cache.pop(k, None)


def render_thumbnail_pixmap(
    path: str,
    width: int = 80,
    height: int = 45,
    placeholder_text: str = "—",
    radius: float = 4.0,
) -> QPixmap:
    """
    Renders a crisp rounded 16:9 QPixmap (letterboxed/aspect-fit) or a clean
    Notion-Dark placeholder tile when the thumbnail does not exist yet.
    Uses a bounded LRU cache without per-paint os.stat() syscalls; call
    clear_thumbnail_pixmap_cache(path) when a thumbnail file is written/replaced.
    """
    norm_path = os.path.normpath(path) if path else ""
    cache_key = (norm_path, width, height, placeholder_text)
    cached = _PIXMAP_CACHE.get(cache_key)
    if cached is not None:
        _PIXMAP_CACHE.move_to_end(cache_key)
        return cached

    exists = bool(norm_path and os.path.isfile(norm_path))
    if not exists:
        # Share a single placeholder pixmap across all missing thumbnails of the same size/text
        placeholder_key = ("", width, height, placeholder_text)
        ph_cached = _PIXMAP_CACHE.get(placeholder_key)
        if ph_cached is not None:
            _PIXMAP_CACHE.move_to_end(placeholder_key)
            return ph_cached
        cache_key = placeholder_key

    canvas = QPixmap(width, height)
    canvas.fill(Qt.transparent)

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    rect = QRectF(0.5, 0.5, width - 1.0, height - 1.0)
    clip_path = QPainterPath()
    clip_path.addRoundedRect(rect, radius, radius)

    # Dark canvas background
    painter.fillPath(clip_path, QColor("#191919"))

    drew_image = False
    if exists:
        src = QPixmap(norm_path)
        if not src.isNull():
            scaled = src.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (width - scaled.width()) // 2
            y = (height - scaled.height()) // 2
            painter.save()
            painter.setClipPath(clip_path)
            painter.drawPixmap(x, y, scaled)
            painter.restore()
            drew_image = True

    if not drew_image and placeholder_text:
        painter.setPen(QColor("#5A5956"))
        font = QFont("Segoe UI", 8 if len(placeholder_text) > 2 else 9, QFont.DemiBold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, placeholder_text)

    # Subtle Notion hairline border
    painter.setPen(QPen(QColor("#2F2F2F"), 1.0))
    painter.drawPath(clip_path)
    painter.end()

    _PIXMAP_CACHE[cache_key] = canvas
    if len(_PIXMAP_CACHE) > _MAX_CACHE_ENTRIES:
        _PIXMAP_CACHE.popitem(last=False)
    return canvas


def render_thumbnail_icon(
    path: str,
    width: int = 80,
    height: int = 45,
    placeholder_text: str = "—",
    radius: float = 4.0,
) -> QIcon:
    norm_path = os.path.normpath(path) if path else ""
    cache_key = (norm_path, width, height, placeholder_text)
    cached = _ICON_CACHE.get(cache_key)
    if cached is not None:
        _ICON_CACHE.move_to_end(cache_key)
        return cached
    pix = render_thumbnail_pixmap(norm_path, width, height, placeholder_text, radius)
    # Only cache under norm_path if the file actually exists (otherwise placeholder is shared)
    store_key = cache_key if (norm_path and cache_key in _PIXMAP_CACHE) else ("", width, height, placeholder_text)
    if store_key in _ICON_CACHE:
        _ICON_CACHE.move_to_end(store_key)
        return _ICON_CACHE[store_key]
    icon = QIcon(pix)
    _ICON_CACHE[store_key] = icon
    if len(_ICON_CACHE) > _MAX_CACHE_ENTRIES:
        _ICON_CACHE.popitem(last=False)
    return icon


class _ThumbnailWorkerThread(QThread):
    """
    Background dispatcher running thumbnail generation jobs across a bounded ThreadPoolExecutor
    with priority ordering (visible items first) and independent media-queue cancellation.
    """

    media_thumbnail_ready = Signal(str, str, str)  # shot_id, version_dir, thumb_path
    shot_thumbnail_ready = Signal(str, str)        # shot_id, thumb_path
    batch_progress = Signal(int, int, str)         # done, total, label
    batch_finished = Signal(int)                   # generated_count

    def __init__(self, max_workers: int = 3, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._max_workers = max(1, int(max_workers))
        self._queue: "queue.PriorityQueue[Tuple[int, int, Optional[Tuple[Any, ...]]]]" = queue.PriorityQueue()
        self._seq = itertools.count()
        self._pending_keys: Set[str] = set()
        self._lock = threading.Lock()
        self._stop_flag = False
        self._generation_id = 0
        self._media_generation_id = 0

    def enqueue(self, job_key: str, job: Tuple[Any, ...], priority: int = 1) -> None:
        with self._lock:
            if self._stop_flag:
                return
            if job_key and job_key in self._pending_keys:
                return
            if job_key:
                self._pending_keys.add(job_key)
            gen = self._generation_id
            m_gen = self._media_generation_id
            order = next(self._seq)
        self._queue.put((priority, order, (gen, m_gen, job_key, job)))

    def clear_pending(self) -> None:
        """Drops all queued jobs (shots + media) and increments both generation counters."""
        with self._lock:
            self._generation_id += 1
            self._media_generation_id += 1
            self._pending_keys.clear()
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break

    def clear_media_pending(self) -> None:
        """Cancels queued media thumbnail jobs when switching shots without affecting shot thumbnails."""
        with self._lock:
            self._media_generation_id += 1
            self._pending_keys = {k for k in self._pending_keys if not k.startswith("media:")}

    def request_stop(self) -> None:
        with self._lock:
            self._stop_flag = True
            self._generation_id += 1
            self._media_generation_id += 1
            self._pending_keys.clear()
            order = next(self._seq)
        self._queue.put((-1, order, None))

    def _is_stale(self, gen: int, m_gen: int, kind: str) -> bool:
        with self._lock:
            if self._stop_flag or gen != self._generation_id:
                return True
            if kind == "media" and m_gen != self._media_generation_id:
                return True
            return False

    def _execute_job(self, gen: int, m_gen: int, job: Tuple[Any, ...]) -> None:
        kind = job[0]
        if self._is_stale(gen, m_gen, kind):
            return
        try:
            if kind == "media":
                _, project, media_dict, force = job
                out_expected = media_dict.get("thumb_path") or get_media_thumbnail_path(
                    project,
                    media_dict.get("shot_id", ""),
                    media_dict.get("category", "media"),
                    media_dict.get("identifier", "plate"),
                    media_dict.get("version", "v0001"),
                )
                if not force and out_expected and os.path.isfile(out_expected) and os.path.getsize(out_expected) > 0:
                    return
                out = generate_media_thumbnail(project, media_dict, force=force)
                if out and not self._is_stale(gen, m_gen, kind):
                    self.media_thumbnail_ready.emit(
                        media_dict.get("shot_id", ""),
                        media_dict.get("version_dir", ""),
                        out,
                    )

            elif kind == "shot":
                _, project, repo, shot, force, media_dict = job
                out_expected = get_shot_thumbnail_path(project, shot.shot_id)
                if not force and out_expected and os.path.isfile(out_expected) and os.path.getsize(out_expected) > 0:
                    return
                out = generate_shot_thumbnail(
                    project, repo, shot, force=force, media_dict=media_dict
                )
                if out and not self._is_stale(gen, m_gen, kind):
                    if media_dict and media_dict.get("thumb_path"):
                        self.media_thumbnail_ready.emit(
                            shot.shot_id,
                            media_dict.get("version_dir", ""),
                            media_dict["thumb_path"],
                        )
                    self.shot_thumbnail_ready.emit(shot.shot_id, out)

            elif kind == "batch_project":
                _, project, repo, force = job
                shots: List[Shot] = list(project.all_shots()) if project else []
                total = len(shots)
                generated = 0
                for idx, sh in enumerate(shots):
                    if self._is_stale(gen, m_gen, kind):
                        break
                    self.batch_progress.emit(idx + 1, total, sh.shot_id)
                    media_items = repo.scan_all_shot_media(project, sh.shot_id)
                    for m in media_items:
                        if self._is_stale(gen, m_gen, kind):
                            break
                        if not m.get("is_playable"):
                            continue
                        existed = os.path.isfile(m.get("thumb_path") or "")
                        out_m = generate_media_thumbnail(project, m, force=force)
                        if out_m and (force or not existed):
                            generated += 1
                            self.media_thumbnail_ready.emit(
                                sh.shot_id, m.get("version_dir", ""), out_m
                            )
                    if self._is_stale(gen, m_gen, kind):
                        break
                    shot_png = get_shot_thumbnail_path(project, sh.shot_id)
                    shot_existed = os.path.isfile(shot_png)
                    out_s = generate_shot_thumbnail(project, repo, sh, force=force)
                    if out_s and (force or not shot_existed):
                        generated += 1
                        self.shot_thumbnail_ready.emit(sh.shot_id, out_s)
                if not self._is_stale(gen, m_gen, kind):
                    self.batch_finished.emit(generated)
        except Exception as e:
            print(f"Warning in thumbnail worker ({kind}): {e}")

    def run(self) -> None:
        sem = threading.Semaphore(self._max_workers)
        with ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="thumb") as pool:
            while True:
                _, _, payload = self._queue.get()
                if payload is None:
                    break
                gen, m_gen, job_key, job = payload
                if job_key:
                    with self._lock:
                        self._pending_keys.discard(job_key)
                kind = job[0]
                if self._is_stale(gen, m_gen, kind):
                    continue

                if kind == "batch_project":
                    self._execute_job(gen, m_gen, job)
                    continue

                sem.acquire()
                if self._is_stale(gen, m_gen, kind):
                    sem.release()
                    continue

                def _run_and_release(g=gen, mg=m_gen, j=job):
                    try:
                        self._execute_job(g, mg, j)
                    finally:
                        sem.release()

                pool.submit(_run_and_release)


class ThumbnailService(QObject):
    """High-level controller managing background thumbnail generation for a Qt window."""

    media_thumbnail_ready = Signal(str, str, str)  # shot_id, version_dir, thumb_path
    shot_thumbnail_ready = Signal(str, str)        # shot_id, thumb_path
    batch_progress = Signal(int, int, str)         # done, total, label
    batch_finished = Signal(int)                   # generated_count

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._worker = _ThumbnailWorkerThread(max_workers=3, parent=self)
        self._worker.media_thumbnail_ready.connect(self._on_media_ready)
        self._worker.shot_thumbnail_ready.connect(self._on_shot_ready)
        self._worker.batch_progress.connect(self.batch_progress)
        self._worker.batch_finished.connect(self.batch_finished)
        self._worker.start()

    def _on_media_ready(self, shot_id: str, version_dir: str, thumb_path: str) -> None:
        clear_thumbnail_pixmap_cache(thumb_path)
        self.media_thumbnail_ready.emit(shot_id, version_dir, thumb_path)

    def _on_shot_ready(self, shot_id: str, thumb_path: str) -> None:
        clear_thumbnail_pixmap_cache(thumb_path)
        self.shot_thumbnail_ready.emit(shot_id, thumb_path)

    @staticmethod
    def _snapshot_media(media_obj: Union[MediaItem, Dict[str, Any]]) -> Dict[str, Any]:
        if isinstance(media_obj, MediaItem):
            return media_obj.to_dict()
        return dict(media_obj)

    def queue_media(
        self,
        project: Project,
        media_dict: Union[MediaItem, Dict[str, Any]],
        force: bool = False,
        priority: int = 1,
    ) -> None:
        if not project or not media_dict or not media_dict.get("is_playable"):
            return
        if not force and media_dict.get("has_thumb"):
            return
        out = media_dict.get("thumb_path") or get_media_thumbnail_path(
            project,
            media_dict.get("shot_id", ""),
            media_dict.get("category", "media"),
            media_dict.get("identifier", "plate"),
            media_dict.get("version", "v0001"),
        )
        key = f"media:{out}:{'1' if force else '0'}"
        self._worker.enqueue(key, ("media", project, self._snapshot_media(media_dict), force), priority=priority)

    def queue_shot(
        self,
        project: Project,
        repo: Any,
        shot: Shot,
        force: bool = False,
        media_dict: Optional[Union[MediaItem, Dict[str, Any]]] = None,
        priority: int = 1,
    ) -> None:
        if not project or not shot or not shot.shot_id:
            return
        out = get_shot_thumbnail_path(project, shot.shot_id)
        key = f"shot:{out}:{'1' if force else '0'}:{media_dict.get('version_dir', '') if media_dict else ''}"
        self._worker.enqueue(
            key,
            ("shot", project, repo, shot, force, self._snapshot_media(media_dict) if media_dict else None),
            priority=priority,
        )

    def queue_project_batch(self, project: Project, repo: Any, force: bool = False) -> None:
        if not project or not repo:
            return
        key = f"batch:{project.vfx_root}:{'1' if force else '0'}"
        self._worker.enqueue(key, ("batch_project", project, repo, force), priority=2)

    def clear_pending(self) -> None:
        self._worker.clear_pending()

    def clear_media_pending(self) -> None:
        self._worker.clear_media_pending()

    def shutdown(self) -> None:
        self._worker.request_stop()
        if self._worker.isRunning():
            self._worker.wait(2500)
