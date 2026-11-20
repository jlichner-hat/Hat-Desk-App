"""The small desk panel that opens from the tray icon."""
from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QElapsedTimer, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QInputDialog, QLabel,
                               QMenu, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from . import theme

LONG_PRESS_MS = 1200


class HoldButton(QPushButton):
    """Moves the desk while held: calls hold(dir) every 100 ms, hold(0) on release."""

    def __init__(self, text, direction, controller, parent=None):
        super().__init__(text, parent)
        self.setObjectName("Move")
        self.direction, self.ctl = direction, controller
        self._timer = QTimer(self, interval=100)
        self._timer.timeout.connect(lambda: self.ctl.hold(self.direction))
        self.pressed.connect(self._start)
        self.released.connect(self._end)
        self.setFocusPolicy(Qt.NoFocus)

    def _start(self):
        self.ctl.hold(self.direction)
        self._timer.start()

    def _end(self):
        self._timer.stop()
        self.ctl.hold(0)

    def hideEvent(self, e):
        self._end()
        super().hideEvent(e)


class PresetButton(QPushButton):
    """Click: go to the preset. Hold 1.2 s (or right-click > Save): store the current height."""
    go = Signal(int)
    save = Signal(int)
    rename = Signal(int)

    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.setObjectName("Preset")
        self.index = index
        self._clock = QElapsedTimer()
        self._long = QTimer(self, singleShot=True, interval=LONG_PRESS_MS)
        self._long.timeout.connect(self._long_press)
        self._saved = False
        self.setFocusPolicy(Qt.NoFocus)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._saved = False
            self._long.start()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._long.stop()
            if not self._saved and self.rect().contains(e.position().toPoint()):
                self.go.emit(self.index)
        self.setDown(False)

    def _long_press(self):
        self._saved = True
        self.save.emit(self.index)

    def _menu(self, pos):
        m = QMenu(self)
        m.addAction("Save current height here", lambda: self.save.emit(self.index))
        m.addAction("Rename...", lambda: self.rename.emit(self.index))
        m.exec(self.mapToGlobal(pos))


