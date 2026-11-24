"""Left-hand Shot Explorer panel backed by virtualized QTableView + QAbstractTableModel + PropertyPillDelegate."""

import os
from typing import Dict, List, Optional

from qtpy.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from qtpy.QtGui import QFont
from qtpy.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_STATUSES
from core.io.google_sheets import get_status_counts
from core.models import Project, PropertyType, Shot, ShotProperty
from core.models.project import resolve_shot_thumb_path
from core.storage import ProjectRepository
from tools import ToolRegistry
from ui.theme import get_active_stylesheet
from ui.widgets.property_delegate import (
    PROP_DEF_ROLE,
    PROP_VALUE_ROLE,
    PropertyPillDelegate,
)
from ui.widgets.property_widgets import make_color_dot_icon
from ui.widgets.thumbnail_worker import render_thumbnail_icon


_DELEGATE_PROP_TYPES = frozenset({
    PropertyType.STATUS,
    PropertyType.SELECT,
    PropertyType.MULTI_SELECT,
    PropertyType.PEOPLE,
    PropertyType.DATE,
    PropertyType.CHECKBOX,
})

_BOLD_FONT = QFont("Segoe UI", 10, QFont.Bold)


class ShotTableModel(QAbstractTableModel):
    """
    Virtualized table model for VFX Shots.
    Only visible rows (~15-20) are painted or have thumbnails rendered;
    supports O(1) thumbnail updates and precomputed lowercase search haystacks.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._shots: List[Shot] = []
        self._visible_props: List[ShotProperty] = []
        self._thumb_dir: str = ""
        self._row_by_id: Dict[str, int] = {}
        self._haystacks: List[str] = []
        self._notes_col: int = 3

    def set_data(
        self,
        shots: List[Shot],
        visible_props: List[ShotProperty],
        thumb_dir: str = "",
    ) -> None:
        self.beginResetModel()
        self._shots = list(shots or [])
        self._visible_props = list(visible_props or [])
        self._thumb_dir = thumb_dir or ""
        if self._thumb_dir:
            vfx_root = os.path.dirname(os.path.normpath(self._thumb_dir))
            for s in self._shots:
                if s.thumb:
                    s.thumb = resolve_shot_thumb_path(vfx_root, s.thumb)
        self._notes_col = 3 + len(self._visible_props)
        self._row_by_id = {s.shot_id: idx for idx, s in enumerate(self._shots)}
        self._haystacks = [self._build_haystack(s) for s in self._shots]
        self.endResetModel()

    @staticmethod
    def _build_haystack(s: Shot) -> str:
        custom_vals = " ".join(str(v) for v in s.custom_fields.values()) if s.custom_fields else ""
        return f"{s.shot_id} {s.name} {s.status} {s.assign} {s.notes} {custom_vals}".lower()

    def refresh_haystack(self, row: int) -> None:
        if 0 <= row < len(self._shots):
            self._haystacks[row] = self._build_haystack(self._shots[row])

    def haystack_at(self, row: int) -> str:
        if 0 <= row < len(self._haystacks):
            return self._haystacks[row]
        return ""

    def shot_at(self, row: int) -> Optional[Shot]:
        if 0 <= row < len(self._shots):
            return self._shots[row]
        return None

    def row_for_shot_id(self, shot_id: str) -> int:
        return self._row_by_id.get(shot_id, -1)

    @property
    def notes_col(self) -> int:
        return self._notes_col

    @property
    def visible_props(self) -> List[ShotProperty]:
        return self._visible_props

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._shots)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return 4 + len(self._visible_props)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        if section == 0:
            return "Preview"
        if section == 1:
            return "Shot ID"
        if section == 2:
            return "Frames"
        if section == self._notes_col:
            return "Notes"
        p_idx = section - 3
        if 0 <= p_idx < len(self._visible_props):
            return self._visible_props[p_idx].name
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.NoItemFlags
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        col = index.column()
        if col == self._notes_col:
            return base | Qt.ItemIsEditable
        if 3 <= col < self._notes_col:
            prop = self._visible_props[col - 3]
            if prop.prop_type not in _DELEGATE_PROP_TYPES:
                return base | Qt.ItemIsEditable
        return base

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._shots):
            return None
        s = self._shots[row]
        col = index.column()

        if role == Qt.UserRole:
            return s

        if col == 0:
            if role == Qt.DecorationRole:
                t_path = s.thumb if s.thumb else (
                    os.path.join(self._thumb_dir, f"{s.shot_id}.png") if self._thumb_dir else ""
                )
                return render_thumbnail_icon(t_path, 80, 45)
            return None

        if col == 1:
            if role == Qt.DisplayRole:
                return s.shot_id
            if role == Qt.FontRole:
                return _BOLD_FONT
            return None

        if col == 2:
            if role == Qt.DisplayRole:
                sf = s.start_frame
                ef = s.end_frame
                return f"{sf}–{ef} ({ef - sf + 1}f)"
            return None

        if col == self._notes_col:
            if role in (Qt.DisplayRole, Qt.EditRole):
                return s.notes or ""
            return None

        p_idx = col - 3
        if 0 <= p_idx < len(self._visible_props):
            prop = self._visible_props[p_idx]
            if role == PROP_DEF_ROLE:
                return prop
            val = s.get_field(prop.storage_key)
            if role == PROP_VALUE_ROLE:
                return val
            if role in (Qt.DisplayRole, Qt.EditRole):
                return val
            if role == Qt.ToolTipRole and val:
                return val
        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or role != Qt.EditRole:
            return False
        row = index.row()
        if row < 0 or row >= len(self._shots):
            return False
        s = self._shots[row]
        col = index.column()
        new_val = str(value or "").strip()

        if col == self._notes_col:
            if (s.notes or "") == new_val:
                return False
            s.notes = new_val
            self.refresh_haystack(row)
            self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
            return True

        p_idx = col - 3
        if 0 <= p_idx < len(self._visible_props):
            prop = self._visible_props[p_idx]
            old_val = s.get_field(prop.storage_key)
            if old_val == new_val:
                return False
            s.set_field(prop.storage_key, new_val)
            self.refresh_haystack(row)
            self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole, PROP_VALUE_ROLE, Qt.ToolTipRole])
            return True
        return False

    def notify_thumbnail_updated(self, shot_id: str, thumb_path: str) -> None:
        row = self._row_by_id.get(shot_id, -1)
        if row < 0:
            return
        if thumb_path:
            self._shots[row].thumb = thumb_path
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [Qt.DecorationRole])

    def notify_rows_updated(self, rows: List[int]) -> None:
        if not rows:
            return
        last_col = self.columnCount() - 1
        for r in rows:
            if 0 <= r < len(self._shots):
                self.refresh_haystack(r)
                self.dataChanged.emit(
                    self.index(r, 0),
                    self.index(r, last_col),
                    [Qt.DisplayRole, Qt.EditRole, PROP_VALUE_ROLE],
                )


class ShotFilterProxyModel(QSortFilterProxyModel):
    """Fast in-memory filter proxy for ShotTableModel (sequence, status, search text)."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._query: str = ""
        self._seq_filter: str = "All Seqs"
        self._status_filter: str = "All Statuses"

    def set_filters(self, query: str, seq_filter: str, status_filter: str) -> None:
        q = (query or "").strip().lower()
        sq = seq_filter or "All Seqs"
        st = status_filter or "All Statuses"
        if q == self._query and sq == self._seq_filter and st == self._status_filter:
            return
        self._query = q
        self._seq_filter = sq
        self._status_filter = st
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model: ShotTableModel = self.sourceModel()
        if model is None:
            return True
        rec = model.shot_at(source_row)
        if rec is None:
            return False
        if self._seq_filter != "All Seqs" and rec.seq != self._seq_filter:
            return False
        if self._status_filter != "All Statuses" and rec.status.lower() != self._status_filter.lower():
            return False
        if self._query and self._query not in model.haystack_at(source_row):
            return False
        return True


