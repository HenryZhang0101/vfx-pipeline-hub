"""Reusable Notion-Dark typed property widgets shared across Shot Explorer, Shot Properties, and Project Settings."""

from typing import Callable, List, Optional, Tuple

from qtpy.QtCore import QDate, Qt, Signal
from qtpy.QtGui import QColor, QIcon, QPainter, QPixmap
from qtpy.QtWidgets import (
    QCalendarWidget,
    QCheckBox,
    QColorDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from core.models import (
    DEFAULT_OPTION_COLOR,
    PROPERTY_COLOR_PALETTE,
    Project,
    PropertyType,
    ShotProperty,
)
from ui.theme import get_active_stylesheet


def normalize_qcolor(color_hex: str, fallback: str = DEFAULT_OPTION_COLOR) -> QColor:
    c = QColor(color_hex or "")
    return c if c.isValid() else QColor(fallback)


_COLOR_DOT_CACHE: dict = {}


def make_color_dot_icon(color_hex: str, size: int = 10) -> QIcon:
    """Create (and cache) a crisp anti-aliased circular color dot icon for menus and dropdowns."""
    c = normalize_qcolor(color_hex)
    cache_key = (c.name().lower(), int(size))
    cached = _COLOR_DOT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    pix = QPixmap(size + 2, size + 2)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setBrush(c)
    p.setPen(c.lighter(125))
    p.drawEllipse(1, 1, size, size)
    p.end()
    icon = QIcon(pix)
    if len(_COLOR_DOT_CACHE) < 256:
        _COLOR_DOT_CACHE[cache_key] = icon
    return icon


def pill_stylesheet(color_hex: str, compact: bool = False, muted: bool = False) -> str:
    """Build a Notion-Dark tinted pill button QSS for a given hex color."""
    pad = "2px 8px" if compact else "4px 10px"
    if muted:
        return (
            f"QPushButton {{ background-color: #202020; color: #7C7B78; "
            f"border: 1px solid #2F2F2F; border-radius: 5px; padding: {pad}; "
            f"text-align: left; font-weight: 500; }}"
            f"QPushButton:hover {{ background-color: #2C2C2C; color: #E3E2E0; border-color: #3E3E3E; }}"
        )
    c = normalize_qcolor(color_hex)
    bg = f"rgba({c.red()}, {c.green()}, {c.blue()}, 0.24)"
    bg_hover = f"rgba({c.red()}, {c.green()}, {c.blue()}, 0.36)"
    border = f"rgba({c.red()}, {c.green()}, {c.blue()}, 0.55)"
    return (
        f"QPushButton {{ background-color: {bg}; color: #F1F0EE; "
        f"border: 1px solid {border}; border-radius: 5px; padding: {pad}; "
        f"text-align: left; font-weight: 600; }}"
        f"QPushButton:hover {{ background-color: {bg_hover}; border-color: {c.lighter(125).name()}; }}"
    )


class ColorSwatchButton(QPushButton):
    """Clickable Notion color swatch opening the palette menu + custom QColorDialog."""

    color_changed = Signal(str)

    def __init__(self, color_hex: str = DEFAULT_OPTION_COLOR, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._color = normalize_qcolor(color_hex).name()
        self.setFixedSize(24, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Change color")
        self._apply_style()
        self.clicked.connect(self._open_menu)

    def color(self) -> str:
        return self._color

    def set_color(self, color_hex: str, emit: bool = False) -> None:
        new_c = normalize_qcolor(color_hex).name()
        if new_c != self._color:
            self._color = new_c
            self._apply_style()
            if emit:
                self.color_changed.emit(self._color)

    def _apply_style(self) -> None:
        c = normalize_qcolor(self._color)
        self.setStyleSheet(
            f"QPushButton {{ background-color: {c.name()}; border: 1px solid {c.lighter(130).name()}; "
            f"border-radius: 5px; padding: 0px; }}"
            f"QPushButton:hover {{ border: 2px solid #E3E2E0; }}"
        )

    def _open_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(get_active_stylesheet())
        for label, hex_val in PROPERTY_COLOR_PALETTE:
            act = menu.addAction(make_color_dot_icon(hex_val, 11), label)
            act.triggered.connect(lambda _=False, h=hex_val: self.set_color(h, emit=True))
        menu.addSeparator()
        act_custom = menu.addAction("Custom Color...")
        act_custom.triggered.connect(self._pick_custom)
        menu.exec_(self.mapToGlobal(self.rect().bottomLeft()))

    def _pick_custom(self) -> None:
        chosen = QColorDialog.getColor(normalize_qcolor(self._color), self, "Pick Property Color")
        if chosen.isValid():
            self.set_color(chosen.name(), emit=True)


class ColoredPillCombo(QPushButton):
    """
    Notion-style colored pill dropdown button for Status, Select, and single People properties.
    """

    value_changed = Signal(str)

    def __init__(
        self,
        options: List[Tuple[str, str]],
        value: str = "",
        clearable: bool = True,
        allow_custom_text: bool = False,
        compact: bool = False,
        placeholder: str = "Empty",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._options: List[Tuple[str, str]] = list(options or [])
        self._value: str = (value or "").strip()
        self._clearable = clearable
        self._allow_custom_text = allow_custom_text
        self._compact = compact
        self._placeholder = "—" if compact else placeholder

        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._open_menu)
        self._refresh_visual()

    def set_options(self, options: List[Tuple[str, str]]) -> None:
        self._options = list(options or [])
        self._refresh_visual()

    def value(self) -> str:
        return self._value

    def set_value(self, value: str, emit: bool = False) -> None:
        val = (value or "").strip()
        changed = val != self._value
        self._value = val
        self._refresh_visual()
        if emit and changed:
            self.value_changed.emit(self._value)

    def _color_for_value(self, val: str) -> str:
        low = (val or "").strip().lower()
        for name, col in self._options:
            if name.lower() == low:
                return col
        return DEFAULT_OPTION_COLOR

    def _refresh_visual(self) -> None:
        if not self._value:
            self.setText(f"{self._placeholder}  ▾")
            self.setStyleSheet(pill_stylesheet(DEFAULT_OPTION_COLOR, compact=self._compact, muted=True))
        else:
            col = self._color_for_value(self._value)
            self.setText(f"{self._value}  ▾")
            self.setStyleSheet(pill_stylesheet(col, compact=self._compact, muted=False))

    def _open_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(get_active_stylesheet())
        for name, col in self._options:
            act = menu.addAction(make_color_dot_icon(col, 10), name)
            act.setCheckable(True)
            act.setChecked(name.lower() == self._value.lower())
            act.triggered.connect(lambda _=False, v=name: self.set_value(v, emit=True))

        if self._allow_custom_text:
            if self._options:
                menu.addSeparator()
            act_other = menu.addAction("Type custom name...")
            act_other.triggered.connect(self._prompt_custom_text)

        if self._clearable and self._value:
            menu.addSeparator()
            act_clear = menu.addAction("Clear")
            act_clear.triggered.connect(lambda: self.set_value("", emit=True))

        menu.exec_(self.mapToGlobal(self.rect().bottomLeft()))

    def _prompt_custom_text(self) -> None:
        txt, ok = QInputDialog.getText(self, "Custom Value", "Enter name:", text=self._value)
        if ok:
            self.set_value(txt.strip(), emit=True)


class MultiTagPicker(QPushButton):
    """
    Notion-style multi-select / multi-people pill picker with checkable colored dropdown menu.
    Values are serialized as comma-separated strings (e.g., 'Alice, Bob').
    """

    value_changed = Signal(str)

    def __init__(
        self,
        options: List[Tuple[str, str]],
        value: str = "",
        compact: bool = False,
        placeholder: str = "Select...",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._options: List[Tuple[str, str]] = list(options or [])
        self._selected: List[str] = ShotProperty.parse_multi_value(value)
        self._compact = compact
        self._placeholder = "—" if compact else placeholder

        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._open_menu)

        self._pill_layout = QHBoxLayout(self)
        self._pill_layout.setContentsMargins(6 if compact else 8, 2 if compact else 4, 20, 2 if compact else 4)
        self._pill_layout.setSpacing(4)

        self._refresh_visual()

    def set_options(self, options: List[Tuple[str, str]]) -> None:
        self._options = list(options or [])
        self._refresh_visual()

    def value(self) -> str:
        return ShotProperty.format_multi_value(self._selected)

    def set_value(self, value: str, emit: bool = False) -> None:
        parsed = ShotProperty.parse_multi_value(value)
        changed = parsed != self._selected
        self._selected = parsed
        self._refresh_visual()
        if emit and changed:
            self.value_changed.emit(self.value())

    def _color_for(self, name: str) -> str:
        low = name.strip().lower()
        for opt_name, col in self._options:
            if opt_name.lower() == low:
                return col
        return DEFAULT_OPTION_COLOR

    def _clear_pills(self) -> None:
        while self._pill_layout.count():
            item = self._pill_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _refresh_visual(self) -> None:
        self._clear_pills()
        pad = "2px 6px" if self._compact else "4px 8px"
        self.setStyleSheet(
            f"QPushButton {{ background-color: #202020; color: #9B9A97; "
            f"border: 1px solid #2F2F2F; border-radius: 5px; padding: {pad}; text-align: right; }}"
            f"QPushButton:hover {{ background-color: #2C2C2C; border-color: #3E3E3E; }}"
        )

        if not self._selected:
            self.setText(f"{self._placeholder}  ▾")
            self.setStyleSheet(pill_stylesheet(DEFAULT_OPTION_COLOR, compact=self._compact, muted=True))
            return

        self.setText("▾")
        max_visible = 2 if self._compact else 4
        shown = self._selected[:max_visible]
        overflow = len(self._selected) - len(shown)

        for tag in shown:
            c = normalize_qcolor(self._color_for(tag))
            lbl = QLabel(tag)
            lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            lbl.setStyleSheet(
                f"background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 0.28); "
                f"border: 1px solid rgba({c.red()}, {c.green()}, {c.blue()}, 0.55); "
                f"border-radius: 4px; color: #F1F0EE; padding: 1px 6px; font-size: 11px; font-weight: 600;"
            )
            self._pill_layout.addWidget(lbl)

        if overflow > 0:
            more = QLabel(f"+{overflow}")
            more.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            more.setStyleSheet(
                "background-color: #2C2C2C; border: 1px solid #3E3E3E; border-radius: 4px; "
                "color: #9B9A97; padding: 1px 5px; font-size: 11px; font-weight: 600;"
            )
            self._pill_layout.addWidget(more)

        self._pill_layout.addStretch(1)
        self.setToolTip(", ".join(self._selected))

    def _toggle_item(self, name: str) -> None:
        low = name.lower()
        existing = [x for x in self._selected if x.lower() != low]
        if len(existing) == len(self._selected):
            existing.append(name)
        self._selected = existing
        self._refresh_visual()
        self.value_changed.emit(self.value())

    def _open_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(get_active_stylesheet())
        selected_low = {s.lower() for s in self._selected}

        all_opts = list(self._options)
        known_low = {n.lower() for n, _ in all_opts}
        for s in self._selected:
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
                act.triggered.connect(lambda _=False, n=name: self._toggle_item(n))

        if self._selected:
            menu.addSeparator()
            act_clear = menu.addAction("Clear All")
            act_clear.triggered.connect(lambda: self.set_value("", emit=True))

        menu.exec_(self.mapToGlobal(self.rect().bottomLeft()))


class DatePickerButton(QPushButton):
    """Notion-style date button opening a QCalendarWidget popup with Today / Clear controls."""

    value_changed = Signal(str)

    def __init__(self, value: str = "", compact: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._value = (value or "").strip()
        self._compact = compact
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._open_calendar)
        self._refresh_visual()

    def value(self) -> str:
        return self._value

    def set_value(self, value: str, emit: bool = False) -> None:
        val = (value or "").strip()
        changed = val != self._value
        self._value = val
        self._refresh_visual()
        if emit and changed:
            self.value_changed.emit(self._value)

    def _refresh_visual(self) -> None:
        pad = "2px 8px" if self._compact else "4px 10px"
        if not self._value:
            self.setText("—" if self._compact else "Pick date...")
            self.setStyleSheet(
                f"QPushButton {{ background-color: #202020; color: #7C7B78; "
                f"border: 1px solid #2F2F2F; border-radius: 5px; padding: {pad}; text-align: left; }}"
                f"QPushButton:hover {{ background-color: #2C2C2C; color: #E3E2E0; border-color: #3E3E3E; }}"
            )
        else:
            self.setText(self._value)
            self.setStyleSheet(
                f"QPushButton {{ background-color: #202020; color: #E3E2E0; "
                f"border: 1px solid #2F2F2F; border-radius: 5px; padding: {pad}; text-align: left; font-weight: 500; }}"
                f"QPushButton:hover {{ background-color: #2C2C2C; border-color: #3E3E3E; }}"
            )

    def _open_calendar(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(get_active_stylesheet())

        container = QWidget(menu)
        lay = QVBoxLayout(container)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        cal = QCalendarWidget(container)
        cal.setGridVisible(False)
        cal.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        parsed = QDate.fromString(self._value, "yyyy-MM-dd")
        if parsed.isValid():
            cal.setSelectedDate(parsed)
        else:
            cal.setSelectedDate(QDate.currentDate())

        def _on_pick(qdate: QDate) -> None:
            self.set_value(qdate.toString("yyyy-MM-dd"), emit=True)
            menu.close()

        cal.clicked.connect(_on_pick)
        lay.addWidget(cal)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        today_btn = QPushButton("Today")
        today_btn.clicked.connect(lambda: _on_pick(QDate.currentDate()))
        btn_row.addWidget(today_btn)

        btn_row.addStretch()

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: (self.set_value("", emit=True), menu.close()))
        btn_row.addWidget(clear_btn)
        lay.addLayout(btn_row)

        act = QWidgetAction(menu)
        act.setDefaultWidget(container)
        menu.addAction(act)
        menu.exec_(self.mapToGlobal(self.rect().bottomLeft()))


def build_options_for_property(prop: ShotProperty, project: Optional[Project]) -> List[Tuple[str, str]]:
    """Return [(label, hex_color), ...] for status, select, multi_select, or people properties."""
    if prop.prop_type == PropertyType.PEOPLE:
        if not project:
            return []
        return [(a.name, project.get_artist_color(a.name)) for a in project.list_artists(active_only=True)]
    return [(o.name, o.color) for o in prop.options if o.name]


def create_property_editor(
    prop: ShotProperty,
    project: Optional[Project],
    current_value: str = "",
    compact: bool = False,
    on_change: Optional[Callable[[str], None]] = None,
) -> Tuple[QWidget, Callable[[], str], Callable[[str], None]]:
    """
    Factory creating the appropriate Notion-Dark widget for any ShotProperty type.
    Returns (widget, getter_fn, setter_fn).
    """
    ptype = prop.prop_type
    val_str = "" if current_value is None else str(current_value)

    if ptype == PropertyType.STATUS:
        opts = build_options_for_property(prop, project)
        w = ColoredPillCombo(
            opts,
            value=val_str or prop.default_value or "Pending",
            clearable=False,
            compact=compact,
        )
        if on_change:
            w.value_changed.connect(on_change)
        return w, w.value, lambda v: w.set_value(v, emit=False)

    if ptype == PropertyType.SELECT:
        opts = build_options_for_property(prop, project)
        w = ColoredPillCombo(
            opts,
            value=val_str,
            clearable=True,
            compact=compact,
        )
        if on_change:
            w.value_changed.connect(on_change)
        return w, w.value, lambda v: w.set_value(v, emit=False)

    if ptype == PropertyType.MULTI_SELECT:
        opts = build_options_for_property(prop, project)
        w = MultiTagPicker(opts, value=val_str, compact=compact)
        if on_change:
            w.value_changed.connect(on_change)
        return w, w.value, lambda v: w.set_value(v, emit=False)

    if ptype == PropertyType.PEOPLE:
        opts = build_options_for_property(prop, project)
        if prop.allow_multiple:
            w = MultiTagPicker(opts, value=val_str, compact=compact, placeholder="Unassigned")
            if on_change:
                w.value_changed.connect(on_change)
            return w, w.value, lambda v: w.set_value(v, emit=False)
        else:
            w = ColoredPillCombo(
                opts,
                value=val_str,
                clearable=True,
                allow_custom_text=True,
                compact=compact,
                placeholder="Unassigned",
            )
            if on_change:
                w.value_changed.connect(on_change)
            return w, w.value, lambda v: w.set_value(v, emit=False)

    if ptype == PropertyType.DATE:
        w = DatePickerButton(value=val_str, compact=compact)
        if on_change:
            w.value_changed.connect(on_change)
        return w, w.value, lambda v: w.set_value(v, emit=False)

    if ptype == PropertyType.CHECKBOX:
        chk = QCheckBox()
        chk.setChecked(ShotProperty.parse_bool(val_str))
        if on_change:
            chk.toggled.connect(lambda checked: on_change(ShotProperty.format_bool(checked)))
        if compact:
            wrap = QWidget()
            wrap.setStyleSheet("background: transparent;")
            hl = QHBoxLayout(wrap)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setAlignment(Qt.AlignCenter)
            hl.addWidget(chk)
            return (
                wrap,
                lambda: ShotProperty.format_bool(chk.isChecked()),
                lambda v: chk.setChecked(ShotProperty.parse_bool(v)),
            )
        return (
            chk,
            lambda: ShotProperty.format_bool(chk.isChecked()),
            lambda v: chk.setChecked(ShotProperty.parse_bool(v)),
        )

    if ptype == PropertyType.NUMBER:
        spin = QDoubleSpinBox()
        spin.setRange(-999999999.0, 999999999.0)
        spin.setDecimals(2)
        spin.setSpecialValueText("")
        try:
            spin.setValue(float(val_str))
        except (TypeError, ValueError):
            spin.setValue(0.0)

        def _fmt_num() -> str:
            v = spin.value()
            if v == int(v):
                return str(int(v))
            return f"{v:g}"

        if on_change:
            spin.valueChanged.connect(lambda _v: on_change(_fmt_num()))

        def _set_num(v: str) -> None:
            try:
                spin.setValue(float(v))
            except (TypeError, ValueError):
                spin.setValue(0.0)

        return spin, _fmt_num, _set_num

    # Default: TEXT
    edit = QLineEdit(val_str)
    edit.setPlaceholderText("Empty")
    if on_change:
        edit.editingFinished.connect(lambda: on_change(edit.text().strip()))
    return edit, lambda: edit.text().strip(), lambda v: edit.setText(str(v or ""))
