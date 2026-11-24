"""Media Manager panel: virtualized QTableView multi-shot media/asset browser, filters, color tags, DJV & QT exports."""

import os
from typing import Any, Dict, List, Optional, Set, Union

from qtpy.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from qtpy.QtGui import QColor, QFont, QIcon
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from config import open_path_in_os
from core.models import MediaItem, PROPERTY_COLOR_PALETTE, Project, Shot
from core.storage import ProjectRepository
from tools import ToolRegistry
from ui.theme import get_active_stylesheet
from ui.widgets.property_widgets import make_color_dot_icon
from ui.widgets.thumbnail_worker import render_thumbnail_icon


_SORT_ROLE = Qt.UserRole + 10

_SHOT_FONT = QFont("Segoe UI", 9, QFont.DemiBold)
_IDENT_FONT = QFont("Segoe UI", 10, QFont.Bold)
_VER_COLOR = QColor("#98c379")
_MOD_COLOR = QColor("#9B9A97")
_TEXT_COLOR = QColor("#E3E2E0")
_PLATE_COLOR = QColor("#e5c07b")
_RENDER_COLOR = QColor("#61afef")
_EMPTY_ICON = QIcon()

_HEADERS = (
    "Preview",
    "Shot",
    "Category",
    "Identifier",
    "Version",
    "Range / Count",
    "Modified",
    "Sequence / File",
)


class MediaTableModel(QAbstractTableModel):
    """
    Virtualized table model for MediaItem lists.
    Thumbnails are rendered lazily only for visible rows; thumbnail & metadata updates are O(1).
    """

    COL_PREVIEW = 0
    COL_SHOT = 1
    COL_CATEGORY = 2
    COL_IDENTIFIER = 3
    COL_VERSION = 4
    COL_RANGE = 5
    COL_MODIFIED = 6
    COL_FILE = 7

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._items: List[MediaItem] = []
        self._row_by_vdir: Dict[str, int] = {}
        self._row_by_key: Dict[str, int] = {}
        self._latest_keys: Set[str] = set()

    def set_items(self, items: List[MediaItem]) -> None:
        self.beginResetModel()
        self._items = list(items or [])
        self._row_by_vdir = {}
        self._row_by_key = {}
        best_by_group: Dict[Any, MediaItem] = {}
        for idx, m in enumerate(self._items):
            if m.version_dir:
                self._row_by_vdir[os.path.normcase(os.path.normpath(m.version_dir))] = idx
            self._row_by_key[m.media_key] = idx
            gk = m.group_key
            prev = best_by_group.get(gk)
            if prev is None or (m.version_num, m.version.lower()) > (prev.version_num, prev.version.lower()):
                best_by_group[gk] = m
        self._latest_keys = {m.media_key for m in best_by_group.values()}
        self.endResetModel()

    def items(self) -> List[MediaItem]:
        return self._items

    def item_at(self, row: int) -> Optional[MediaItem]:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def is_latest(self, media_key: str) -> bool:
        return media_key in self._latest_keys

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._items)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(_HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < len(_HEADERS):
            return _HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._items):
            return None
        m = self._items[row]
        col = index.column()

        if role == Qt.UserRole:
            return m

        if col == self.COL_PREVIEW:
            if role == Qt.DecorationRole:
                placeholder = "3D" if not m.is_playable else "—"
                return render_thumbnail_icon(m.thumb_path, 80, 45, placeholder_text=placeholder)
            return None

        if col == self.COL_SHOT:
            if role == Qt.DisplayRole:
                return m.shot_id
            if role == Qt.FontRole:
                return _SHOT_FONT
            if role == _SORT_ROLE:
                return m.shot_id.lower()
            return None

        if col == self.COL_CATEGORY:
            if role == Qt.DisplayRole:
                return m.category
            if role == _SORT_ROLE:
                return m.category.lower()
            return None

        if col == self.COL_IDENTIFIER:
            if role == Qt.DisplayRole:
                return m.identifier
            if role == Qt.FontRole:
                return _IDENT_FONT
            if role == Qt.DecorationRole:
                return make_color_dot_icon(m.color, 10) if m.color else _EMPTY_ICON
            if role == Qt.ForegroundRole:
                if m.color:
                    return _TEXT_COLOR
                return _PLATE_COLOR if m.identifier.lower() == "plate" else _RENDER_COLOR
            if role == _SORT_ROLE:
                return m.identifier.lower()
            return None

        if col == self.COL_VERSION:
            if role == Qt.DisplayRole:
                return m.version
            if role == Qt.ForegroundRole:
                return _VER_COLOR
            if role == _SORT_ROLE:
                return (m.version_num, m.version.lower())
            return None

        if col == self.COL_RANGE:
            if role == Qt.DisplayRole:
                return m.range_display
            if role == _SORT_ROLE:
                return m.frame_count
            return None

        if col == self.COL_MODIFIED:
            if role == Qt.DisplayRole:
                return m.modified_at or "—"
            if role == Qt.ForegroundRole:
                return _MOD_COLOR
            if role == _SORT_ROLE:
                return m.mtime
            return None

        if col == self.COL_FILE:
            if role == Qt.DisplayRole:
                return f"{m.display_file}   ·   {m.notes}" if m.notes else m.display_file
            if role == Qt.ToolTipRole:
                tip_lines = [m.first_file_path or m.version_dir]
                if m.notes:
                    tip_lines.append(f"Note: {m.notes}")
                return "\n".join(p for p in tip_lines if p)
            if role == _SORT_ROLE:
                return m.display_file.lower()
            return None

        return None

    def notify_thumbnail_updated(self, version_dir: str, thumb_path: str) -> None:
        if not version_dir:
            return
        norm_vdir = os.path.normcase(os.path.normpath(version_dir))
        row = self._row_by_vdir.get(norm_vdir, -1)
        if row < 0:
            return
        m = self._items[row]
        m.thumb_path = thumb_path
        m.has_thumb = bool(thumb_path and os.path.isfile(thumb_path))
        idx = self.index(row, self.COL_PREVIEW)
        self.dataChanged.emit(idx, idx, [Qt.DecorationRole])

    def notify_keys_updated(self, media_keys: Set[str], cols: Optional[List[int]] = None) -> None:
        for mk in media_keys:
            row = self._row_by_key.get(mk, -1)
            if row < 0:
                continue
            if cols:
                for c in cols:
                    idx = self.index(row, c)
                    self.dataChanged.emit(idx, idx)
            else:
                self.dataChanged.emit(self.index(row, 0), self.index(row, len(_HEADERS) - 1))


