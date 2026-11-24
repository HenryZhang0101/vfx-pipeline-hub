"""Lightweight QStyledItemDelegate rendering Notion-Dark property pills without per-cell QWidgets."""

from typing import Callable, Dict, List, Optional, Tuple

from qtpy.QtCore import QDate, QEvent, QPoint, QRect, QRectF, Qt
from qtpy.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from qtpy.QtWidgets import (
    QCalendarWidget,
    QHBoxLayout,
    QInputDialog,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from core.models import DEFAULT_OPTION_COLOR, Project, PropertyType, ShotProperty
from ui.theme import get_active_stylesheet
from ui.widgets.property_widgets import (
    build_options_for_property,
    make_color_dot_icon,
    normalize_qcolor,
)

# Custom roles provided by ShotTableModel for delegate rendering/interaction
PROP_DEF_ROLE = Qt.UserRole + 20      # Optional[ShotProperty]
PROP_VALUE_ROLE = Qt.UserRole + 21    # raw string value

_DELEGATE_PROP_TYPES = frozenset({
    PropertyType.STATUS,
    PropertyType.SELECT,
    PropertyType.MULTI_SELECT,
    PropertyType.PEOPLE,
    PropertyType.DATE,
    PropertyType.CHECKBOX,
})


class PropertyPillDelegate(QStyledItemDelegate):
    """
    Zero-widget QStyledItemDelegate for Shot Explorer typed property columns.
    Paints Notion-Dark colored pills, multi-tag chips, date chips, and checkboxes
    directly with QPainter, and opens popup menus / calendars on left-click.
    """

    def __init__(
        self,
        project_getter: Callable[[], Optional[Project]],
        on_commit: Callable[[object, ShotProperty, str], None],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._project_getter = project_getter
        self._on_commit = on_commit

    def _color_lookup(self, prop: ShotProperty, project: Optional[Project]) -> Dict[str, str]:
        opts = build_options_for_property(prop, project)
        return {name.lower(): col for name, col in opts}

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        prop: Optional[ShotProperty] = index.data(PROP_DEF_ROLE)
        if prop is None or prop.prop_type not in _DELEGATE_PROP_TYPES:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Let Qt draw row selection / hover background without text
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.icon = opt.icon.__class__()
        style = opt.widget.style() if opt.widget else None
        if style:
            style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        val_str = str(index.data(PROP_VALUE_ROLE) or "").strip()
        project = self._project_getter()
        rect = option.rect.adjusted(6, 10, -6, -10)
        hovered = bool(option.state & QStyle.State_MouseOver)

        ptype = prop.prop_type
        if ptype == PropertyType.CHECKBOX:
            self._paint_checkbox(painter, option.rect, ShotProperty.parse_bool(val_str), hovered)
        elif ptype == PropertyType.DATE:
            self._paint_date_pill(painter, rect, val_str, hovered)
        elif ptype == PropertyType.MULTI_SELECT or (ptype == PropertyType.PEOPLE and prop.allow_multiple):
            self._paint_multi_pills(painter, rect, prop, project, val_str, hovered)
        else:
            # STATUS, SELECT, single PEOPLE
            display_val = val_str or (prop.default_value or "Pending" if ptype == PropertyType.STATUS else "")
            self._paint_single_pill(painter, rect, prop, project, display_val, hovered)

        painter.restore()

    def _paint_single_pill(
        self,
        painter: QPainter,
        rect: QRect,
        prop: ShotProperty,
        project: Optional[Project],
        val_str: str,
        hovered: bool,
    ) -> None:
        pill_h = min(26, max(20, rect.height()))
        y = rect.y() + (rect.height() - pill_h) // 2
        pill_rect = QRectF(rect.x() + 0.5, y + 0.5, max(10, rect.width() - 1), pill_h - 1)

        path = QPainterPath()
        path.addRoundedRect(pill_rect, 5.0, 5.0)

        if not val_str:
            bg = QColor("#2C2C2C" if hovered else "#202020")
            border = QColor("#3E3E3E" if hovered else "#2F2F2F")
            fg = QColor("#E3E2E0" if hovered else "#7C7B78")
            font = QFont("Segoe UI", 9, QFont.Medium)
            label = "—"
        else:
            cmap = self._color_lookup(prop, project)
            c = normalize_qcolor(cmap.get(val_str.lower(), DEFAULT_OPTION_COLOR))
            bg = QColor(c.red(), c.green(), c.blue(), 92 if hovered else 61)
            border = c.lighter(125) if hovered else QColor(c.red(), c.green(), c.blue(), 140)
            fg = QColor("#F1F0EE")
            font = QFont("Segoe UI", 9, QFont.DemiBold)
            label = val_str

        painter.fillPath(path, bg)
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(path)

        painter.setFont(font)
        painter.setPen(fg)
        fm = QFontMetrics(font)
        arrow_w = 14
        text_rect = pill_rect.adjusted(8, 0, -(arrow_w + 4), 0)
        elided = fm.elidedText(label, Qt.ElideRight, max(10, int(text_rect.width())))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided)

        arrow_rect = QRectF(pill_rect.right() - arrow_w - 4, pill_rect.y(), arrow_w, pill_rect.height())
        painter.setPen(QColor("#9B9A97"))
        painter.drawText(arrow_rect, Qt.AlignCenter, "▾")

    def _paint_multi_pills(
        self,
        painter: QPainter,
        rect: QRect,
        prop: ShotProperty,
        project: Optional[Project],
        val_str: str,
        hovered: bool,
    ) -> None:
        tags = ShotProperty.parse_multi_value(val_str)
        if not tags:
            self._paint_single_pill(painter, rect, prop, project, "", hovered)
            return

        pill_h = min(26, max(20, rect.height()))
        y = rect.y() + (rect.height() - pill_h) // 2
        outer_rect = QRectF(rect.x() + 0.5, y + 0.5, max(10, rect.width() - 1), pill_h - 1)
        outer_path = QPainterPath()
        outer_path.addRoundedRect(outer_rect, 5.0, 5.0)
        painter.fillPath(outer_path, QColor("#2C2C2C" if hovered else "#202020"))
        painter.setPen(QPen(QColor("#3E3E3E" if hovered else "#2F2F2F"), 1.0))
        painter.drawPath(outer_path)

        cmap = self._color_lookup(prop, project)
        font = QFont("Segoe UI", 8, QFont.DemiBold)
        painter.setFont(font)
        fm = QFontMetrics(font)

        shown = tags[:2]
        overflow = len(tags) - len(shown)
        x = outer_rect.x() + 5.0
        max_x = outer_rect.right() - 18.0
        tag_h = pill_h - 6.0
        tag_y = outer_rect.y() + 2.5

        for tag in shown:
            avail = max_x - x - (24.0 if overflow > 0 else 0.0)
            if avail < 18.0:
                overflow += 1
                continue
            txt = fm.elidedText(tag, Qt.ElideRight, int(avail - 10))
            tw = min(avail, fm.horizontalAdvance(txt) + 12.0)
            c = normalize_qcolor(cmap.get(tag.lower(), DEFAULT_OPTION_COLOR))
            t_rect = QRectF(x, tag_y, tw, tag_h)
            t_path = QPainterPath()
            t_path.addRoundedRect(t_rect, 4.0, 4.0)
            painter.fillPath(t_path, QColor(c.red(), c.green(), c.blue(), 72))
            painter.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 140), 1.0))
            painter.drawPath(t_path)
            painter.setPen(QColor("#F1F0EE"))
            painter.drawText(t_rect, Qt.AlignCenter, txt)
            x += tw + 4.0

        if overflow > 0 and x < max_x:
            badge_txt = f"+{overflow}"
            bw = min(max_x - x, fm.horizontalAdvance(badge_txt) + 10.0)
            b_rect = QRectF(x, tag_y, bw, tag_h)
            b_path = QPainterPath()
            b_path.addRoundedRect(b_rect, 4.0, 4.0)
            painter.fillPath(b_path, QColor("#2C2C2C"))
            painter.setPen(QPen(QColor("#3E3E3E"), 1.0))
            painter.drawPath(b_path)
            painter.setPen(QColor("#9B9A97"))
            painter.drawText(b_rect, Qt.AlignCenter, badge_txt)

        arrow_rect = QRectF(outer_rect.right() - 16.0, outer_rect.y(), 14.0, outer_rect.height())
        painter.setPen(QColor("#9B9A97"))
        painter.drawText(arrow_rect, Qt.AlignCenter, "▾")

    def _paint_date_pill(self, painter: QPainter, rect: QRect, val_str: str, hovered: bool) -> None:
        pill_h = min(26, max(20, rect.height()))
        y = rect.y() + (rect.height() - pill_h) // 2
        pill_rect = QRectF(rect.x() + 0.5, y + 0.5, max(10, rect.width() - 1), pill_h - 1)
        path = QPainterPath()
        path.addRoundedRect(pill_rect, 5.0, 5.0)
        painter.fillPath(path, QColor("#2C2C2C" if hovered else "#202020"))
        painter.setPen(QPen(QColor("#3E3E3E" if hovered else "#2F2F2F"), 1.0))
        painter.drawPath(path)

        font = QFont("Segoe UI", 9, QFont.Medium if val_str else QFont.Normal)
        painter.setFont(font)
        painter.setPen(QColor("#E3E2E0" if val_str else ("#E3E2E0" if hovered else "#7C7B78")))
        painter.drawText(pill_rect.adjusted(8, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft, val_str or "—")

    def _paint_checkbox(self, painter: QPainter, cell_rect: QRect, checked: bool, hovered: bool) -> None:
        box_size = 16
        cx = cell_rect.x() + (cell_rect.width() - box_size) // 2
        cy = cell_rect.y() + (cell_rect.height() - box_size) // 2
        box = QRectF(cx + 0.5, cy + 0.5, box_size - 1, box_size - 1)
        path = QPainterPath()
        path.addRoundedRect(box, 3.5, 3.5)

        if checked:
            painter.fillPath(path, QColor("#2383E2"))
            painter.setPen(QPen(QColor("#3B96F0"), 1.0))
            painter.drawPath(path)
            # Crisp white checkmark
            pen = QPen(QColor("#FFFFFF"), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(QPoint(cx + 4, cy + 8), QPoint(cx + 7, cy + 11))
            painter.drawLine(QPoint(cx + 7, cy + 11), QPoint(cx + 12, cy + 5))
        else:
            painter.fillPath(path, QColor("#2C2C2C" if hovered else "#202020"))
            painter.setPen(QPen(QColor("#555555" if hovered else "#3A3A3A"), 1.0))
            painter.drawPath(path)

    def editorEvent(self, event, model, option: QStyleOptionViewItem, index) -> bool:
        prop: Optional[ShotProperty] = index.data(PROP_DEF_ROLE)
        if prop is None or prop.prop_type not in _DELEGATE_PROP_TYPES:
            return super().editorEvent(event, model, option, index)

        if event.type() != QEvent.MouseButtonRelease or event.button() != Qt.LeftButton:
            return False

        # Ignore clicks with multi-selection modifiers (Ctrl/Shift) so row multi-selection works naturally
        if event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
            return False

        val_str = str(index.data(PROP_VALUE_ROLE) or "").strip()
        project = self._project_getter()
        ptype = prop.prop_type
        parent_widget = option.widget if isinstance(option.widget, QWidget) else None

        if ptype == PropertyType.CHECKBOX:
            new_val = ShotProperty.format_bool(not ShotProperty.parse_bool(val_str))
            self._on_commit(index, prop, new_val)
            return True

        global_pos = (
            parent_widget.viewport().mapToGlobal(option.rect.bottomLeft())
            if (parent_widget is not None and hasattr(parent_widget, "viewport"))
            else event.globalPos()
        )

        if ptype == PropertyType.DATE:
            self._open_date_popup(parent_widget, global_pos, index, prop, val_str)
            return True

        if ptype == PropertyType.MULTI_SELECT or (ptype == PropertyType.PEOPLE and prop.allow_multiple):
            self._open_multi_menu(parent_widget, global_pos, index, prop, project, val_str)
            return True

        self._open_single_menu(parent_widget, global_pos, index, prop, project, val_str)
        return True

    def _open_single_menu(
        self,
        parent: Optional[QWidget],
        global_pos: QPoint,
        index,
        prop: ShotProperty,
        project: Optional[Project],
        val_str: str,
    ) -> None:
        menu = QMenu(parent)
        menu.setStyleSheet(get_active_stylesheet())
        options = build_options_for_property(prop, project)
        clearable = prop.prop_type != PropertyType.STATUS
        allow_custom = prop.prop_type == PropertyType.PEOPLE

        for name, col in options:
            act = menu.addAction(make_color_dot_icon(col, 10), name)
            act.setCheckable(True)
            act.setChecked(name.lower() == val_str.lower())
            act.triggered.connect(lambda _=False, v=name: self._on_commit(index, prop, v))

        if allow_custom:
            if options:
                menu.addSeparator()
            act_other = menu.addAction("Type custom name...")

            def _prompt():
                txt, ok = QInputDialog.getText(parent, "Custom Value", "Enter name:", text=val_str)
                if ok:
                    self._on_commit(index, prop, txt.strip())

            act_other.triggered.connect(_prompt)

        if clearable and val_str:
            menu.addSeparator()
            act_clear = menu.addAction("Clear")
            act_clear.triggered.connect(lambda: self._on_commit(index, prop, ""))

        menu.exec_(global_pos)

    def _open_multi_menu(
        self,
        parent: Optional[QWidget],
        global_pos: QPoint,
        index,
        prop: ShotProperty,
        project: Optional[Project],
        val_str: str,
    ) -> None:
        menu = QMenu(parent)
        menu.setStyleSheet(get_active_stylesheet())
        selected = ShotProperty.parse_multi_value(val_str)
        selected_low = {s.lower() for s in selected}

        all_opts: List[Tuple[str, str]] = list(build_options_for_property(prop, project))
        known_low = {n.lower() for n, _ in all_opts}
        for s in selected:
            if s.lower() not in known_low:
                all_opts.append((s, DEFAULT_OPTION_COLOR))

        if not all_opts:
            empty_act = menu.addAction("(No options configured)")
            empty_act.setEnabled(False)
        else:
            for name, col in all_opts:
                act = menu.addAction(make_color_dot_icon(col, 10), name)
                act.setCheckable(True)
                act.setChecked(name.lower() in selected_low)

                def _toggle(_=False, n=name):
                    cur = ShotProperty.parse_multi_value(str(index.data(PROP_VALUE_ROLE) or ""))
                    low = n.lower()
                    nxt = [x for x in cur if x.lower() != low]
                    if len(nxt) == len(cur):
                        nxt.append(n)
                    self._on_commit(index, prop, ShotProperty.format_multi_value(nxt))

                act.triggered.connect(_toggle)

        if selected:
            menu.addSeparator()
            act_clear = menu.addAction("Clear All")
            act_clear.triggered.connect(lambda: self._on_commit(index, prop, ""))

        menu.exec_(global_pos)

    def _open_date_popup(
        self,
        parent: Optional[QWidget],
        global_pos: QPoint,
        index,
        prop: ShotProperty,
        val_str: str,
    ) -> None:
        menu = QMenu(parent)
        menu.setStyleSheet(get_active_stylesheet())

        container = QWidget(menu)
        lay = QVBoxLayout(container)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        cal = QCalendarWidget(container)
        cal.setGridVisible(False)
        cal.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        parsed = QDate.fromString(val_str, "yyyy-MM-dd")
        cal.setSelectedDate(parsed if parsed.isValid() else QDate.currentDate())

        def _pick(qdate: QDate) -> None:
            self._on_commit(index, prop, qdate.toString("yyyy-MM-dd"))
            menu.close()

        cal.clicked.connect(_pick)
        lay.addWidget(cal)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        today_btn = QPushButton("Today")
        today_btn.clicked.connect(lambda: _pick(QDate.currentDate()))
        btn_row.addWidget(today_btn)
        btn_row.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: (self._on_commit(index, prop, ""), menu.close()))
        btn_row.addWidget(clear_btn)
        lay.addLayout(btn_row)

        act = QWidgetAction(menu)
        act.setDefaultWidget(container)
        menu.addAction(act)
        menu.exec_(global_pos)