class DeskPanel(QWidget):
    """Frameless popup: height, up/down, three presets, one status line."""
    open_settings = Signal()
    skip_requested = Signal(str)

    def __init__(self, controller, scheduler, popup: bool = True):
        flags = (Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint) if popup else Qt.Widget
        super().__init__(None, flags)
        self.ctl, self.sched = controller, scheduler
        self.store = controller.store
        self.setAttribute(Qt.WA_TranslucentBackground, popup)
        self.setWindowTitle("HAT Desk")
        self.setWindowIcon(theme.app_icon())
        self._pending_rule = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        card = QWidget(objectName="Panel")
        if popup:
            sh = QGraphicsDropShadowEffect(blurRadius=24, offset=QPoint(0, 4))
            sh.setColor(Qt.gray)
            card.setGraphicsEffect(sh)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)

        # header: mark, desk name, settings
        head = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(theme.pixmap("hat_mark.png", width=20, dpr=self.devicePixelRatioF()))
        head.addWidget(mark)
        self.name = QLabel(objectName="Name")
        head.addWidget(self.name, 1)
        gear = QPushButton("⚙", objectName="Icon", toolTip="Settings")
        gear.setFocusPolicy(Qt.NoFocus)
        gear.clicked.connect(self.open_settings)
        head.addWidget(gear)
        lay.addLayout(head)

        # height readout
        hrow = QHBoxLayout()
        hrow.addStretch()
        self.height_lbl = QLabel("--", objectName="Height")
        self.unit_lbl = QLabel("cm", objectName="Unit")
        hrow.addWidget(self.height_lbl, 0, Qt.AlignBottom)
        hrow.addWidget(self.unit_lbl, 0, Qt.AlignBottom)
        hrow.addStretch()
        lay.addLayout(hrow)
        self.bar = QProgressBar(textVisible=False, maximum=1000)
        lay.addWidget(self.bar)

        # up / down
        moves = QHBoxLayout()
        moves.setSpacing(8)
        self.up = HoldButton("▲", +1, controller)
        self.down = HoldButton("▼", -1, controller)
        self.up.setToolTip("Hold to raise")
        self.down.setToolTip("Hold to lower")
        moves.addWidget(self.up)
        moves.addWidget(self.down)
        lay.addLayout(moves)

        # presets
        prow = QHBoxLayout()
        prow.setSpacing(6)
        self.presets = []
        for i in range(3):
            b = PresetButton(i)
            b.go.connect(self._go_preset)
            b.save.connect(self._save_preset)
            b.rename.connect(self._rename_preset)
            prow.addWidget(b)
            self.presets.append(b)
        lay.addLayout(prow)

        # scheduled-move banner
        self.banner = QFrame(objectName="Banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(10, 6, 8, 6)
        self.banner_lbl = QLabel(wordWrap=True)
        bl.addWidget(self.banner_lbl, 1)
        skip = QPushButton("Skip", objectName="Link")
        skip.setCursor(Qt.PointingHandCursor)
        skip.clicked.connect(self._skip)
        bl.addWidget(skip)
        self.banner.hide()
        lay.addWidget(self.banner)

        self.status = QLabel(objectName="Status", wordWrap=True)
        self.status.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.status)

        self.setFixedWidth(300)
        self._timer = QTimer(self, interval=150)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self._flash_until = None
        self.refresh()

    # ---- actions --------------------------------------------------------------
    def _go_preset(self, i):
        r = self.ctl.go_preset(i)
        if r == "not set":
            self.flash(f"Hold “{self.store.settings.presets[i].name}” to save the current height")
        elif r not in ("started",):
            self.flash(self._result_text(r))

    def _save_preset(self, i):
        if self.ctl.save_preset(i):
            p = self.store.settings.presets[i]
            self.flash(f"Saved {self.store.settings.fmt(p.height_mm)} as “{p.name}”")
        else:
            self.flash("Desk not connected")

    def _rename_preset(self, i):
        cur = self.store.settings.presets[i].name
        name, ok = QInputDialog.getText(self, "Rename preset", "Name:", text=cur)
        if ok and name.strip():
            self.store.update(lambda s: setattr(s.presets[i], "name", name.strip()[:12]))

    def show_warning(self, rule, seconds):
        self._pending_rule = rule
        self.banner_lbl.setText(f"<b>{rule.name or 'Scheduled move'}</b> in {seconds} s: "
                                f"{rule.describe_action().lower()}")
        self.banner.show()
        self.status.hide()
        self.adjustSize()
        QTimer.singleShot(max(1, seconds) * 1000 + 3000, self._clear_warning)

    def _clear_warning(self):
        self._pending_rule = None
        self.banner.hide()
        self.status.show()
        self.adjustSize()

    def _skip(self):
        if self._pending_rule:
            self.skip_requested.emit(self._pending_rule.id)
            self.flash(f"Skipped “{self._pending_rule.name}” for today")
        self._clear_warning()

    def flash(self, text, ms=3500):
        self.status.setText(text)
        self._flash_until = dt.datetime.now() + dt.timedelta(milliseconds=ms)

    @staticmethod
    def _result_text(r):
        return {"offline": "Desk not connected", "resting": "Motor resting, try again in a few minutes",
                "not set": "Preset not set"}.get(r, r)

    # ---- display ----------------------------------------------------------------
    def refresh(self):
        s = self.store.settings
        self.name.setText(s.desk_name)
        h = self.ctl.height
        txt = s.fmt(h, unit=False)
        self.height_lbl.setText(txt)
        self.unit_lbl.setText("in" if s.units == "in" else "cm")
        if h is not None:
            self.bar.setValue(int(1000 * (h - s.min_height) / max(1.0, s.stroke_mm)))
        online = self.ctl.online
        self.up.setEnabled(online)
        self.down.setEnabled(online)
        for b, p in zip(self.presets, s.presets):
            b.setText(p.name)
            b.setProperty("empty", p.height_mm is None)
            b.setToolTip(f"{p.name}: {s.fmt(p.height_mm)}\nClick to go, hold to save current height"
                         if p.height_mm is not None else "Not set. Hold to save the current height here")
            b.setEnabled(online)
            b.style().unpolish(b)
            b.style().polish(b)
        if self._flash_until and dt.datetime.now() < self._flash_until:
            return
        msg = self.ctl.message
        if not msg:
            nxt = self.sched.next_runs(1) if self.sched else []
            if nxt:
                t, rule = nxt[0]
                when = t.strftime("%I:%M %p").lstrip("0")
                day = "" if t.date() == dt.date.today() else t.strftime(" %a")
                msg = f"Next: {rule.name or rule.describe_action()} · {when}{day}"
        self.status.setText(msg)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)

    def sizeHint(self):
        return QSize(300, 330)