class MediaFilterProxyModel(QSortFilterProxyModel):
    """Fast in-memory filter & typed-sort proxy for MediaTableModel."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._cat_filter: str = "all"
        self._id_filter: str = "all"
        self._color_filter: str = "all"
        self._search_text: str = ""
        self._latest_only: bool = False

    def set_filters(
        self,
        cat_filter: str,
        id_filter: str,
        color_filter: str,
        search_text: str,
        latest_only: bool,
    ) -> None:
        c = cat_filter or "all"
        i = id_filter or "all"
        col = color_filter or "all"
        s = (search_text or "").strip()
        lo = bool(latest_only)
        if (
            c == self._cat_filter
            and i == self._id_filter
            and col == self._color_filter
            and s == self._search_text
            and lo == self._latest_only
        ):
            return
        self._cat_filter = c
        self._id_filter = i
        self._color_filter = col
        self._search_text = s
        self._latest_only = lo
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model: MediaTableModel = self.sourceModel()
        if model is None:
            return True
        m = model.item_at(source_row)
        if m is None:
            return False
        if self._latest_only and not model.is_latest(m.media_key):
            return False
        return m.matches_filter(
            category_filter=self._cat_filter,
            identifier_filter=self._id_filter,
            color_filter=self._color_filter,
            search_text=self._search_text,
        )

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        a = left.data(_SORT_ROLE)
        b = right.data(_SORT_ROLE)
        if a is not None and b is not None and type(a) is type(b):
            return a < b
        return str(left.data(Qt.DisplayRole) or "").lower() < str(right.data(Qt.DisplayRole) or "").lower()


class MediaManagerPanel(QWidget):
    """
    Production-first Media Manager workspace for one or more selected shots:
      - Browse ingested plates, comp renders, 3D renders, caches, and playblasts
      - Aggregates media & assets across all selected shots when multi-selecting
      - Filter by search query, Category, Identifier/Type, Color tag, and Latest Only
      - Tag media items with Notion-Dark colors and notes (persisted in project.json)
      - Multi-select clips for DJV playback, QT Review/Delivery exports, and thumbnail actions
    """

    ingest_requested = Signal(str)
    qt_export_from_media_requested = Signal(str, list)
    status_message = Signal(str)
    generate_media_thumbnails_requested = Signal(list)
    set_as_shot_thumbnail_requested = Signal(object)
    media_metadata_changed = Signal()

    # Column indices (preserved for compatibility)
    COL_PREVIEW = MediaTableModel.COL_PREVIEW
    COL_SHOT = MediaTableModel.COL_SHOT
    COL_CATEGORY = MediaTableModel.COL_CATEGORY
    COL_IDENTIFIER = MediaTableModel.COL_IDENTIFIER
    COL_VERSION = MediaTableModel.COL_VERSION
    COL_RANGE = MediaTableModel.COL_RANGE
    COL_MODIFIED = MediaTableModel.COL_MODIFIED
    COL_FILE = MediaTableModel.COL_FILE

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._repo: Optional[ProjectRepository] = None
        self._tool_registry: Optional[ToolRegistry] = None
        self._current_shots: List[Shot] = []
        self._current_shot: Optional[Shot] = None
        self._all_scanned_media: List[MediaItem] = []

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(100)
        self._search_timer.timeout.connect(self._apply_media_filter)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(6)

        # Row 1: Shot context summary + item count badge
        m_top_row = QHBoxLayout()
        m_top_row.setSpacing(8)
        self.shot_context_lbl = QLabel("No Shot Selected")
        self.shot_context_lbl.setStyleSheet("font-weight: 600; color: #E3E2E0;")
        m_top_row.addWidget(self.shot_context_lbl, 1)

        self.media_count_lbl = QLabel("")
        self.media_count_lbl.setStyleSheet("color: #9B9A97; font-size: 11px;")
        m_top_row.addWidget(self.media_count_lbl)
        layout.addLayout(m_top_row)

        # Row 2: Media Filter Bar (Search | Category | Type | Color | Latest Only)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)

        self.media_search_edit = QLineEdit()
        self.media_search_edit.setPlaceholderText("Filter media / assets...")
        self.media_search_edit.setClearButtonEnabled(True)
        self.media_search_edit.textChanged.connect(lambda _: self._search_timer.start())
        filter_row.addWidget(self.media_search_edit, 2)

        self.media_cat_combo = QComboBox()
        self.media_cat_combo.addItem("All Categories", "all")
        self.media_cat_combo.addItem("Media", "media")
        self.media_cat_combo.addItem("Assets", "assets")
        self.media_cat_combo.currentIndexChanged.connect(self._apply_media_filter)
        filter_row.addWidget(self.media_cat_combo)

        self.media_id_combo = QComboBox()
        self.media_id_combo.addItem("All Types", "all")
        self.media_id_combo.currentIndexChanged.connect(self._apply_media_filter)
        filter_row.addWidget(self.media_id_combo)

        self.media_color_combo = QComboBox()
        self.media_color_combo.addItem("All Colors", "all")
        self.media_color_combo.addItem("No Color", "none")
        for cname, chex in PROPERTY_COLOR_PALETTE:
            self.media_color_combo.addItem(make_color_dot_icon(chex, 9), cname, chex)
        self.media_color_combo.currentIndexChanged.connect(self._apply_media_filter)
        filter_row.addWidget(self.media_color_combo)

        self.latest_only_chk = QCheckBox("Latest Only")
        self.latest_only_chk.setToolTip("Show only the latest version per shot, category, and identifier")
        self.latest_only_chk.toggled.connect(self._apply_media_filter)
        filter_row.addWidget(self.latest_only_chk)

        layout.addLayout(filter_row)

        # Virtualized Media Versions Table (QTableView + Model + Proxy)
        self._model = MediaTableModel(self)
        self._proxy = MediaFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)

        self.media_table = QTableView(self)
        self.media_table.setModel(self._proxy)
        self.media_table.verticalHeader().setVisible(False)
        self.media_table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.media_table.verticalHeader().setDefaultSectionSize(52)
        self.media_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.media_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.media_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.media_table.customContextMenuRequested.connect(self._on_media_table_context_menu)
        self.media_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.media_table.setAlternatingRowColors(True)
        self.media_table.setSortingEnabled(True)
        self.media_table.setIconSize(QSize(80, 45))
        self.media_table.setWordWrap(False)

        m_hdr = self.media_table.horizontalHeader()
        m_hdr.setStretchLastSection(False)
        m_hdr.setSectionResizeMode(self.COL_PREVIEW, QHeaderView.Fixed)
        self.media_table.setColumnWidth(self.COL_PREVIEW, 92)
        m_hdr.setSectionResizeMode(self.COL_SHOT, QHeaderView.Interactive)
        self.media_table.setColumnWidth(self.COL_SHOT, 100)
        m_hdr.setSectionResizeMode(self.COL_CATEGORY, QHeaderView.Interactive)
        self.media_table.setColumnWidth(self.COL_CATEGORY, 74)
        m_hdr.setSectionResizeMode(self.COL_IDENTIFIER, QHeaderView.Interactive)
        self.media_table.setColumnWidth(self.COL_IDENTIFIER, 128)
        m_hdr.setSectionResizeMode(self.COL_VERSION, QHeaderView.Interactive)
        self.media_table.setColumnWidth(self.COL_VERSION, 72)
        m_hdr.setSectionResizeMode(self.COL_RANGE, QHeaderView.Interactive)
        self.media_table.setColumnWidth(self.COL_RANGE, 124)
        m_hdr.setSectionResizeMode(self.COL_MODIFIED, QHeaderView.Interactive)
        self.media_table.setColumnWidth(self.COL_MODIFIED, 120)
        m_hdr.setSectionResizeMode(self.COL_FILE, QHeaderView.Stretch)
        self.media_table.setColumnHidden(self.COL_SHOT, True)

        self.media_table.doubleClicked.connect(lambda _: self.play_selected_in_djv())
        layout.addWidget(self.media_table, 1)

    def _assign_context(
        self,
        project: Optional[Project],
        repo: Optional[ProjectRepository],
        tool_registry: ToolRegistry,
        shots: Union[Optional[Shot], List[Shot]],
    ) -> None:
        self._project = project
        self._repo = repo
        self._tool_registry = tool_registry

        if isinstance(shots, list):
            self._current_shots = [s for s in shots if s is not None]
        elif shots is not None:
            self._current_shots = [shots]
        else:
            self._current_shots = []
        self._current_shot = self._current_shots[0] if self._current_shots else None
        self._update_context_header()

    def set_loading_state(
        self,
        project: Optional[Project],
        repo: Optional[ProjectRepository],
        tool_registry: ToolRegistry,
        shots: Union[Optional[Shot], List[Shot]],
    ) -> None:
        """Updates header immediately while an async background media scan runs."""
        self._assign_context(project, repo, tool_registry, shots)
        if not self._current_shots:
            self.apply_scanned_media([])
        else:
            self.media_count_lbl.setText("Scanning...")

    def set_context(
        self,
        project: Optional[Project],
        repo: Optional[ProjectRepository],
        tool_registry: ToolRegistry,
        shots: Union[Optional[Shot], List[Shot]],
    ):
        """Synchronous context update + scan (retained for backwards compatibility)."""
        self._assign_context(project, repo, tool_registry, shots)
        self.refresh_media()

    def apply_scanned_media(self, media_items: List[MediaItem]) -> None:
        """Applies scanned MediaItem results from either sync or async scan."""
        self._all_scanned_media = list(media_items or [])

        # Refresh Identifier/Type filter dropdown while preserving current selection if still present
        prev_id = self.media_id_combo.currentData() or "all"
        distinct_ids = sorted({m.identifier for m in self._all_scanned_media if m.identifier}, key=str.lower)
        self.media_id_combo.blockSignals(True)
        self.media_id_combo.clear()
        self.media_id_combo.addItem("All Types", "all")
        for ident in distinct_ids:
            self.media_id_combo.addItem(ident, ident)
        idx = self.media_id_combo.findData(prev_id)
        self.media_id_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.media_id_combo.blockSignals(False)

        show_shot_col = len(self._current_shots) > 1
        self.media_table.setColumnHidden(self.COL_SHOT, not show_shot_col)

        self._model.set_items(self._all_scanned_media)
        self._apply_media_filter(select_first=True)

    def _update_context_header(self) -> None:
        count = len(self._current_shots)
        if count == 0 or not self._project:
            self.shot_context_lbl.setText("No Shot Selected")
        elif count == 1:
            shot = self._current_shots[0]
            sf, ef = shot.start_frame, shot.end_frame
            eff_fps = shot.fps if shot.fps > 0 else self._project.fps
            self.shot_context_lbl.setText(
                f"{shot.shot_id}   ·   {sf}–{ef} ({ef - sf + 1}f)   ·   {eff_fps:g} fps"
            )
        else:
            preview_ids = ", ".join(s.shot_id for s in self._current_shots[:4])
            if count > 4:
                preview_ids += f" (+{count - 4} more)"
            self.shot_context_lbl.setText(f"{count} Shots Selected   ·   {preview_ids}")

    def update_module_visibility(self, tool_registry: ToolRegistry):
        self._tool_registry = tool_registry

    def refresh_media(self):
        """Rescans disk for the currently selected shot(s) and reapplies active filters."""
        scanned: List[MediaItem] = []
        if self._current_shots and self._project and self._repo:
            shot_ids = [s.shot_id for s in self._current_shots if s.shot_id]
            scanned = self._repo.scan_shots_media(self._project, shot_ids)
        self.apply_scanned_media(scanned)

    def _apply_media_filter(self, *_args, select_first: bool = False):
        """Filters cached MediaTableModel via QSortFilterProxyModel without rebuilding rows."""
        cat_filter = self.media_cat_combo.currentData() or "all"
        id_filter = self.media_id_combo.currentData() or "all"
        color_filter = self.media_color_combo.currentData() or "all"
        search_text = self.media_search_edit.text().strip()
        latest_only = self.latest_only_chk.isChecked()

        self._proxy.set_filters(cat_filter, id_filter, color_filter, search_text, latest_only)

        total_n = len(self._all_scanned_media)
        shown_n = self._proxy.rowCount()
        if not self._current_shots:
            self.media_count_lbl.setText("")
        elif shown_n == total_n:
            self.media_count_lbl.setText(f"{total_n} item{'s' if total_n != 1 else ''}")
        else:
            self.media_count_lbl.setText(f"{shown_n} / {total_n} items")

        if select_first and shown_n > 0:
            self.media_table.selectRow(0)

    def all_scanned_media(self) -> List[MediaItem]:
        return list(self._all_scanned_media)

    def current_media_list(self) -> List[MediaItem]:
        out: List[MediaItem] = []
        for pr in range(self._proxy.rowCount()):
            src = self._proxy.mapToSource(self._proxy.index(pr, 0))
            m = self._model.item_at(src.row())
            if m is not None:
                out.append(m)
        return out

    def visible_media_items(self) -> List[MediaItem]:
        """Returns media items currently visible in the viewport (for visible-first thumbnail priority)."""
        row_count = self._proxy.rowCount()
        if row_count <= 0:
            return []
        vp = self.media_table.viewport()
        top_row = self.media_table.rowAt(0)
        if top_row < 0:
            top_row = 0
        bot_row = self.media_table.rowAt(max(0, vp.height() - 1))
        if bot_row < 0:
            bot_row = min(row_count - 1, top_row + 25)
        result: List[MediaItem] = []
        for pr in range(top_row, min(row_count, bot_row + 1)):
            src = self._proxy.mapToSource(self._proxy.index(pr, 0))
            m = self._model.item_at(src.row())
            if m is not None:
                result.append(m)
        return result

    def update_media_thumbnail(self, version_dir: str, thumb_path: str) -> None:
        """O(1) in-place Preview icon update without rebuilding the table."""
        self._model.notify_thumbnail_updated(version_dir, thumb_path)

    def selected_media_items(self) -> List[MediaItem]:
        sel_model = self.media_table.selectionModel()
        if sel_model is None:
            return []
        proxy_rows = sorted({idx.row() for idx in sel_model.selectedRows()})
        if not proxy_rows:
            cur = self.media_table.currentIndex()
            if cur.isValid():
                proxy_rows = [cur.row()]
        items: List[MediaItem] = []
        for pr in proxy_rows:
            src = self._proxy.mapToSource(self._proxy.index(pr, 0))
            m = self._model.item_at(src.row())
            if m is not None:
                items.append(m)
        return items

    def selected_media_dicts(self) -> List[MediaItem]:
        """Backwards-compatible alias returning selected MediaItem objects (dict-compatible)."""
        return self.selected_media_items()

    def _get_primary_media_item(self) -> Optional[MediaItem]:
        items = self.selected_media_items()
        return items[0] if items else None

    def play_selected_in_djv(self):
        selected = self.selected_media_items()
        if not selected:
            QMessageBox.information(self, "Select Media", "Please select one or more clips to play in DJV.")
            return

        paths = [m.first_file_path for m in selected if m.is_playable and m.first_file_path]
        if not paths:
            QMessageBox.information(self, "Select Media", "Selected item(s) have no playable media.")
            return

        djv = self._tool_registry.djv if self._tool_registry else None
        if not djv:
            open_path_in_os(paths[0])
            return

        fps = self._project.fps if self._project else 24.0
        ocio_cfg = self._project.effective_ocio_config if self._project else None
        ok, msg = djv.play_media_list(paths, speed=fps, ocio_config=ocio_cfg)
        if not ok:
            QMessageBox.warning(self, "DJV Playback Error", msg)
        else:
            self.status_message.emit(msg)

    def _on_open_selected_media_folder(self):
        m = self._get_primary_media_item()
        if not m:
            return
        folder = m.folder_path or m.version_dir
        if folder and os.path.isdir(folder):
            open_path_in_os(folder)

    def _on_open_exports_folder(self, shot_id: str = ""):
        if not self._project:
            return
        sid = shot_id or (self._current_shot.shot_id if self._current_shot else "")
        if not sid:
            return
        exp_dir = os.path.join(self._project.exports_dir, sid)
        os.makedirs(exp_dir, exist_ok=True)
        open_path_in_os(exp_dir)

    def _on_batch_set_color(self, color_hex: str):
        if not self._project:
            return
        selected = self.selected_media_items()
        if not selected:
            return
        selected_keys: Set[str] = set()
        for m in selected:
            m.color = color_hex
            self._project.set_media_meta(m.media_key, color=color_hex)
            selected_keys.add(m.media_key)

        self._model.notify_keys_updated(selected_keys, cols=[self.COL_IDENTIFIER])
        if (self.media_color_combo.currentData() or "all") != "all":
            self._proxy.invalidateFilter()
            self._apply_media_filter()

        self.media_metadata_changed.emit()

    def _on_edit_media_note(self):
        if not self._project:
            return
        m = self._get_primary_media_item()
        if not m:
            return
        text, ok = QInputDialog.getText(
            self,
            f"Media Note — {m.shot_id} {m.identifier} {m.version}",
            "Note:",
            QLineEdit.Normal,
            m.notes,
        )
        if not ok:
            return
        new_note = text.strip()
        m.notes = new_note
        self._project.set_media_meta(m.media_key, notes=new_note)
        self._model.notify_keys_updated({m.media_key}, cols=[self.COL_FILE])
        self.media_metadata_changed.emit()

    def _emit_qt_export(self, mode: str, media_items: Optional[List[MediaItem]] = None):
        items = media_items if media_items is not None else self.selected_media_items()
        playable = [m for m in items if m.is_playable]
        self.qt_export_from_media_requested.emit(mode, playable)

    def _on_media_table_context_menu(self, pos):
        clicked_idx = self.media_table.indexAt(pos)
        selected_media = self.selected_media_items() if clicked_idx.isValid() else []
        if not selected_media and not self._current_shot:
            return

        menu = QMenu(self)
        menu.setStyleSheet(get_active_stylesheet())

        if selected_media:
            count = len(selected_media)
            label_suffix = (
                f" ({count} selected)"
                if count > 1
                else f" ({selected_media[0].identifier} {selected_media[0].version})"
            )

            playable_sel = [m for m in selected_media if m.is_playable]
            if playable_sel:
                p_count = len(playable_sel)
                p_suffix = (
                    f" ({p_count} selected)"
                    if p_count > 1
                    else f" ({playable_sel[0].identifier} {playable_sel[0].version})"
                )
                has_djv = bool(self._tool_registry and self._tool_registry.is_enabled("djv_player"))
                play_lbl = (
                    f"Play Media in DJV ({p_count} clips)" if p_count > 1 else "Play Media in DJV"
                ) if has_djv else (
                    f"Play Media ({p_count} clips)" if p_count > 1 else "Play Media"
                )
                act_play = menu.addAction(play_lbl)
                act_play.triggered.connect(self.play_selected_in_djv)

                if self._tool_registry and self._tool_registry.is_enabled("slate_delivery"):
                    act_send = menu.addAction(f"Send to Client{p_suffix}...")
                    act_send.triggered.connect(
                        lambda _=False, items=list(playable_sel): self._emit_qt_export("qt_review", items)
                    )
                menu.addSeparator()

            # Color tag submenu (Notion-Dark palette)
            color_menu = menu.addMenu(f"Set Color{label_suffix}")
            color_menu.setStyleSheet(get_active_stylesheet())
            for cname, chex in PROPERTY_COLOR_PALETTE:
                act_col = color_menu.addAction(make_color_dot_icon(chex, 10), cname)
                act_col.triggered.connect(lambda _=False, hx=chex: self._on_batch_set_color(hx))
            color_menu.addSeparator()
            act_clear_col = color_menu.addAction("Clear Color")
            act_clear_col.triggered.connect(lambda: self._on_batch_set_color(""))

            if count == 1:
                act_note = menu.addAction("Edit Note...")
                act_note.triggered.connect(self._on_edit_media_note)

            if playable_sel:
                if len(playable_sel) == 1:
                    act_set_shot = menu.addAction(
                        f"Set as Shot Thumbnail ({playable_sel[0].shot_id})"
                        if len(self._current_shots) > 1
                        else "Set as Shot Thumbnail"
                    )
                    act_set_shot.triggered.connect(
                        lambda _=False, item=playable_sel[0]: self.set_as_shot_thumbnail_requested.emit(item)
                    )
                gen_lbl = (
                    f"Generate / Refresh Thumbnails ({len(playable_sel)})"
                    if len(playable_sel) > 1
                    else "Generate / Refresh Thumbnail"
                )
                act_gen = menu.addAction(gen_lbl)
                act_gen.triggered.connect(
                    lambda _=False, items=list(playable_sel): self.generate_media_thumbnails_requested.emit(items)
                )

            act_dir = menu.addAction("Open Version Folder")
            act_dir.triggered.connect(self._on_open_selected_media_folder)
            menu.addSeparator()

        target_shot_id = (
            selected_media[0].shot_id
            if selected_media and selected_media[0].shot_id
            else (self._current_shot.shot_id if self._current_shot else "")
        )
        if target_shot_id:
            if self._tool_registry and self._tool_registry.is_enabled("media_ingester"):
                act_ing = menu.addAction(f"Ingest Media to Shot ({target_shot_id})...")
                act_ing.triggered.connect(lambda _=False, sid=target_shot_id: self.ingest_requested.emit(sid))

            exp_name = (
                os.path.basename(self._project.exports_dir)
                if (self._project and self._project.exports_dir)
                else "_export"
            )
            act_exp = menu.addAction(f"Open Shot Exports ({target_shot_id} / {exp_name})")
            act_exp.triggered.connect(lambda _=False, sid=target_shot_id: self._on_open_exports_folder(sid))

        menu.exec_(self.media_table.viewport().mapToGlobal(pos))