class ShotExplorerPanel(QWidget):
    """Persistent left-hand Shot Explorer sharing the active project & shot selection."""

    shot_selected = Signal(object)
    shots_selection_changed = Signal(list)
    shot_data_changed = Signal()
    kpi_updated = Signal(str)
    kpi_data_updated = Signal(object, list, str)
    status_filter_changed = Signal(str)
    create_shot_requested = Signal()
    edit_shot_properties_requested = Signal()
    manage_properties_requested = Signal()
    open_in_dcc_launcher_requested = Signal()
    remove_shots_requested = Signal()
    ingest_requested = Signal()
    ingest_new_shot_requested = Signal()
    open_shot_folder_requested = Signal()
    save_requested = Signal()
    qt_export_from_shots_requested = Signal(str)
    play_latest_in_djv_requested = Signal()
    generate_thumbnails_requested = Signal()
    set_custom_thumbnail_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._shots: List[Shot] = []
        self._project: Optional[Project] = None
        self._repo: Optional[ProjectRepository] = None
        self._tool_registry: Optional[ToolRegistry] = None
        self._visible_props: List[ShotProperty] = []
        self._updating_ui = False

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(100)
        self._search_timer.timeout.connect(self._apply_shot_filter)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(6)

        # Search & Filter row
        filter_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter shots...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(lambda _: self._search_timer.start())
        filter_row.addWidget(self.search_edit, 2)

        self.seq_filter_combo = QComboBox()
        self.seq_filter_combo.addItem("All Seqs")
        self.seq_filter_combo.currentTextChanged.connect(self._apply_shot_filter)
        filter_row.addWidget(self.seq_filter_combo, 1)

        self.status_filter_combo = QComboBox()
        self.status_filter_combo.addItem("All Statuses")
        for st in DEFAULT_STATUSES:
            self.status_filter_combo.addItem(st)
        self.status_filter_combo.currentTextChanged.connect(self._on_status_filter_combo_changed)
        filter_row.addWidget(self.status_filter_combo, 1)
        layout.addLayout(filter_row)

        # Virtualized Shot Table (QTableView + Model + Proxy + Pill Delegate)
        self._model = ShotTableModel(self)
        self._model.dataChanged.connect(self._on_model_data_changed)
        self._proxy = ShotFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)

        self.shot_table = QTableView(self)
        self.shot_table.setModel(self._proxy)
        self.shot_table.setMouseTracking(True)
        self.shot_table.verticalHeader().setVisible(False)
        self.shot_table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.shot_table.verticalHeader().setDefaultSectionSize(52)
        self.shot_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.shot_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.shot_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.shot_table.customContextMenuRequested.connect(self._on_shot_table_context_menu)
        self.shot_table.setAlternatingRowColors(True)
        self.shot_table.setIconSize(QSize(80, 45))
        self.shot_table.setWordWrap(False)

        self._pill_delegate = PropertyPillDelegate(
            project_getter=lambda: self._project,
            on_commit=self._on_delegate_property_commit,
            parent=self.shot_table,
        )
        self.shot_table.setItemDelegate(self._pill_delegate)

        self.shot_table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.shot_table.doubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.shot_table, 1)

    def set_context(
        self,
        project: Optional[Project],
        repo: Optional[ProjectRepository],
        tool_registry: ToolRegistry,
        shots: List[Shot],
        preserve_shot_id: str = "",
    ):
        self._project = project
        self._repo = repo
        self._tool_registry = tool_registry
        self._shots = shots

        # Refresh Sequence Filter
        cur_seq = self.seq_filter_combo.currentText()
        self.seq_filter_combo.blockSignals(True)
        self.seq_filter_combo.clear()
        self.seq_filter_combo.addItem("All Seqs")
        seqs = sorted({s.seq for s in shots if s.seq})
        for sq in seqs:
            self.seq_filter_combo.addItem(sq)
        if cur_seq in seqs:
            self.seq_filter_combo.setCurrentText(cur_seq)
        self.seq_filter_combo.blockSignals(False)

        # Refresh Status Filter from project schema (with colored dot icons)
        cur_st = self.status_filter_combo.currentText()
        self.status_filter_combo.blockSignals(True)
        self.status_filter_combo.clear()
        self.status_filter_combo.addItem("All Statuses")
        status_opts = (
            self._project.get_status_property().options
            if self._project
            else []
        )
        if status_opts:
            for opt in status_opts:
                self.status_filter_combo.addItem(make_color_dot_icon(opt.color, 9), opt.name)
            opt_names = [o.name for o in status_opts]
        else:
            for st in DEFAULT_STATUSES:
                self.status_filter_combo.addItem(st)
            opt_names = list(DEFAULT_STATUSES)
        if cur_st in opt_names:
            self.status_filter_combo.setCurrentText(cur_st)
        self.status_filter_combo.blockSignals(False)

        self._populate_shot_table(preserve_shot_id=preserve_shot_id)
        self.update_kpi_banner()

    def update_module_visibility(self, tool_registry: ToolRegistry):
        self._tool_registry = tool_registry

    def update_shot_thumbnail(self, shot_id: str, thumb_path: str) -> None:
        """O(1) in-place Preview icon update without rebuilding the table."""
        if not shot_id:
            return
        self._updating_ui = True
        try:
            self._model.notify_thumbnail_updated(shot_id, thumb_path)
        finally:
            self._updating_ui = False

    def current_shot(self) -> Optional[Shot]:
        idx = self.shot_table.currentIndex()
        if not idx.isValid():
            return None
        src_idx = self._proxy.mapToSource(idx)
        return self._model.shot_at(src_idx.row())

    def selected_shots(self) -> List[Shot]:
        sel_model = self.shot_table.selectionModel()
        if sel_model is None:
            return []
        proxy_rows = sorted({idx.row() for idx in sel_model.selectedRows()})
        if not proxy_rows:
            cur = self.shot_table.currentIndex()
            if cur.isValid():
                proxy_rows = [cur.row()]
        records: List[Shot] = []
        for pr in proxy_rows:
            src_idx = self._proxy.mapToSource(self._proxy.index(pr, 0))
            rec = self._model.shot_at(src_idx.row())
            if rec is not None:
                records.append(rec)
        return records

    def visible_shots(self) -> List[Shot]:
        """Returns shots currently visible in the viewport (for visible-first thumbnail priority)."""
        row_count = self._proxy.rowCount()
        if row_count <= 0:
            return []
        vp = self.shot_table.viewport()
        top_row = self.shot_table.rowAt(0)
        if top_row < 0:
            top_row = 0
        bot_row = self.shot_table.rowAt(max(0, vp.height() - 1))
        if bot_row < 0:
            bot_row = min(row_count - 1, top_row + 25)
        result: List[Shot] = []
        for pr in range(top_row, min(row_count, bot_row + 1)):
            src_idx = self._proxy.mapToSource(self._proxy.index(pr, 0))
            rec = self._model.shot_at(src_idx.row())
            if rec is not None:
                result.append(rec)
        return result

    def _configure_columns(self) -> None:
        sh_hdr = self.shot_table.horizontalHeader()
        sh_hdr.setStretchLastSection(False)
        sh_hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        self.shot_table.setColumnWidth(0, 92)
        sh_hdr.setSectionResizeMode(1, QHeaderView.Interactive)
        self.shot_table.setColumnWidth(1, 108)
        sh_hdr.setSectionResizeMode(2, QHeaderView.Interactive)
        self.shot_table.setColumnWidth(2, 118)

        for idx, prop in enumerate(self._visible_props):
            c_idx = 3 + idx
            if prop.prop_type == PropertyType.CHECKBOX:
                sh_hdr.setSectionResizeMode(c_idx, QHeaderView.Interactive)
                self.shot_table.setColumnWidth(c_idx, 68)
            elif prop.prop_type in (PropertyType.STATUS, PropertyType.SELECT, PropertyType.DATE):
                sh_hdr.setSectionResizeMode(c_idx, QHeaderView.Interactive)
                self.shot_table.setColumnWidth(c_idx, 122)
            elif prop.prop_type in (PropertyType.PEOPLE, PropertyType.MULTI_SELECT):
                sh_hdr.setSectionResizeMode(c_idx, QHeaderView.Interactive)
                self.shot_table.setColumnWidth(
                    c_idx,
                    146 if (prop.allow_multiple or prop.prop_type == PropertyType.MULTI_SELECT) else 122,
                )
            else:
                sh_hdr.setSectionResizeMode(c_idx, QHeaderView.Interactive)
                self.shot_table.setColumnWidth(c_idx, 110)

        notes_col = self._model.notes_col
        sh_hdr.setSectionResizeMode(notes_col, QHeaderView.Stretch)

    def _populate_shot_table(self, preserve_shot_id: str = ""):
        prev_sid = preserve_shot_id
        if not prev_sid:
            cur = self.current_shot()
            prev_sid = cur.shot_id if cur else ""

        self._updating_ui = True
        if self._project:
            self._visible_props = [p for p in self._project.shot_properties if p.show_in_table]
        else:
            self._visible_props = []

        t_dir = self._project.thumbnails_dir if self._project else ""
        self._model.set_data(self._shots, self._visible_props, thumb_dir=t_dir)
        self._configure_columns()
        self._apply_shot_filter()
        self._updating_ui = False

        # Restore selection
        target_proxy_row = -1
        if prev_sid:
            src_row = self._model.row_for_shot_id(prev_sid)
            if src_row >= 0:
                p_idx = self._proxy.mapFromSource(self._model.index(src_row, 0))
                if p_idx.isValid():
                    target_proxy_row = p_idx.row()
        if target_proxy_row < 0 and self._proxy.rowCount() > 0:
            target_proxy_row = 0

        if target_proxy_row >= 0:
            self.shot_table.selectRow(target_proxy_row)
            self._on_selection_changed()
        else:
            self.shot_selected.emit(None)
            self.shots_selection_changed.emit([])

    def _on_status_filter_combo_changed(self, text: str) -> None:
        self._apply_shot_filter()
        norm = "" if (not text or text == "All Statuses") else text
        self.status_filter_changed.emit(norm)

    def set_status_filter(self, status_name: str) -> None:
        """Programmatically set the status filter (e.g. from StatusKPIBar click)."""
        target = (status_name or "").strip()
        self.status_filter_combo.blockSignals(True)
        if not target or target.lower() == "all statuses":
            self.status_filter_combo.setCurrentIndex(0)
        else:
            idx = self.status_filter_combo.findText(target, Qt.MatchFixedString)
            self.status_filter_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.status_filter_combo.blockSignals(False)
        self._apply_shot_filter()

    def _apply_shot_filter(self):
        self._proxy.set_filters(
            self.search_edit.text(),
            self.seq_filter_combo.currentText(),
            self.status_filter_combo.currentText(),
        )

    def _on_delegate_property_commit(self, proxy_index: QModelIndex, prop: ShotProperty, new_val: str) -> None:
        if self._updating_ui or not proxy_index.isValid():
            return
        src_idx = self._proxy.mapToSource(proxy_index)
        if not src_idx.isValid():
            return
        changed = self._model.setData(src_idx, new_val, Qt.EditRole)
        if changed:
            if prop.key == "status":
                self.update_kpi_banner()
            self.shot_data_changed.emit()

    def _on_model_data_changed(self, top_left: QModelIndex, bottom_right: QModelIndex, roles=None) -> None:
        if self._updating_ui:
            return
        # Inline text / Notes edits committed directly through Qt's default text editor
        if roles and Qt.DecorationRole in roles and Qt.EditRole not in roles:
            return
        if top_left.column() == self._model.notes_col or (
            3 <= top_left.column() < self._model.notes_col
            and self._visible_props[top_left.column() - 3].prop_type not in _DELEGATE_PROP_TYPES
        ):
            self.shot_data_changed.emit()

    def get_kpi_text(self) -> str:
        c = get_status_counts(self._shots)
        return (
            f"Total: {c['total']}   |   "
            f"✅ Approved: {c['approved']}   |   "
            f"🔨 WIP: {c['wip']}   |   "
            f"👀 Review: {c['review']}   |   "
            f"⏳ Pending: {c['pending']}"
        )

    def update_kpi_banner(self):
        self.kpi_updated.emit(self.get_kpi_text())
        cur_st = self.status_filter_combo.currentText()
        active_st = "" if (not cur_st or cur_st == "All Statuses") else cur_st
        self.kpi_data_updated.emit(self._project, list(self._shots), active_st)

    def _on_batch_set_status(self, new_status: str):
        selected = self.selected_shots()
        if not selected:
            return
        self._updating_ui = True
        updated_rows: List[int] = []
        for s in selected:
            s.status = new_status
            r = self._model.row_for_shot_id(s.shot_id)
            if r >= 0:
                updated_rows.append(r)
        self._model.notify_rows_updated(updated_rows)
        self._updating_ui = False
        self._proxy.invalidateFilter()
        self.update_kpi_banner()
        self.shot_data_changed.emit()

    def _on_batch_set_artist(self, artist_name: str):
        selected = self.selected_shots()
        if not selected:
            return
        self._updating_ui = True
        updated_rows: List[int] = []
        for s in selected:
            s.assign = artist_name
            r = self._model.row_for_shot_id(s.shot_id)
            if r >= 0:
                updated_rows.append(r)
        self._model.notify_rows_updated(updated_rows)
        self._updating_ui = False
        self._proxy.invalidateFilter()
        self.shot_data_changed.emit()

    def _on_selection_changed(self, *_args):
        if self._updating_ui:
            return
        self.shot_selected.emit(self.current_shot())
        self.shots_selection_changed.emit(self.selected_shots())

    def _on_item_double_clicked(self, index: QModelIndex):
        if index.isValid() and index.column() in (0, 1, 2):
            self.edit_shot_properties_requested.emit()

    def _on_shot_table_context_menu(self, pos):
        clicked_idx = self.shot_table.indexAt(pos)
        selected_shots = self.selected_shots() if clicked_idx.isValid() else []

        menu = QMenu(self)
        menu.setStyleSheet(get_active_stylesheet())

        if selected_shots:
            count = len(selected_shots)
            label_suffix = f" ({count} shots)" if count > 1 else f" ({selected_shots[0].shot_id})"

            act_props = menu.addAction("Shot Properties...")
            act_props.triggered.connect(self.edit_shot_properties_requested.emit)

            # Colored Status submenu from project schema
            status_menu = menu.addMenu(f"Set Status{label_suffix}")
            status_menu.setStyleSheet(get_active_stylesheet())
            status_opts = self._project.get_status_property().options if self._project else []
            if status_opts:
                for opt in status_opts:
                    act_st = status_menu.addAction(make_color_dot_icon(opt.color, 10), opt.name)
                    act_st.triggered.connect(lambda _=False, val=opt.name: self._on_batch_set_status(val))
            else:
                for st in DEFAULT_STATUSES:
                    act_st = status_menu.addAction(st)
                    act_st.triggered.connect(lambda _=False, val=st: self._on_batch_set_status(val))

            # Colored Artist submenu from project artists roster
            if self._project:
                artist_menu = menu.addMenu(f"Set Artist{label_suffix}")
                artist_menu.setStyleSheet(get_active_stylesheet())
                active_artists = self._project.list_artists(active_only=True)
                for art in active_artists:
                    act_a = artist_menu.addAction(
                        make_color_dot_icon(self._project.get_artist_color(art.name), 10),
                        art.name,
                    )
                    act_a.triggered.connect(lambda _=False, val=art.name: self._on_batch_set_artist(val))
                if active_artists:
                    artist_menu.addSeparator()
                act_un = artist_menu.addAction("Unassigned")
                act_un.triggered.connect(lambda: self._on_batch_set_artist(""))

            act_dcc = menu.addAction("Open in DCC Launcher")
            act_dcc.triggered.connect(self.open_in_dcc_launcher_requested.emit)

            gen_lbl = f"Generate Thumbnails ({count} shots)" if count > 1 else "Generate Thumbnail"
            act_gen_thumb = menu.addAction(gen_lbl)
            act_gen_thumb.triggered.connect(self.generate_thumbnails_requested.emit)
            if count == 1:
                act_custom_thumb = menu.addAction("Set Custom Thumbnail...")
                act_custom_thumb.triggered.connect(self.set_custom_thumbnail_requested.emit)
            if self._tool_registry and self._tool_registry.is_enabled("media_ingester"):
                menu.addSeparator()
                act_ing = menu.addAction("Ingest Media to Shot...")
                act_ing.triggered.connect(self.ingest_requested.emit)

            menu.addSeparator()
            act_dir = menu.addAction("Open Shot Folder")
            act_dir.triggered.connect(self.open_shot_folder_requested.emit)

            rm_lbl = f"Remove Shots ({count})..." if count > 1 else f"Remove Shot ({selected_shots[0].shot_id})..."
            act_rm = menu.addAction(rm_lbl)
            act_rm.triggered.connect(self.remove_shots_requested.emit)
            menu.addSeparator()

        act_new = menu.addAction("New Seq / Shot...")
        act_new.triggered.connect(self.create_shot_requested.emit)

        if self._tool_registry and self._tool_registry.is_enabled("media_ingester"):
            act_ing_new = menu.addAction("Ingest Media as New Shot...")
            act_ing_new.triggered.connect(self.ingest_new_shot_requested.emit)

        menu.addSeparator()
        act_manage = menu.addAction("Manage Properties...")
        act_manage.triggered.connect(self.manage_properties_requested.emit)

        menu.exec_(self.shot_table.viewport().mapToGlobal(pos))
