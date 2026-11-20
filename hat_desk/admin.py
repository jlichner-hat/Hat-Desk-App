"""HAT Desk Admin: see every desk, move groups of desks, and set clock schedules.

    python -m hat_desk.admin
"""
from __future__ import annotations

import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
                               QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTimeEdit,
                               QVBoxLayout, QWidget)

from .admin_client import AdminSettings, DeskClient, DeskEntry, DeskError, discover
from .settings import DAY_NAMES, ScheduleRule
from .ui import theme
from .ui.settings_dialog import _days_text

ACTION_LABELS = [
    ("max", "Raise to highest (remember height)"),
    ("restore", "Return to remembered height"),
    ("min", "Lower to lowest (remember height)"),
    ("preset:1", "Go to preset 1"),
    ("preset:2", "Go to preset 2"),
    ("preset:3", "Go to preset 3"),
]


def default_schedules():
    return [
        ScheduleRule(id=uuid.uuid4().hex[:8], name="Cleaning", time="17:30", days=[0, 1, 2, 3, 4], action="max"),
        ScheduleRule(id=uuid.uuid4().hex[:8], name="Morning", time="06:30", days=[0, 1, 2, 3, 4], action="restore"),
    ]


def fmt_time(t: str) -> str:
    h, m = (int(x) for x in t.split(":"))
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


