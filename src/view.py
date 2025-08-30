import sys
from PySide6 import QtCore, QtGui, QtWidgets
from src.controller import AppController

class RuleEditorDialog(QtWidgets.QDialog):
    """I let the user edit a single rule. Only 'Process' or 'Global' are allowed (UI constrains to Process)."""
    def __init__(self, parent=None, rule=None, is_global=False):
        super().__init__(parent)
        self.setWindowTitle("Edit Rule (Process)" if not is_global else "Edit Rule (Global)")
        self.setModal(True)
        self.rule = rule or {
            "match": "Process",
            "contains": "",
            "active_border_color": "#FF0000",
            "inactive_border_color": "#0000FF",
            "animation": {"type": "none", "speed": 1.0}
        }

        form = QtWidgets.QFormLayout()

        self.cboMatch = QtWidgets.QComboBox()
        if is_global:
            self.cboMatch.addItems(["Global"])
        else:
            self.cboMatch.addItems(["Process"])
        self.cboMatch.setCurrentIndex(0)
        self.cboMatch.setEnabled(False)

        self.txtContains = QtWidgets.QLineEdit(self.rule.get("contains", ""))
        self.txtContains.setEnabled(not is_global)

        self.txtActive = QtWidgets.QLineEdit(self.rule.get("active_border_color", "#FF0000"))
        self.btnPickActive = QtWidgets.QPushButton("Color…")
        self.btnPickActive.clicked.connect(lambda: self._pick_color(self.txtActive))

        self.txtInactive = QtWidgets.QLineEdit(self.rule.get("inactive_border_color", "#0000FF"))
        self.btnPickInactive = QtWidgets.QPushButton("Color…")
        self.btnPickInactive.clicked.connect(lambda: self._pick_color(self.txtInactive))

        self.cboAnimType = QtWidgets.QComboBox()
        self.cboAnimType.addItems(["none", "rainbow", "pulse", "fade", "breath", "tri", "sparkle", "steps"])
        self.cboAnimType.setCurrentText((self.rule.get("animation") or {}).get("type", "none"))
        self.dblAnimSpeed = QtWidgets.QDoubleSpinBox()
        self.dblAnimSpeed.setRange(0.1, 10.0)
        self.dblAnimSpeed.setSingleStep(0.1)
        self.dblAnimSpeed.setValue(float((self.rule.get("animation") or {}).get("speed", 1.0)))

        hl1 = QtWidgets.QHBoxLayout()
        hl1.addWidget(self.txtActive, 1)
        hl1.addWidget(self.btnPickActive)
        hl2 = QtWidgets.QHBoxLayout()
        hl2.addWidget(self.txtInactive, 1)
        hl2.addWidget(self.btnPickInactive)

        form.addRow("Match type:", self.cboMatch)
        form.addRow("Parameter (contains):", self.txtContains)
        form.addRow("Active color (#RRGGBB/default/none):", QtWidgets.QWidget())
        form.itemAt(form.rowCount()-1, QtWidgets.QFormLayout.FieldRole).widget().setLayout(hl1)
        form.addRow("Inactive color (#RRGGBB/default/none):", QtWidgets.QWidget())
        form.itemAt(form.rowCount()-1, QtWidgets.QFormLayout.FieldRole).widget().setLayout(hl2)
        form.addRow("Animation:", self.cboAnimType)
        form.addRow("Speed:", self.dblAnimSpeed)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        vbox = QtWidgets.QVBoxLayout(self)
        vbox.addLayout(form)
        vbox.addWidget(btns)

    def _pick_color(self, line_edit: QtWidgets.QLineEdit):
        """I open a QColorDialog and push the chosen #RRGGBB back to the field."""
        s = line_edit.text().strip()
        if s.lower() in ("default", "none"):
            base = QtGui.QColor("#000000")
        else:
            base = QtGui.QColor(s if s else "#000000")
        col = QtWidgets.QColorDialog.getColor(base, self, "Pick color (#RRGGBB)")
        if col.isValid():
            line_edit.setText(col.name().upper())

    def get_rule(self) -> dict:
        """I return the edited rule, normalized."""
        r = {
            "match": self.cboMatch.currentText(),
            "contains": self.txtContains.text().strip(),
            "active_border_color": self.txtActive.text().strip(),
            "inactive_border_color": self.txtInactive.text().strip(),
            "animation": {
                "type": self.cboAnimType.currentText(),
                "speed": float(self.dblAnimSpeed.value())
            }
        }
        return r

