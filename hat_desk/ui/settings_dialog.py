"""Desk settings: presets, low limit, units, calibration; schedule (read-only); about."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
                               QHeaderView, QAbstractItemView)

from . import theme
from ..settings import DAY_NAMES


def _days_text(days):
    days = sorted(days)
    if days == [0, 1, 2, 3, 4]:
        return "Mon–Fri"
    if days == list(range(7)):
        return "Every day"
    return ", ".join(DAY_NAMES[d] for d in days)


class HeightSpin(QDoubleSpinBox):
    """Edits a desk height stored in mm, shown in the user's units."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.inches = settings.units == "in"
        self.setDecimals(1)
        self.setSuffix(" in" if self.inches else " cm")
        lo, hi = settings.min_height, settings.max_height
        self.setRange(self._from_mm(lo), self._from_mm(hi))
        self.setSingleStep(0.5)

    def _from_mm(self, mm):
        return mm / 25.4 if self.inches else mm / 10

    def set_mm(self, mm):
        self.setValue(self._from_mm(mm))

    def mm(self):
        return self.value() * (25.4 if self.inches else 10)


class SettingsDialog(QDialog):
    def __init__(self, controller, scheduler, parent=None):
        super().__init__(parent)
        self.ctl, self.sched, self.store = controller, scheduler, controller.store
        s = self.store.settings
        self.setWindowTitle("HAT Desk settings")
        self.setWindowIcon(theme.app_icon())
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setSizeConstraint(QLayout.SetMinimumSize)
        tabs = QTabWidget()
        lay.addWidget(tabs)
        tabs.addTab(self._desk_tab(s), "My desk")
        tabs.addTab(self._schedule_tab(s), "Schedule")
        tabs.addTab(self._about_tab(), "About")
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setObjectName("Primary")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    # ---- tabs ----------------------------------------------------------------
    def _use_current(self, spin):
        b = QPushButton("Use current")
        b.clicked.connect(lambda: self.ctl.height is not None and spin.set_mm(self.ctl.height))
        return b

    def _desk_tab(self, s):
        """One grid for the whole tab: [check] [label/name] [value, stretches] [button]."""
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(16, 16, 16, 16)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(8)
        g.setColumnStretch(2, 1)
        r = 0

        def section(text):
            nonlocal r
            if r:
                g.setRowMinimumHeight(r, 6)
                r += 1
            g.addWidget(QLabel(text, objectName="Section"), r, 0, 1, 4)
            r += 1

        def hint(text):
            nonlocal r
            g.addWidget(QLabel(text, objectName="Hint", wordWrap=True), r, 0, 1, 4)
            r += 1

        self.name = QLineEdit(s.desk_name)
        g.addWidget(QLabel("Desk name"), r, 0, 1, 2)
        g.addWidget(self.name, r, 2, 1, 2)
        r += 1
        self.units = QComboBox()
        self.units.addItems(["Centimetres", "Inches"])
        self.units.setCurrentIndex(1 if s.units == "in" else 0)
        g.addWidget(QLabel("Units"), r, 0, 1, 2)
        g.addWidget(self.units, r, 2, 1, 2)
        r += 1

        section("PRESETS")
        self.preset_rows = []
        for i, p in enumerate(s.presets):
            on = QCheckBox()
            on.setChecked(p.height_mm is not None)
            on.setToolTip("Preset is programmed")
            name = QLineEdit(p.name)
            name.setMaxLength(12)
            name.setFixedWidth(96)
            h = HeightSpin(s)
            h.set_mm(p.height_mm if p.height_mm is not None else (self.ctl.height or s.min_height))
            h.setEnabled(on.isChecked())
            on.toggled.connect(h.setEnabled)
            g.addWidget(on, r, 0)
            g.addWidget(name, r, 1)
            g.addWidget(h, r, 2)
            g.addWidget(self._use_current(h), r, 3)
            self.preset_rows.append((on, name, h))
            r += 1
        hint("Tip: in the desk panel, hold a preset button to save the current height to it.")

        section("LIMITS")
        self.low_on = QCheckBox()
        self.low_on.setChecked(s.low_limit_mm is not None)
        self.low = HeightSpin(s)
        self.low.set_mm(s.low_limit_mm if s.low_limit_mm is not None else s.min_height)
        self.low.setEnabled(self.low_on.isChecked())
        self.low_on.toggled.connect(self.low.setEnabled)
        g.addWidget(self.low_on, r, 0)
        g.addWidget(QLabel("Lowest"), r, 1)
        g.addWidget(self.low, r, 2)
        g.addWidget(self._use_current(self.low), r, 3)
        r += 1
        hint("Protects a chair, drawer unit or knees under the desk. "
             "Up/down, presets and schedules all stop here.")
        g.addWidget(QLabel("Speed"), r, 0, 1, 2)
        g.addWidget(QLabel("Fixed by the LINAK control box (about 38 mm/s)", objectName="Hint"), r, 2, 1, 2)
        r += 1

        section("CALIBRATION")
        self.cal = HeightSpin(s)
        self.cal.setRange(self.cal._from_mm(300), self.cal._from_mm(1600))
        self.cal.set_mm(self.ctl.height if self.ctl.height is not None else s.min_height)
        self.cal.setEnabled(self.ctl.height is not None)
        g.addWidget(QLabel("Height now"), r, 0, 1, 2)
        g.addWidget(self.cal, r, 2, 1, 2)
        r += 1
        hint("Measure floor to desktop with a tape and enter it, so the readout matches your desk.")

        if sys.platform == "win32":
            self.autostart = QCheckBox("Start with Windows")
            self.autostart.setChecked(s.start_with_windows)
            g.addWidget(self.autostart, r, 0, 1, 4)
            r += 1
        else:
            self.autostart = None
        g.setRowStretch(r, 1)
        return w

    def _schedule_tab(self, s):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16)
        who = s.managed_by or "your administrator"
        lay.addWidget(QLabel(f"Set by {who}. These moves run on this PC's clock, "
                             "with a notice and a Skip button first.", objectName="Hint", wordWrap=True))
        t = QTableWidget(len(s.schedules), 3)
        t.setHorizontalHeaderLabels(["Time", "Days", "Move"])
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.NoSelection)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        t.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        t.setWordWrap(False)
        t.setTextElideMode(Qt.ElideRight)
        for i, r in enumerate(s.schedules):
            hh, mm = (int(x) for x in r.time.split(":"))
            t12 = f"{(hh % 12) or 12}:{mm:02d} {'AM' if hh < 12 else 'PM'}"
            for j, v in enumerate([t12, _days_text(r.days), (r.name + ": " if r.name else "") + r.describe_action()]):
                it = QTableWidgetItem(v)
                if not r.enabled:
                    it.setForeground(Qt.gray)
                t.setItem(i, j, it)
        lay.addWidget(t)
        if not s.schedules:
            lay.addWidget(QLabel("No scheduled moves.", objectName="Hint"))
        if s.remembered_height_mm is not None:
            lay.addWidget(QLabel(f"Will return to {s.fmt(s.remembered_height_mm)} on the next "
                                 "“return” move.", objectName="Hint"))
        return w

    def _about_tab(self):
        from .. import __version__
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 24, 24, 24)
        logo = QLabel()
        logo.setPixmap(theme.pixmap("hat_logo_trans.png", width=220, dpr=self.devicePixelRatioF()))
        logo.setAlignment(Qt.AlignCenter)
        lay.addWidget(logo)
        lay.addSpacing(10)
        mode = " (simulated desk)" if self.ctl.simulate else ""
        info = QLabel(f"HAT Desk {__version__}{mode}\nLINAK DL6 · CBD6S · USB2LIN06", objectName="Hint")
        info.setAlignment(Qt.AlignCenter)
        lay.addWidget(info)
        lay.addStretch()
        return w

    # ---- save ------------------------------------------------------------------
    def _save(self):
        ctl_h = self.ctl.height
        leg_mm = None if ctl_h is None else self.store.settings.to_leg_mm(ctl_h)

        def upd(s):
            s.desk_name = self.name.text().strip() or s.desk_name
            # Calibration shifts the readout. Shift every stored height by the same amount so
            # presets and limits keep pointing at the same physical leg position.
            delta = 0.0
            if self.cal.isEnabled() and leg_mm is not None and abs(self.cal.mm() - ctl_h) > 0.5:
                new_offset = round(self.cal.mm() - leg_mm, 1)
                delta = new_offset - s.height_offset_mm
                s.height_offset_mm = new_offset
                if s.remembered_height_mm is not None:
                    s.remembered_height_mm = round(s.remembered_height_mm + delta, 1)
                if s.high_limit_mm is not None:
                    s.high_limit_mm = round(s.high_limit_mm + delta, 1)
            for p, (on, name, h) in zip(s.presets, self.preset_rows):
                p.name = name.text().strip() or p.name
                p.height_mm = round(h.mm() + delta, 1) if on.isChecked() else None
            s.low_limit_mm = round(self.low.mm() + delta, 1) if self.low_on.isChecked() else None
            s.units = "in" if self.units.currentIndex() == 1 else "cm"
            if self.autostart is not None:
                s.start_with_windows = self.autostart.isChecked()
        self.store.update(upd)
        if self.autostart is not None:
            from ..autostart import set_autostart
            set_autostart(self.store.settings.start_with_windows)
        self.accept()