class RuleDialog(QDialog):
    def __init__(self, rule: ScheduleRule, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scheduled move")
        self.rule = rule
        form = QFormLayout(self)
        form.setContentsMargins(18, 18, 18, 18)
        form.setVerticalSpacing(10)
        self.name = QLineEdit(rule.name)
        self.name.setPlaceholderText("e.g. Cleaning")
        form.addRow("Name", self.name)
        self.time = QTimeEdit(QTime.fromString(rule.time, "HH:mm"))
        self.time.setDisplayFormat("h:mm AP")
        form.addRow("Time", self.time)
        days = QHBoxLayout()
        days.setSpacing(14)
        self.days = []
        for i, d in enumerate(DAY_NAMES):
            c = QCheckBox(d)
            c.setChecked(i in rule.days)
            days.addWidget(c)
            self.days.append(c)
        form.addRow("Days", days)
        self.action = QComboBox()
        for key, label in ACTION_LABELS:
            self.action.addItem(label, key)
        idx = max(0, self.action.findData(rule.action))
        self.action.setCurrentIndex(idx)
        form.addRow("Move", self.action)
        self.warn = QSpinBox(suffix=" s", maximum=600, value=rule.warn_seconds)
        form.addRow("Notice first", self.warn)
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(rule.enabled)
        form.addRow("", self.enabled)
        form.addRow(QLabel("Each desk runs this on its own clock, so it still happens when this "
                           "PC is off. People at their desk get a notice with a Skip button.",
                           objectName="Hint", wordWrap=True))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def result_rule(self) -> ScheduleRule:
        return ScheduleRule(id=self.rule.id, name=self.name.text().strip(),
                            time=self.time.time().toString("HH:mm"),
                            days=[i for i, c in enumerate(self.days) if c.isChecked()],
                            action=self.action.currentData(), enabled=self.enabled.isChecked(),
                            warn_seconds=self.warn.value())


class Bridge(QObject):
    status = Signal(int, object)          # row, dict or error string
    results = Signal(str, object)         # title, list of (desk name, result)


class AdminWindow(QMainWindow):
    def __init__(self, settings: AdminSettings | None = None, settings_path: str | None = None):
        super().__init__()
        self.cfg = settings or AdminSettings.load(settings_path)
        self.cfg_path = settings_path
        if not self.cfg.schedules:
            self.cfg.schedules = default_schedules()
        self.pool = ThreadPoolExecutor(max_workers=16)
        self.bridge = Bridge()
        self.bridge.status.connect(self._show_status)
        self.bridge.results.connect(self._show_results)
        self.setWindowTitle("HAT Desk Admin")
        self.setWindowIcon(theme.app_icon())
        self.resize(980, 600)

        root = QWidget(objectName="Window")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(20, 16, 20, 16)

        # header
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(theme.pixmap("hat_logo_trans.png", height=40, dpr=self.devicePixelRatioF()))
        head.addWidget(logo)
        head.addSpacing(12)
        title = QLabel("Desk Admin")
        title.setStyleSheet("font-size: 14pt; font-weight: 300;")
        head.addWidget(title)
        head.addStretch()
        key_btn = QPushButton("Admin key...")
        key_btn.clicked.connect(self._set_key)
        head.addWidget(key_btn)
        lay.addLayout(head)
        lay.addSpacing(6)

        split = QSplitter()
        lay.addWidget(split, 1)

        # ---- desks ----
        left = QGroupBox("Desks")
        ll = QVBoxLayout(left)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Desk", "Height", "Status", "Address"])
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._check_changed)
        ll.addWidget(self.table)
        row = QHBoxLayout()
        for text, fn in (("Find desks", self._discover), ("Add...", self._add), ("Remove", self._remove)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        self.all_box = QCheckBox("Select all")
        self.all_box.toggled.connect(self._select_all)
        row.addWidget(self.all_box)
        ll.addLayout(row)
        split.addWidget(left)

        # ---- right side ----
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        move = QGroupBox("Move checked desks now")
        ml = QHBoxLayout(move)
        for text, action in (("Highest", "max"), ("Return", "restore"), ("Lowest", "min"),
                             ("P1", "preset:1"), ("P2", "preset:2"), ("P3", "preset:3")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, a=action: self._move(a))
            ml.addWidget(b)
        stop = QPushButton("Stop", objectName="Primary")
        stop.clicked.connect(self._stop)
        ml.addWidget(stop)
        rl.addWidget(move)

        sched = QGroupBox("Schedule")
        sl = QVBoxLayout(sched)
        self.rules = QTableWidget(0, 4)
        self.rules.setHorizontalHeaderLabels(["Time", "Days", "Name", "Move"])
        self.rules.verticalHeader().hide()
        self.rules.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rules.setSelectionMode(QAbstractItemView.SingleSelection)
        self.rules.setEditTriggers(QAbstractItemView.NoEditTriggers)
        rh = self.rules.horizontalHeader()
        for c in (0, 1, 2):
            rh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        rh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.rules.doubleClicked.connect(self._edit_rule)
        sl.addWidget(self.rules)
        srow = QHBoxLayout()
        for text, fn in (("Add", self._add_rule), ("Edit", self._edit_rule), ("Delete", self._del_rule)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            srow.addWidget(b)
        srow.addStretch()
        push = QPushButton("Send to checked desks", objectName="Primary")
        push.clicked.connect(self._push)
        srow.addWidget(push)
        sl.addLayout(srow)
        rl.addWidget(sched, 1)
        self.result_lbl = QLabel(objectName="Hint", wordWrap=True)
        rl.addWidget(self.result_lbl)
        split.addWidget(right)
        split.setSizes([500, 480])

        self._fill_desks()
        self._fill_rules()
        self.timer = QTimer(self, interval=3000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        QTimer.singleShot(100, self.poll)
        if not self.cfg.admin_key:
            QTimer.singleShot(300, self._set_key)

    # ---- persistence -----------------------------------------------------------
    def _save(self):
        self.cfg.save(self.cfg_path)

    def _set_key(self):
        key, ok = QInputDialog.getText(self, "Admin key",
                                       "Office admin key (the same key set on each desk at install):",
                                       QLineEdit.Password, self.cfg.admin_key)
        if ok:
            self.cfg.admin_key = key.strip()
            self._save()
            self.poll()

    # ---- desks -------------------------------------------------------------------
    def _fill_desks(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.cfg.desks))
        for i, d in enumerate(self.cfg.desks):
            it = QTableWidgetItem(d.name)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if d.selected else Qt.Unchecked)
            self.table.setItem(i, 0, it)
            for j in (1, 2):
                self.table.setItem(i, j, QTableWidgetItem("…"))
            self.table.setItem(i, 3, QTableWidgetItem(d.address))
        self.table.blockSignals(False)

    def _check_changed(self, item):
        if item.column() == 0 and item.row() < len(self.cfg.desks):
            self.cfg.desks[item.row()].selected = item.checkState() == Qt.Checked
            self._save()

    def _select_all(self, on):
        for d in self.cfg.desks:
            d.selected = on
        self._fill_desks()
        self._save()

    def _discover(self):
        self.result_lbl.setText("Looking for desks on the network...")
        QApplication.processEvents()
        found = discover()
        known = {(d.host, d.port) for d in self.cfg.desks}
        new = [d for d in found if (d.host, d.port) not in known]
        self.cfg.desks.extend(new)
        self.cfg.desks.sort(key=lambda d: d.name.lower())
        self._save()
        self._fill_desks()
        self.poll()
        self.result_lbl.setText(f"Found {len(found)} desk(s), {len(new)} new.")

    def _add(self):
        text, ok = QInputDialog.getText(self, "Add desk", "Desk PC name or IP address (optionally :port):")
        if not ok or not text.strip():
            return
        host, _, port = text.strip().partition(":")
        self.cfg.desks.append(DeskEntry(name=host, host=host, port=int(port or 47820)))
        self._save()
        self._fill_desks()
        self.poll()

    def _remove(self):
        names = [d.name for d in self.cfg.desks if d.selected]
        if not names:
            return
        if QMessageBox.question(self, "Remove desks", f"Remove {len(names)} checked desk(s) from this list?") \
                != QMessageBox.Yes:
            return
        self.cfg.desks = [d for d in self.cfg.desks if not d.selected]
        self._save()
        self._fill_desks()

    def _client(self, d):
        return DeskClient(d, self.cfg.admin_key)

    def poll(self):
        for i, d in enumerate(list(self.cfg.desks)):
            def job(i=i, d=d):
                try:
                    self.bridge.status.emit(i, self._client(d).status())
                except DeskError as e:
                    self.bridge.status.emit(i, str(e))
            self.pool.submit(job)

    def _show_status(self, row, st):
        if row >= self.table.rowCount():
            return
        if isinstance(st, str):
            self.table.item(row, 1).setText("--")
            self.table.item(row, 2).setText(st.capitalize())
            self.table.item(row, 2).setForeground(QColor(theme.WARN))
            return
        h = st.get("height_mm")
        self.table.item(row, 1).setText("--" if h is None else f"{h / 10:.1f} cm")
        if not st.get("online"):
            text = st.get("message") or "Leg not connected"
        elif st.get("moving"):
            text = st.get("message") or "Moving"
        else:
            text = st.get("message") or "Ready"
        if st.get("simulated"):
            text += " (sim)"
        self.table.item(row, 2).setText(text)
        self.table.item(row, 2).setForeground(QColor(theme.INK) if st.get("online") else QColor(theme.WARN))
        name = st.get("name")
        if name and row < len(self.cfg.desks) and self.cfg.desks[row].name != name:
            self.cfg.desks[row].name = name
            self.table.blockSignals(True)
            self.table.item(row, 0).setText(name)
            self.table.blockSignals(False)
            self._save()

    # ---- group actions -------------------------------------------------------------
    def _checked(self):
        return [d for d in self.cfg.desks if d.selected]

    def _fan_out(self, title, fn):
        desks = self._checked()
        if not desks:
            self.result_lbl.setText("Check at least one desk first.")
            return

        def run():
            futs = [(d, self.pool.submit(fn, d)) for d in desks]
            out = []
            for d, f in futs:
                try:
                    out.append((d.name, f.result()))
                except DeskError as e:
                    out.append((d.name, str(e)))
            self.bridge.results.emit(title, out)
        self.pool.submit(run)
        self.result_lbl.setText(f"{title}: sending to {len(desks)} desk(s)...")

    def _move(self, action):
        self._fan_out(f"Move ({action})", lambda d: self._client(d).action(action, label="admin"))

    def _stop(self):
        self._fan_out("Stop", lambda d: self._client(d).stop())

    def _push(self):
        rules = list(self.cfg.schedules)
        self._fan_out("Schedule", lambda d: f"{self._client(d).put_schedules(rules, self.cfg.console_name)['count']} rule(s) saved")

    def _show_results(self, title, results):
        ok = sum(1 for _, r in results if r in ("started", "stopped", "reached") or "saved" in str(r))
        lines = ", ".join(f"{n}: {r}" for n, r in results[:8])
        more = f" (+{len(results) - 8} more)" if len(results) > 8 else ""
        self.result_lbl.setText(f"{title}: {ok}/{len(results)} OK. {lines}{more}")
        self.poll()

    # ---- schedule ------------------------------------------------------------------
    def _fill_rules(self):
        self.rules.setRowCount(len(self.cfg.schedules))
        for i, r in enumerate(self.cfg.schedules):
            label = dict(ACTION_LABELS).get(r.action, r.action)
            for j, v in enumerate((fmt_time(r.time), _days_text(r.days), r.name, label)):
                it = QTableWidgetItem(v)
                if not r.enabled:
                    it.setForeground(Qt.gray)
                self.rules.setItem(i, j, it)

    def _add_rule(self):
        dlg = RuleDialog(ScheduleRule(id=uuid.uuid4().hex[:8], name="", time="17:30"), self)
        if dlg.exec():
            self.cfg.schedules.append(dlg.result_rule())
            self._save()
            self._fill_rules()

    def _edit_rule(self, *_):
        row = self.rules.currentRow()
        if row < 0:
            return
        dlg = RuleDialog(self.cfg.schedules[row], self)
        if dlg.exec():
            self.cfg.schedules[row] = dlg.result_rule()
            self._save()
            self._fill_rules()

    def _del_rule(self):
        row = self.rules.currentRow()
        if row >= 0:
            del self.cfg.schedules[row]
            self._save()
            self._fill_rules()

    def closeEvent(self, e):
        self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(e)


def main(argv=None):
    app = QApplication(sys.argv[:1] if argv is None else argv)
    app.setApplicationName("HAT Desk Admin")
    app.setFont(theme.base_font())
    app.setStyleSheet(theme.STYLE)
    w = AdminWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
