"""
gui/main_window.py
------------------
The desktop UI.

Layout
    LEFT    : open button, parameter editor, material list, region list
    CENTER  : 3D view plus a camera toolbar
    RIGHT   : selected-region details and validation results
    BOTTOM  : parsing log

The window owns the ParseResult.  Editing a parameter re-runs the parser
over the original source text with an override, which recomputes every
dependent expression exactly as SDE would, then reloads the 3D scene.
"""

import os
from typing import Dict, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDockWidget, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QSizePolicy, QSlider, QSplitter, QStatusBar, QTableWidget,
    QTableWidgetItem,
    QTabWidget, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from models.region import ParseResult, material_color
from parser.scm_parser import ScmParser, write_modified_scm
from validation.geometry_checks import run_all_checks, PASS, FAIL, WARN, SKIP
from viewer.vtk_viewer import DeviceViewer


STATUS_COLORS = {
    PASS: "#2e9e4f",
    FAIL: "#d13438",
    WARN: "#d9822b",
    SKIP: "#7a848e",
}


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SCM Device Viewer")
        self.resize(1680, 980)

        self.result: Optional[ParseResult] = None
        self.current_path: Optional[str] = None
        self.baseline_params: Dict[str, float] = {}
        self.overrides: Dict[str, float] = {}

        self.viewer = DeviceViewer(self)
        self.viewer.on_pick = self._on_pick
        self.setCentralWidget(self._build_center())

        self._build_left_dock()
        self._build_right_dock()
        self._build_bottom_dock()
        self._build_menu()

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Open an .scm file to begin")

        QTimer.singleShot(0, self.viewer.enable_region_picking)

    # ==================================================================
    #  centre : 3D view + camera toolbar
    # ==================================================================
    def _build_center(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QToolBar()
        bar.setMovable(False)
        for label in ("Front", "Back", "Left", "Right", "Top", "Bottom",
                      "Isometric"):
            act = QAction(label, self)
            act.triggered.connect(
                lambda _=False, l=label: self.viewer.view_preset(l))
            bar.addAction(act)
        bar.addSeparator()

        act_reset = QAction("Reset camera", self)
        act_reset.triggered.connect(self.viewer.reset_view)
        bar.addAction(act_reset)
        bar.addSeparator()

        self.proj_box = QComboBox()
        self.proj_box.addItems(["Perspective", "Orthographic"])
        self.proj_box.currentTextChanged.connect(
            lambda t: self.viewer.set_parallel(t == "Orthographic"))
        bar.addWidget(QLabel("  Projection: "))
        bar.addWidget(self.proj_box)

        self.style_box = QComboBox()
        self.style_box.addItems(["Surface", "Wireframe"])
        self.style_box.currentTextChanged.connect(
            lambda t: self.viewer.set_style(t.lower()))
        bar.addWidget(QLabel("   Style: "))
        bar.addWidget(self.style_box)

        bar.addWidget(QLabel("   Transparency: "))
        self.alpha = QSlider(Qt.Horizontal)
        self.alpha.setRange(5, 100)
        self.alpha.setValue(100)
        self.alpha.setFixedWidth(160)
        self.alpha.valueChanged.connect(
            lambda v: self.viewer.set_opacity(v / 100.0))
        bar.addWidget(self.alpha)

        self.cb_contacts = QCheckBox("Contacts")
        self.cb_contacts.setChecked(True)
        self.cb_contacts.toggled.connect(self.viewer.set_show_contacts)
        bar.addWidget(self.cb_contacts)

        self.cb_windows = QCheckBox("Refinement windows")
        self.cb_windows.setChecked(False)
        self.cb_windows.toggled.connect(self.viewer.set_show_windows)
        bar.addWidget(self.cb_windows)

        lay.addWidget(bar)
        lay.addWidget(self.viewer.interactor)
        return w

    # ==================================================================
    #  left dock
    # ==================================================================
    def _build_left_dock(self):
        dock = QDockWidget("Model", self)
        dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        body = QWidget()
        lay = QVBoxLayout(body)

        btn_open = QPushButton("Open SCM file…")
        btn_open.clicked.connect(self.open_file)
        lay.addWidget(btn_open)

        self.lbl_file = QLabel("no file loaded")
        self.lbl_file.setWordWrap(True)
        self.lbl_file.setStyleSheet("color:#7a848e;")
        lay.addWidget(self.lbl_file)

        # ------------------------------------------------------------------
        #  Parameters / Materials / Regions live inside a vertical splitter
        #  so the user can drag the separators between them.  Each section
        #  keeps its own QGroupBox and all of its original widgets; only the
        #  height policy changed.
        # ------------------------------------------------------------------
        self.left_splitter = QSplitter(Qt.Vertical)
        self.left_splitter.setChildrenCollapsible(False)
        self.left_splitter.setHandleWidth(6)
        self.left_splitter.setOpaqueResize(True)

        # ---- parameters -------------------------------------------------
        grp = QGroupBox("Parameters")
        gl = QVBoxLayout(grp)
        self.param_table = QTableWidget(0, 2)
        self.param_table.setHorizontalHeaderLabels(["Parameter", "Value"])
        self.param_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.param_table.verticalHeader().setVisible(False)
        self.param_table.setMinimumHeight(60)
        self.param_table.setSizePolicy(QSizePolicy.Expanding,
                                       QSizePolicy.Expanding)
        gl.addWidget(self.param_table)

        row = QHBoxLayout()
        b_apply = QPushButton("Apply")
        b_apply.clicked.connect(self.apply_parameters)
        b_reset = QPushButton("Reset")
        b_reset.clicked.connect(self.reset_parameters)
        b_save = QPushButton("Save SCM…")
        b_save.clicked.connect(self.save_modified)
        for b in (b_apply, b_reset, b_save):
            row.addWidget(b)
        gl.addLayout(row)
        grp.setMinimumHeight(130)
        self.left_splitter.addWidget(grp)

        # ---- materials --------------------------------------------------
        grp_m = QGroupBox("Materials")
        ml = QVBoxLayout(grp_m)
        self.material_list = QListWidget()
        self.material_list.setMinimumHeight(50)
        self.material_list.setSizePolicy(QSizePolicy.Expanding,
                                         QSizePolicy.Expanding)
        self.material_list.itemChanged.connect(self._material_toggled)
        ml.addWidget(self.material_list)
        grp_m.setMinimumHeight(90)
        self.left_splitter.addWidget(grp_m)

        # ---- regions ----------------------------------------------------
        grp_r = QGroupBox("Regions")
        rl = QVBoxLayout(grp_r)
        self.region_filter = QLineEdit()
        self.region_filter.setPlaceholderText("filter by name…")
        self.region_filter.textChanged.connect(self._filter_regions)
        rl.addWidget(self.region_filter)

        self.region_tree = QTreeWidget()
        self.region_tree.setHeaderLabels(["Region", "Material"])
        self.region_tree.setMinimumHeight(60)
        self.region_tree.setSizePolicy(QSizePolicy.Expanding,
                                       QSizePolicy.Expanding)
        self.region_tree.itemChanged.connect(self._region_toggled)
        self.region_tree.itemSelectionChanged.connect(self._region_selected)
        rl.addWidget(self.region_tree)

        rrow = QHBoxLayout()
        b_hide = QPushButton("Hide selected")
        b_hide.clicked.connect(self._hide_selected)
        b_all = QPushButton("Show all")
        b_all.clicked.connect(self._show_all)
        rrow.addWidget(b_hide)
        rrow.addWidget(b_all)
        rl.addLayout(rrow)
        grp_r.setMinimumHeight(150)
        self.left_splitter.addWidget(grp_r)

        # Initial proportions, and how the spare space is shared when the
        # main window is resized: Regions grows most, Materials least.
        self.left_splitter.setStretchFactor(0, 2)   # Parameters
        self.left_splitter.setStretchFactor(1, 1)   # Materials
        self.left_splitter.setStretchFactor(2, 3)   # Regions
        self.left_splitter.setSizes([280, 150, 380])

        lay.addWidget(self.left_splitter, 1)

        dock.setWidget(body)
        dock.setMinimumWidth(340)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    # ==================================================================
    #  right dock
    # ==================================================================
    def _build_right_dock(self):
        dock = QDockWidget("Inspector", self)
        tabs = QTabWidget()

        # ---- selected region --------------------------------------------
        info = QWidget()
        form = QFormLayout(info)
        self.info_fields = {}
        for key in ("Region Name", "Material",
                    "X minimum", "X maximum",
                    "Y minimum", "Y maximum",
                    "Z minimum", "Z maximum",
                    "X length", "Y length", "Z length",
                    "Volume", "Source line"):
            lab = QLabel("-")
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.info_fields[key] = lab
            form.addRow(key + ":", lab)
        tabs.addTab(info, "Region")

        # ---- contacts ----------------------------------------------------
        cwrap = QWidget()
        cl = QVBoxLayout(cwrap)
        self.contact_tree = QTreeWidget()
        self.contact_tree.setHeaderLabels(["Contact", "Region", "Face"])
        self.contact_tree.itemSelectionChanged.connect(self._contact_selected)
        cl.addWidget(self.contact_tree)
        self.contact_detail = QLabel("select a contact")
        self.contact_detail.setWordWrap(True)
        cl.addWidget(self.contact_detail)
        tabs.addTab(cwrap, "Contacts")

        # ---- validation ---------------------------------------------------
        vwrap = QWidget()
        vl = QVBoxLayout(vwrap)
        b_run = QPushButton("Run geometry checks")
        b_run.clicked.connect(self.run_validation)
        vl.addWidget(b_run)
        self.validation_tree = QTreeWidget()
        self.validation_tree.setHeaderLabels(["Check", "Result"])
        self.validation_tree.setColumnWidth(0, 240)
        vl.addWidget(self.validation_tree)
        tabs.addTab(vwrap, "Validation")

        dock.setWidget(tabs)
        dock.setMinimumWidth(340)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

    # ==================================================================
    #  bottom dock
    # ==================================================================
    def _build_bottom_dock(self):
        dock = QDockWidget("Parsing log", self)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("monospace", 9))
        self.log.setMaximumBlockCount(5000)
        dock.setWidget(self.log)
        dock.setMinimumHeight(140)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)

    def _build_menu(self):
        m = self.menuBar().addMenu("&File")
        a_open = QAction("&Open SCM…", self)
        a_open.setShortcut(QKeySequence.Open)
        a_open.triggered.connect(self.open_file)
        m.addAction(a_open)

        a_reload = QAction("&Reload", self)
        a_reload.setShortcut("F5")
        a_reload.triggered.connect(lambda: self.load_path(self.current_path)
                                   if self.current_path else None)
        m.addAction(a_reload)

        a_save = QAction("&Save modified SCM…", self)
        a_save.triggered.connect(self.save_modified)
        m.addAction(a_save)

        m.addSeparator()
        a_shot = QAction("Save screenshot…", self)
        a_shot.triggered.connect(self.save_screenshot)
        m.addAction(a_shot)

        m.addSeparator()
        a_quit = QAction("&Quit", self)
        a_quit.setShortcut(QKeySequence.Quit)
        a_quit.triggered.connect(self.close)
        m.addAction(a_quit)

    # ==================================================================
    #  loading
    # ==================================================================
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Sentaurus SDE Scheme file", "",
            "Scheme files (*.scm *.cmd *.txt);;All files (*)")
        if path:
            self.load_path(path)

    def load_path(self, path: str, overrides: Dict[str, float] = None):
        self.log.clear()
        try:
            result = ScmParser().parse_file(path, overrides=overrides)
        except Exception as exc:                              # noqa: BLE001
            QMessageBox.critical(self, "Parse failed",
                                 f"Could not read {os.path.basename(path)}:\n\n"
                                 f"{type(exc).__name__}: {exc}")
            return

        self.result = result
        self.current_path = path
        self.overrides = dict(overrides or {})
        if not self.overrides:
            self.baseline_params = dict(result.parameters)

        self.lbl_file.setText(os.path.basename(path))
        self._fill_log(result)
        self._fill_parameters(result)
        self._fill_materials(result)
        self._fill_regions(result)
        self._fill_contacts(result)

        self.viewer.load(result.regions, result.contacts, result.windows,
                         reset_camera=not overrides)
        self.run_validation()

        n_err, n_warn = len(result.errors), len(result.warnings)
        self.statusBar().showMessage(
            f"{len(result.regions)} regions loaded | "
            f"{len(result.materials)} materials | "
            f"{len(result.contacts)} contacts | "
            f"{n_err} errors, {n_warn} warnings")

    # ==================================================================
    #  panel population
    # ==================================================================
    def _fill_log(self, result: ParseResult):
        for m in result.messages:
            self.log.appendPlainText(str(m))
        self.log.appendPlainText(
            f"--- {len(result.regions)} regions loaded, "
            f"{len(result.errors)} errors, {len(result.warnings)} warnings ---")

    def _fill_parameters(self, result: ParseResult):
        self.param_table.blockSignals(True)
        self.param_table.setRowCount(0)
        for name, val in sorted(result.parameters.items()):
            row = self.param_table.rowCount()
            self.param_table.insertRow(row)
            item_n = QTableWidgetItem(name)
            item_n.setFlags(item_n.flags() & ~Qt.ItemIsEditable)
            self.param_table.setItem(row, 0, item_n)
            self.param_table.setItem(row, 1, QTableWidgetItem(f"{val:g}"))
        self.param_table.blockSignals(False)

    def _fill_materials(self, result: ParseResult):
        self.material_list.blockSignals(True)
        self.material_list.clear()
        for mat in result.materials:
            it = QListWidgetItem(mat)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            r, g, b = material_color(mat)
            it.setForeground(QColor(int(r * 255), int(g * 255), int(b * 255)))
            self.material_list.addItem(it)
        self.material_list.blockSignals(False)

    def _fill_regions(self, result: ParseResult):
        self.region_tree.blockSignals(True)
        self.region_tree.clear()
        by_mat = {}
        for r in result.regions:
            by_mat.setdefault(r.material, []).append(r)
        for mat in sorted(by_mat):
            top = QTreeWidgetItem([mat, f"{len(by_mat[mat])} regions"])
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable)
            top.setCheckState(0, Qt.Checked)
            rr, gg, bb = material_color(mat)
            top.setForeground(0, QColor(int(rr * 255), int(gg * 255), int(bb * 255)))
            for r in by_mat[mat]:
                child = QTreeWidgetItem([r.name, r.material])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Checked)
                child.setData(0, Qt.UserRole, r.name)
                top.addChild(child)
            self.region_tree.addTopLevelItem(top)
        self.region_tree.expandAll()
        self.region_tree.blockSignals(False)

    def _fill_contacts(self, result: ParseResult):
        self.contact_tree.clear()
        for c in result.contacts:
            it = QTreeWidgetItem([c.name,
                                  c.region_name or "unresolved",
                                  c.face or "-"])
            it.setData(0, Qt.UserRole, c.name)
            self.contact_tree.addTopLevelItem(it)

    # ==================================================================
    #  parameter editing
    # ==================================================================
    def apply_parameters(self):
        if not self.result or not self.current_path:
            return
        overrides = {}
        for row in range(self.param_table.rowCount()):
            name = self.param_table.item(row, 0).text()
            raw = self.param_table.item(row, 1).text().strip()
            try:
                overrides[name] = float(raw)
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid value",
                    f"'{raw}' is not a number (parameter {name}).")
                return

        changed = {k: v for k, v in overrides.items()
                   if abs(v - self.baseline_params.get(k, v)) > 1e-15}
        self.log.appendPlainText(
            f"\n=== re-parsing with {len(changed)} changed parameter(s): "
            f"{', '.join(f'{k}={v:g}' for k, v in changed.items()) or 'none'} ===")
        self.load_path(self.current_path, overrides=overrides)

    def reset_parameters(self):
        if not self.current_path:
            return
        self.log.appendPlainText("\n=== parameters reset to file values ===")
        self.load_path(self.current_path, overrides=None)

    def save_modified(self):
        if not self.result:
            return
        overrides = {}
        for row in range(self.param_table.rowCount()):
            name = self.param_table.item(row, 0).text()
            try:
                overrides[name] = float(self.param_table.item(row, 1).text())
            except ValueError:
                pass
        path, _ = QFileDialog.getSaveFileName(
            self, "Save modified SCM", "", "Scheme files (*.scm)")
        if not path:
            return
        text = write_modified_scm(self.result.source_text, overrides)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.statusBar().showMessage(f"Saved {os.path.basename(path)}")
        self.log.appendPlainText(f"[INFO] wrote {path}")

    def save_screenshot(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save screenshot", "", "PNG image (*.png)")
        if path:
            self.viewer.save_screenshot_to(path)
            self.statusBar().showMessage(f"Saved {os.path.basename(path)}")

    # ==================================================================
    #  visibility
    # ==================================================================
    def _material_toggled(self, item: QListWidgetItem):
        self.viewer.set_material_visible(
            item.text(), item.checkState() == Qt.Checked)

    def _region_toggled(self, item: QTreeWidgetItem, column: int):
        if column != 0:
            return
        name = item.data(0, Qt.UserRole)
        checked = item.checkState(0) == Qt.Checked
        if name is None:
            # a material group: cascade to children
            self.region_tree.blockSignals(True)
            for i in range(item.childCount()):
                item.child(i).setCheckState(
                    0, Qt.Checked if checked else Qt.Unchecked)
            self.region_tree.blockSignals(False)
            self.viewer.set_material_visible(item.text(0), checked)
        else:
            self.viewer.set_region_visible(name, checked)

    def _hide_selected(self):
        names = [it.data(0, Qt.UserRole)
                 for it in self.region_tree.selectedItems()
                 if it.data(0, Qt.UserRole)]
        if not names:
            return
        self.region_tree.blockSignals(True)
        for it in self.region_tree.selectedItems():
            if it.data(0, Qt.UserRole):
                it.setCheckState(0, Qt.Unchecked)
        self.region_tree.blockSignals(False)
        self.viewer.hide_regions(names)

    def _show_all(self):
        self.region_tree.blockSignals(True)
        it = self.region_tree.invisibleRootItem()
        for i in range(it.childCount()):
            top = it.child(i)
            top.setCheckState(0, Qt.Checked)
            for j in range(top.childCount()):
                top.child(j).setCheckState(0, Qt.Checked)
        self.region_tree.blockSignals(False)
        self.material_list.blockSignals(True)
        for i in range(self.material_list.count()):
            self.material_list.item(i).setCheckState(Qt.Checked)
        self.material_list.blockSignals(False)
        self.viewer.show_all()

    def _filter_regions(self, text: str):
        text = text.lower().strip()
        root = self.region_tree.invisibleRootItem()
        for i in range(root.childCount()):
            top = root.child(i)
            any_visible = False
            for j in range(top.childCount()):
                child = top.child(j)
                hit = text in child.text(0).lower()
                child.setHidden(bool(text) and not hit)
                any_visible |= hit or not text
            top.setHidden(bool(text) and not any_visible)

    # ==================================================================
    #  selection
    # ==================================================================
    def _region_selected(self):
        items = self.region_tree.selectedItems()
        name = items[0].data(0, Qt.UserRole) if items else None
        self._show_region_info(name)
        self.viewer.highlight(name)

    def _on_pick(self, name: Optional[str]):
        """Called from the 3D view when the user clicks a body."""
        self._show_region_info(name)
        self.viewer.highlight(name)
        if not name:
            return
        root = self.region_tree.invisibleRootItem()
        for i in range(root.childCount()):
            top = root.child(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if child.data(0, Qt.UserRole) == name:
                    self.region_tree.setCurrentItem(child)
                    return

    def _show_region_info(self, name: Optional[str]):
        if not name or not self.result:
            for lab in self.info_fields.values():
                lab.setText("-")
            return
        r = self.result.region_by_name(name)
        if r is None:
            return
        f = self.info_fields
        f["Region Name"].setText(r.name)
        f["Material"].setText(r.material)
        f["X minimum"].setText(f"{r.x0:.6g}")
        f["X maximum"].setText(f"{r.x1:.6g}")
        f["Y minimum"].setText(f"{r.y0:.6g}")
        f["Y maximum"].setText(f"{r.y1:.6g}")
        f["Z minimum"].setText(f"{r.z0:.6g}")
        f["Z maximum"].setText(f"{r.z1:.6g}")
        f["X length"].setText(f"{r.lx:.6g}")
        f["Y length"].setText(f"{r.ly:.6g}")
        f["Z length"].setText(f"{r.lz:.6g}")
        f["Volume"].setText(f"{r.volume:.4g}")
        f["Source line"].setText(str(r.source_line or "-"))

    def _contact_selected(self):
        items = self.contact_tree.selectedItems()
        if not items or not self.result:
            self.contact_detail.setText("select a contact")
            return
        nm = items[0].data(0, Qt.UserRole)
        c = next((x for x in self.result.contacts if x.name == nm), None)
        if c is None:
            return
        pos = ("(" + ", ".join(f"{v:.6g}" for v in c.position) + ")"
               if c.position else "not attached")
        self.contact_detail.setText(
            f"<b>{c.name}</b><br>"
            f"region: {c.region_name or 'unresolved'}<br>"
            f"coordinate: {pos}<br>"
            f"face direction: {c.face or '-'}<br>"
            f"defined at line: {c.source_line or '-'}")

    # ==================================================================
    #  validation
    # ==================================================================
    def run_validation(self):
        self.validation_tree.clear()
        if not self.result:
            return
        results = run_all_checks(self.result.regions, self.result.contacts)
        for res in results:
            it = QTreeWidgetItem([res.name, f"{res.status} - {res.detail}"])
            it.setForeground(1, QColor(STATUS_COLORS.get(res.status, "#000")))
            for sub in (res.items or []):
                it.addChild(QTreeWidgetItem(["", sub]))
            self.validation_tree.addTopLevelItem(it)
        self.validation_tree.expandAll()

        n_fail = sum(1 for r in results if r.status == FAIL)
        if n_fail:
            self.log.appendPlainText(
                f"[VALIDATION] {n_fail} check(s) FAILED - see the "
                f"Validation tab")
        else:
            self.log.appendPlainText("[VALIDATION] all checks passed or skipped")