class MainWindow(QtWidgets.QMainWindow):
    """I host the entire UI and forward actions to the controller."""
    def __init__(self, controller: AppController):
        super().__init__()
        self.ctrl = controller

        # Single icon instance for reuse.
        self._app_icon = QtGui.QIcon("icon.ico")
        self.setWindowIcon(self._app_icon)
        self.setWindowTitle("Animated Windows Borders")
        self.resize(980, 680)

        QtWidgets.QApplication.setApplicationName("Animated Windows Borders")
        QtWidgets.QApplication.setApplicationDisplayName("Animated Windows Borders")
        QtWidgets.QApplication.setOrganizationName("Rafael Neves")

        self._build_tray()
        self._build_ui()

        # Wire controller signals
        self.ctrl.status_changed.connect(self._set_status)
        self.ctrl.rules_changed.connect(self._update_rules_list)
        self.ctrl.service_toggled.connect(self._on_service_toggled)

        # Initial UI sync
        self._set_status("Status: running" if self.ctrl.config_data.get("service_enabled") else "Status: stopped")
        self._update_rules_list(self.ctrl.rules_text())
        if self.ctrl.config_data.get("service_enabled"):
            QtCore.QTimer.singleShot(0, self._minimize_to_tray)

    # ---------- UI construction ----------
    def _build_ui(self):
        tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(tabs)

        # Service tab
        self.chkEnable = QtWidgets.QCheckBox("Enable service (apply colors periodically)")
        self.chkEnable.setChecked(bool(self.ctrl.config_data.get("service_enabled", False)))
        self.chkEnable.toggled.connect(self.ctrl.toggle_service)

        self.btnReload = QtWidgets.QPushButton("Reload config.json")
        self.btnSave = QtWidgets.QPushButton("Save config.json")
        self.btnReload.clicked.connect(self.ctrl.reload_config)
        self.btnSave.clicked.connect(self._save_config)

        hlTopBtns = QtWidgets.QHBoxLayout()
        hlTopBtns.addWidget(self.btnReload)
        hlTopBtns.addWidget(self.btnSave)
        hlTopBtns.addStretch(1)

        self.lstRules = QtWidgets.QListWidget()
        self.lstRules.itemDoubleClicked.connect(self._edit_rule_from_item)

        self.btnAdd = QtWidgets.QPushButton("Add")
        self.btnEdit = QtWidgets.QPushButton("Edit")
        self.btnRemove = QtWidgets.QPushButton("Remove")
        self.btnDuplicate = QtWidgets.QPushButton("Duplicate")
        self.btnAbout = QtWidgets.QPushButton("About")
        self.btnAbout.setFlat(True)
        self.btnAbout.setToolTip("About the app")
        self.btnAbout.clicked.connect(self._show_about)

        hlRuleBtns = QtWidgets.QHBoxLayout()
        hlRuleBtns.addWidget(self.btnAdd)
        hlRuleBtns.addWidget(self.btnEdit)
        hlRuleBtns.addWidget(self.btnRemove)
        hlRuleBtns.addWidget(self.btnDuplicate)
        hlRuleBtns.addWidget(self.btnAbout)
        hlRuleBtns.addStretch(1)

        self.txtFilter = QtWidgets.QLineEdit()
        self.txtFilter.setPlaceholderText("Filter rules…")
        self.txtFilter.setClearButtonEnabled(True)
        self.txtFilter.setFixedHeight(30)
        self.txtFilter.setStyleSheet("""
        QLineEdit {
            border: 1px solid #666;
            border-radius: 12px;
            padding: 4px 10px;
        }
        QLineEdit:focus {
            border: 1px solid #999;
        }
        """)
        self.txtFilter.textChanged.connect(self._apply_filter)

        grpRules = QtWidgets.QGroupBox("Rules")
        vRules = QtWidgets.QVBoxLayout(grpRules)
        vRules.addWidget(self.txtFilter)
        vRules.addWidget(self.lstRules, 1)
        vRules.addLayout(hlRuleBtns)

        service_layout = QtWidgets.QVBoxLayout()
        service_layout.addWidget(self.chkEnable)
        service_layout.addLayout(hlTopBtns)
        grpRules.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        service_layout.addWidget(grpRules, 1)

        service_page = QtWidgets.QWidget()
        service_page.setLayout(service_layout)
        tabs.addTab(service_page, "Service")

        # Assistant tab (Process-only)
        self.txtManual = QtWidgets.QLineEdit()
        self.txtManual.setPlaceholderText("Type a process name (ex: Code.exe)")

        self.lstProcs = QtWidgets.QListWidget()
        self.lstWins  = QtWidgets.QListWidget()

        self.btnRefreshLists = QtWidgets.QPushButton("Refresh lists")
        self.btnCreateFromProc = QtWidgets.QPushButton("Create rule from selected process")
        self.btnCreateFromWin  = QtWidgets.QPushButton("Create rule from selected window")

        gridAssist = QtWidgets.QGridLayout()
        gridAssist.addWidget(self.btnRefreshLists, 0, 0, 1, 2)
        gridAssist.addWidget(QtWidgets.QLabel("Active processes:"), 1, 0)
        gridAssist.addWidget(QtWidgets.QLabel("Open windows:"), 1, 1)
        gridAssist.addWidget(self.lstProcs, 2, 0)
        gridAssist.addWidget(self.lstWins,  2, 1)
        gridAssist.addWidget(QtWidgets.QLabel("Manual input (Process):"), 3, 0, 1, 2)
        gridAssist.addWidget(self.txtManual, 4, 0, 1, 2)
        gridAssist.addWidget(self.btnCreateFromProc, 5, 0, 1, 2)
        gridAssist.addWidget(self.btnCreateFromWin,  6, 0, 1, 2)

        assist_page = QtWidgets.QWidget()
        assist_page.setLayout(gridAssist)
        tabs.addTab(assist_page, "Assistant")

        # Connections
        self.btnAdd.clicked.connect(self._add_rule)
        self.btnEdit.clicked.connect(self._edit_rule)
        self.btnRemove.clicked.connect(self._remove_rule)
        self.btnDuplicate.clicked.connect(self._duplicate_rule)

        self.btnRefreshLists.clicked.connect(self._refresh_lists)
        self.btnCreateFromProc.clicked.connect(self._create_rule_from_selected_process)
        self.btnCreateFromWin.clicked.connect(self._create_rule_from_selected_window)

        self.lstProcs.itemDoubleClicked.connect(lambda _item: self._create_rule_from_selected_process())
        self.lstWins.itemDoubleClicked.connect(lambda _item: self._create_rule_from_selected_window())

    # ---------- Tray ----------
    def _build_tray(self):
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        icon = self._app_icon
        self.tray = QtWidgets.QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Animated Windows Borders")
        menu = QtWidgets.QMenu()

        act_restore = menu.addAction("Restore/Show")
        act_restore.triggered.connect(self._restore_from_tray)

        self.act_tray_enable = menu.addAction("Enable service")
        self.act_tray_enable.setCheckable(True)
        self.act_tray_enable.setChecked(False)
        self.act_tray_enable.triggered.connect(self.ctrl.toggle_service)

        menu.addSeparator()
        act_about = menu.addAction("About…")
        act_about.triggered.connect(self._show_about)

        act_exit = menu.addAction("Exit")
        act_exit.triggered.connect(QtWidgets.QApplication.instance().quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason == QtWidgets.QSystemTrayIcon.DoubleClick:
            self._restore_from_tray()

    def _minimize_to_tray(self):
        if self.tray:
            self.hide()
            self.tray.showMessage(
                "Animated Windows Borders",
                "Running in background.",
                QtWidgets.QSystemTrayIcon.Information,
                2000
            )

    def _restore_from_tray(self):
        self.showNormal()
        self.activateWindow()

    def changeEvent(self, event: QtCore.QEvent):
        if event.type() == QtCore.QEvent.WindowStateChange:
            if self.isMinimized():
                QtCore.QTimer.singleShot(0, self._minimize_to_tray)
        super().changeEvent(event)

    # ---------- Status ----------
    def _set_status(self, msg: str):
        self.statusBar().showMessage(msg)

    def _on_service_toggled(self, enabled: bool):
        # Sync UI affordances when controller toggles programmatically
        self.chkEnable.blockSignals(True)
        self.chkEnable.setChecked(enabled)
        self.chkEnable.blockSignals(False)
        if hasattr(self, "act_tray_enable") and self.act_tray_enable:
            self.act_tray_enable.blockSignals(True)
            self.act_tray_enable.setChecked(enabled)
            self.act_tray_enable.blockSignals(False)

    # ---------- Rules UI ----------
    def _update_rules_list(self, rows: list):
        self.lstRules.clear()
        self.lstRules.addItems(rows)
        self._apply_filter()

    def _current_rule_index(self) -> int:
        return self.lstRules.currentRow()

    def _add_rule(self):
        dlg = RuleEditorDialog(self, is_global=False)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            r = dlg.get_rule()
            self.ctrl.add_rule(r)

    def _edit_rule_from_item(self, _item: QtWidgets.QListWidgetItem):
        self._edit_rule()

    def _edit_rule(self):
        idx = self._current_rule_index()
        if idx < 0:
            return

        current = self.ctrl.get_rule(idx)
        if not current:
            return

        is_global = (current.get("match", "").lower() == "global")

        # Agora o diálogo recebe a regra atual e pré-carrega 'contains' e cores
        dlg = RuleEditorDialog(self, rule=current, is_global=is_global)

        if dlg.exec() == QtWidgets.QDialog.Accepted:
            newr = dlg.get_rule()
            if is_global:
                # Força sem 'contains' para Global
                newr["match"] = "Global"
                newr["contains"] = ""
            else:
                newr["match"] = "Process"
            self.ctrl.edit_rule(idx, newr)

    def _remove_rule(self):
        idx = self._current_rule_index()
        if idx < 0:
            return
        msg = self.ctrl.remove_rule(idx)
        if msg:
            QtWidgets.QMessageBox.warning(self, "Warning", msg)

    def _duplicate_rule(self):
        idx = self._current_rule_index()
        if idx < 0:
            return
        msg = self.ctrl.duplicate_rule(idx)
        if msg:
            QtWidgets.QMessageBox.information(self, "Duplicate", msg)

    # ---------- Assistant ----------
    def _refresh_lists(self):
        procs, wins = self.ctrl.refresh_lists()
        self.lstProcs.setUpdatesEnabled(False)
        self.lstProcs.blockSignals(True)
        self.lstProcs.clear()
        self.lstProcs.addItems(procs)
        self.lstProcs.blockSignals(False)
        self.lstProcs.setUpdatesEnabled(True)

        self.lstWins.setUpdatesEnabled(False)
        self.lstWins.blockSignals(True)
        self.lstWins.clear()
        self.lstWins.addItems(wins)
        self.lstWins.blockSignals(False)
        self.lstWins.setUpdatesEnabled(True)

    def _create_rule_from_selected_process(self):
        exe = self.txtManual.text().strip()
        if not exe and self.lstProcs.currentItem():
            exe = self.lstProcs.currentItem().text().strip()
        msg = self.ctrl.add_or_warn_process_rule(exe)
        if msg:
            QtWidgets.QMessageBox.information(self, "Existing rule", msg)

    def _create_rule_from_selected_window(self):
        if not self.lstWins.currentItem():
            return
        parts = self.lstWins.currentItem().text().split("|")
        if len(parts) >= 3:
            exe = parts[2].strip()
            msg = self.ctrl.add_or_warn_process_rule(exe)
            if msg:
                QtWidgets.QMessageBox.information(self, "Existing rule", msg)

    # ---------- About ----------
    def _show_about(self):
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("About - Animated Windows Borders")


        msg.setIconPixmap(QtGui.QPixmap("icon.ico").scaled(64, 64, QtCore.Qt.KeepAspectRatio))

        msg.setTextFormat(QtCore.Qt.RichText)
        msg.setTextInteractionFlags(
            QtCore.Qt.TextBrowserInteraction | QtCore.Qt.LinksAccessibleByMouse
        )

        msg.setText(
            """<b>Animated Windows Borders</b><br>
            Version 1.0.0<br>
            Developer: Rafael<br>
            Website: <a href='https://rafaelneves.dev.br'>rafaelneves.dev.br</a>"""
        )

        msg.exec_()

    def _apply_filter(self):
        needle = (self.txtFilter.text() or "").strip().lower()
        for i in range(self.lstRules.count()):
            item = self.lstRules.item(i)
            text = (item.text() or "").lower()
            item.setHidden(bool(needle) and needle not in text)

    def _save_config(self):
        self.ctrl.save_config()
        QtWidgets.QMessageBox.information(self, "Save", "Configuration saved to config.json.")

    def closeEvent(self, event: QtGui.QCloseEvent):
        """
        Ao fechar pela janela, não sair: esconder e manter no tray.
        Se não houver tray disponível, segue o fechamento normal.
        """
        if getattr(self, "tray", None):
            event.ignore()
            self._minimize_to_tray()
        else:
            super().closeEvent(event)

