"""Overlay panel: choose which data is shown, presets, units, and per-item settings."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QPushButton, QScrollArea, QSlider, QSpinBox,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..overlay.presets import (PRESET_NAMES, list_user_presets, load_user_preset, make_preset,
                               save_user_preset)
from ..overlay.widgets import WIDGETS
from ..telemetry.channels import CHANNEL_BY_KEY, CHANNELS
from ..telemetry.units import SPEED_UNITS

GROUP_ORDER = ["Flight", "Attitude", "Navigation", "Power", "Environment", "Link", "Time", "Other"]


def _item_group(cfg) -> str:
    if cfg.type == "readout":
        c = CHANNEL_BY_KEY.get(cfg.channel or "")
        return c.group if c else "Flight"
    return WIDGETS[cfg.type].group


def _item_title(cfg) -> str:
    if cfg.type == "readout":
        c = CHANNEL_BY_KEY.get(cfg.channel or "")
        return (c.label if c else cfg.channel or "?") + "  (number)"
    return WIDGETS[cfg.type].title


def _slider(lo, hi) -> QSlider:
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(lo, hi)
    return s


class ColorButton(QPushButton):
    def __init__(self, on_pick, parent=None):
        super().__init__(parent)
        self.on_pick = on_pick
        self.color = ""
        self.clicked.connect(self._pick)
        self.setFixedWidth(70)

    def set_color(self, c: str) -> None:
        self.color = c
        self.setText("default" if not c else "")
        self.setStyleSheet(f"background: {c};" if c else "")

    def _pick(self) -> None:
        col = QColorDialog.getColor(QColor(self.color or "#ffffff"), self, "Choose colour")
        if col.isValid():
            self.set_color(col.name())
            self.on_pick(col.name())


class OverlayPanel(QScrollArea):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.setWidget(body)
        lay = QVBoxLayout(body)
        self._loading = False

        # presets
        row = QHBoxLayout()
        row.addWidget(QLabel("Layout"))
        self.preset = QComboBox()
        row.addWidget(self.preset, 1)
        b = QPushButton("Apply")
        b.setToolTip("Replace the current overlay with this layout")
        b.clicked.connect(self._apply_preset)
        row.addWidget(b)
        b = QPushButton("Save…")
        b.setToolTip("Save the current overlay as your own layout")
        b.clicked.connect(self._save_preset)
        row.addWidget(b)
        lay.addLayout(row)

        # units
        row = QHBoxLayout()
        row.addWidget(QLabel("Units"))
        self.system = QComboBox()
        self.system.addItem("Metric (m)", "metric")
        self.system.addItem("Imperial (ft)", "imperial")
        self.system.currentIndexChanged.connect(self._units_changed)
        row.addWidget(self.system)
        row.addWidget(QLabel("Speed"))
        self.speed = QComboBox()
        for u in SPEED_UNITS:
            self.speed.addItem(u, u)
        self.speed.currentIndexChanged.connect(self._units_changed)
        row.addWidget(self.speed)
        lay.addLayout(row)

        hint = QLabel("Tick the data to show. Drag items in the preview to move them; "
                      "use the mouse wheel over an item to resize it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #a0a4ad;")
        lay.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(260)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.currentItemChanged.connect(self._current_changed)
        lay.addWidget(self.tree, 1)

        # selected item settings
        self.item_box = QGroupBox("Selected item")
        fl = QFormLayout(self.item_box)
        self.size = _slider(30, 300)
        self.size.valueChanged.connect(lambda v: self._set_cfg("scale", v / 100.0))
        fl.addRow("Size", self.size)
        self.opacity = _slider(20, 100)
        self.opacity.valueChanged.connect(lambda v: self._set_cfg("opacity", v / 100.0))
        fl.addRow("Opacity", self.opacity)
        crow = QHBoxLayout()
        self.color = ColorButton(lambda c: self._set_cfg("color", c))
        crow.addWidget(self.color)
        b = QPushButton("Reset")
        b.clicked.connect(lambda: (self._set_cfg("color", ""), self.color.set_color("")))
        crow.addWidget(b)
        crow.addStretch(1)
        fl.addRow("Colour", crow)
        self.align = QComboBox()
        for t, v in (("Left", "left"), ("Centre", "center"), ("Right", "right")):
            self.align.addItem(t, v)
        self.align.currentIndexChanged.connect(lambda _i: self._set_opt("align", self.align.currentData()))
        fl.addRow("Align", self.align)
        self.label = QLineEdit()
        self.label.setPlaceholderText("default")
        self.label.editingFinished.connect(lambda: self._set_opt("label", self.label.text().strip() or None))
        fl.addRow("Label", self.label)
        self.text = QLineEdit()
        self.text.editingFinished.connect(lambda: self._set_opt("text", self.text.text()))
        fl.addRow("Text", self.text)
        self.tz = QComboBox()
        self.tz.addItem("Local time", "local")
        self.tz.addItem("UTC", "utc")
        self.tz.currentIndexChanged.connect(lambda _i: self._set_opt("tz", self.tz.currentData()))
        fl.addRow("Clock", self.tz)
        self.map_mode = QComboBox()
        self.map_mode.addItem("Whole flight", "fit")
        self.map_mode.addItem("Follow aircraft", "follow")
        self.map_mode.currentIndexChanged.connect(lambda _i: self._set_opt("mode", self.map_mode.currentData()))
        fl.addRow("Map view", self.map_mode)
        self.graph_ch = QComboBox()
        for c in CHANNELS:
            if c.quantity not in ("mode", "bool", "coord"):
                self.graph_ch.addItem(c.label, c.key)
        self.graph_ch.currentIndexChanged.connect(lambda _i: self._set_cfg("channel", self.graph_ch.currentData()))
        fl.addRow("Graph of", self.graph_ch)
        self.graph_win = QSpinBox()
        self.graph_win.setRange(5, 600)
        self.graph_win.setSuffix(" s")
        self.graph_win.valueChanged.connect(lambda v: self._set_opt("window", float(v)))
        fl.addRow("Graph span", self.graph_win)
        b = QPushButton("Reset size")
        b.clicked.connect(lambda: (self._set_cfg("scale", 1.0), self.size.setValue(100)))
        fl.addRow("", b)
        self._form = fl
        lay.addWidget(self.item_box)

        # global style
        g = QGroupBox("Overall style")
        fl = QFormLayout(g)
        self.gscale = _slider(50, 200)
        self.gscale.valueChanged.connect(lambda v: self._set_style("scale", v / 100.0))
        fl.addRow("Overall size", self.gscale)
        self.outline = QCheckBox("Dark outline around text")
        self.outline.toggled.connect(lambda v: self._set_style("outline", v))
        fl.addRow("", self.outline)
        self.panels = QCheckBox("Dark boxes behind items")
        self.panels.toggled.connect(lambda v: self._set_style("panels", v))
        fl.addRow("", self.panels)
        self.panel_op = _slider(0, 90)
        self.panel_op.valueChanged.connect(lambda v: self._set_style("panel_opacity", v / 100.0))
        fl.addRow("Box darkness", self.panel_op)
        crow = QHBoxLayout()
        self.hud_color = ColorButton(lambda c: self._set_style("hud_color", c))
        crow.addWidget(QLabel("HUD"))
        crow.addWidget(self.hud_color)
        self.label_color = ColorButton(lambda c: self._set_style("label_color", c))
        crow.addWidget(QLabel("Labels"))
        crow.addWidget(self.label_color)
        crow.addStretch(1)
        fl.addRow("Colours", crow)
        lay.addWidget(g)

        state.projectReset.connect(self.reload)
        state.telemetryChanged.connect(self.reload)
        state.overlayChanged.connect(self._sync_from_state)
        state.widgetSelected.connect(self._select_widget)
        self.reload()

    # ---------------------------------------------------------------- loading

    def reload(self) -> None:
        self._loading = True
        try:
            self.preset.clear()
            for n in PRESET_NAMES:
                self.preset.addItem(n, ("builtin", n))
            for n in list_user_presets():
                self.preset.addItem(f"{n} (mine)", ("user", n))
            proj = self.state.project
            cur = proj.overlay.preset
            for i in range(self.preset.count()):
                if self.preset.itemData(i)[1] == cur:
                    self.preset.setCurrentIndex(i)
                    break
            self.system.setCurrentIndex(max(0, self.system.findData(proj.units.system)))
            self.speed.setCurrentIndex(max(0, self.speed.findData(proj.units.speed)))
            st = proj.overlay.style
            self.gscale.setValue(int(st.scale * 100))
            self.outline.setChecked(st.outline)
            self.panels.setChecked(st.panels)
            self.panel_op.setValue(int(st.panel_opacity * 100))
            self.hud_color.set_color(st.hud_color)
            self.label_color.set_color(st.label_color)
            self._build_tree()
        finally:
            self._loading = False
        self._current_changed(self.tree.currentItem(), None)

    def _build_tree(self) -> None:
        tel = self.state.telemetry
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for g in GROUP_ORDER:
            it = QTreeWidgetItem([g])
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)
            groups[g] = it
        items = []
        for cfg in self.state.project.overlay.widgets:
            w = WIDGETS.get(cfg.type)
            if w is None:
                continue
            it = QTreeWidgetItem([_item_title(cfg)])
            it.setData(0, Qt.ItemDataRole.UserRole, cfg.id)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Checked if cfg.enabled else Qt.CheckState.Unchecked)
            reason = w.missing_reason(tel, cfg)
            if reason:
                it.setForeground(0, QColor(120, 120, 128))
                it.setToolTip(0, reason + " — it won't be drawn.")
            items.append((_item_group(cfg), cfg.type != "readout", it))
        for gname, _is_gauge, it in sorted(items, key=lambda x: (x[1], x[2].text(0))):
            groups.get(gname, groups["Other"]).addChild(it)
        for g in GROUP_ORDER:
            if groups[g].childCount():
                self.tree.addTopLevelItem(groups[g])
                groups[g].setExpanded(True)

    def _sync_from_state(self) -> None:
        """Keep checkboxes in step with changes made elsewhere (e.g. 'Hide' in the preview)."""
        if self._loading:
            return
        self._loading = True
        try:
            for i in range(self.tree.topLevelItemCount()):
                g = self.tree.topLevelItem(i)
                for j in range(g.childCount()):
                    it = g.child(j)
                    cfg = self.state.project.overlay.get(it.data(0, Qt.ItemDataRole.UserRole))
                    if cfg is not None:
                        want = Qt.CheckState.Checked if cfg.enabled else Qt.CheckState.Unchecked
                        if it.checkState(0) != want:
                            it.setCheckState(0, want)
            cfg = self._current_cfg()
            if cfg is not None:
                self.size.setValue(int(round(cfg.scale * 100)))
        finally:
            self._loading = False

    # ---------------------------------------------------------------- tree events

    def _current_cfg(self):
        it = self.tree.currentItem()
        if it is None:
            return None
        wid = it.data(0, Qt.ItemDataRole.UserRole)
        return self.state.project.overlay.get(wid) if wid else None

    def _item_changed(self, it: QTreeWidgetItem, _col: int) -> None:
        if self._loading:
            return
        wid = it.data(0, Qt.ItemDataRole.UserRole)
        cfg = self.state.project.overlay.get(wid) if wid else None
        if cfg is None:
            return
        cfg.enabled = it.checkState(0) == Qt.CheckState.Checked
        self.state.overlay_edited()

    def _current_changed(self, cur, _prev) -> None:
        cfg = self._current_cfg()
        self.item_box.setEnabled(cfg is not None)
        if cfg is None:
            self.item_box.setTitle("Selected item (click an item above)")
            return
        self.item_box.setTitle(f"Selected: {_item_title(cfg)}")
        if self.state.selected_widget_id != cfg.id:
            self.state.select_widget(cfg.id)
        self._loading = True
        try:
            self.size.setValue(int(round(cfg.scale * 100)))
            self.opacity.setValue(int(round(cfg.opacity * 100)))
            self.color.set_color(cfg.color)
            show = {
                self.align: cfg.type == "readout",
                self.label: cfg.type == "readout",
                self.text: cfg.type == "title",
                self.tz: cfg.type == "clock",
                self.map_mode: cfg.type == "minimap",
                self.graph_ch: cfg.type == "graph",
                self.graph_win: cfg.type == "graph",
            }
            for w, vis in show.items():
                w.setVisible(vis)
                self._form.labelForField(w).setVisible(vis)
            self.align.setCurrentIndex(max(0, self.align.findData(cfg.options.get("align", "left"))))
            self.label.setText(cfg.options.get("label") or "")
            self.text.setText(cfg.options.get("text", ""))
            self.tz.setCurrentIndex(max(0, self.tz.findData(cfg.options.get("tz", "local"))))
            self.map_mode.setCurrentIndex(max(0, self.map_mode.findData(cfg.options.get("mode", "fit"))))
            self.graph_ch.setCurrentIndex(max(0, self.graph_ch.findData(cfg.channel or "alt_rel")))
            self.graph_win.setValue(int(cfg.options.get("window", 60)))
        finally:
            self._loading = False

    def _select_widget(self, wid: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            for j in range(g.childCount()):
                it = g.child(j)
                if it.data(0, Qt.ItemDataRole.UserRole) == wid:
                    if self.tree.currentItem() is not it:
                        self.tree.setCurrentItem(it)
                    return

    # ---------------------------------------------------------------- edits

    def _set_cfg(self, attr: str, value) -> None:
        if self._loading:
            return
        cfg = self._current_cfg()
        if cfg is not None:
            setattr(cfg, attr, value)
            self.state.overlay_edited()

    def _set_opt(self, key: str, value) -> None:
        if self._loading:
            return
        cfg = self._current_cfg()
        if cfg is not None:
            if value is None:
                cfg.options.pop(key, None)
            else:
                cfg.options[key] = value
            self.state.overlay_edited()

    def _set_style(self, attr: str, value) -> None:
        if self._loading:
            return
        setattr(self.state.project.overlay.style, attr, value)
        self.state.overlay_edited()

    def _units_changed(self, _i) -> None:
        if self._loading:
            return
        u = self.state.project.units
        u.system = self.system.currentData()
        u.speed = self.speed.currentData()
        self.state.overlay_edited()

    def _apply_preset(self) -> None:
        kind, name = self.preset.currentData()
        lay = make_preset(name) if kind == "builtin" else load_user_preset(name)
        old = self.state.project.overlay
        title = old.get("title")
        if title is not None and lay.get("title") is not None:
            lay.get("title").options["text"] = title.options.get("text", "")
        self.state.project.overlay = lay
        self.state.overlay_edited()
        self.reload()

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save layout", "Name for this layout:")
        if ok and name.strip():
            save_user_preset(name.strip(), self.state.project.overlay)
            self.reload()
            self.state.status.emit(f"Saved layout '{name.strip()}'")
