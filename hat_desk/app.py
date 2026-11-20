"""HAT Desk tray app: one per desk PC.

    python -m hat_desk                 real desk (USB2LIN06 cable)
    python -m hat_desk --sim           simulated desk, no hardware
    python -m hat_desk --admin-key K   set the office admin key on this desk (once, at install)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .controller import DeskController
from .scheduler import Scheduler
from .settings import SettingsStore, app_dir
from .ui import theme
from .ui.panel import DeskPanel
from .ui.settings_dialog import SettingsDialog

log = logging.getLogger("hat_desk")
INSTANCE_KEY = "hat-desk-single-instance"


class Bridge(QObject):
    """Carries events from worker threads (scheduler, leg) to the Qt thread."""
    warning = Signal(object, int)
    fired = Signal(object, str)


def setup_logging():
    h = RotatingFileHandler(os.path.join(app_dir(), "hat_desk.log"), maxBytes=512_000, backupCount=2,
                            encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


class TrayApp:
    def __init__(self, app: QApplication, simulate: bool, agent: bool = True):
        self.app = app
        self.store = SettingsStore()
        self.ctl = DeskController(self.store, simulate=simulate)
        self.sched = Scheduler(self.store, self.ctl)
        self.bridge = Bridge()
        self.sched.on_warning = self.bridge.warning.emit
        self.sched.on_fired = self.bridge.fired.emit
        self.bridge.warning.connect(self._on_warning)
        self.sched.start()

        self.agent = None
        if agent and self.store.settings.agent_enabled:
            from .agent import DeskAgent
            try:
                self.agent = DeskAgent(self.store, self.ctl)
                self.agent.start()
            except OSError as e:
                log.warning("network agent not started: %s", e)

        self.panel = DeskPanel(self.ctl, self.sched)
        self.panel.open_settings.connect(self.open_settings)
        self.panel.skip_requested.connect(self.sched.skip)
        self._pending_rule = None

        self.tray = QSystemTrayIcon(theme.tray_icon(), app)
        self.tray.setToolTip("HAT Desk")
        menu = QMenu()
        for i in range(3):
            menu.addAction(f"Preset {i + 1}", lambda i=i: self.ctl.go_preset(i))
        self._preset_actions = menu.actions()[:3]
        menu.addSeparator()
        menu.addAction("Stop", self.ctl.stop)
        menu.addAction("Settings...", self.open_settings)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        menu.aboutToShow.connect(self._refresh_menu)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self.tray.messageClicked.connect(self._message_clicked)
        self.tray.show()

        self.server = QLocalServer()
        QLocalServer.removeServer(INSTANCE_KEY)
        self.server.listen(INSTANCE_KEY)
        self.server.newConnection.connect(lambda: (self.server.nextPendingConnection(), self.show_panel()))

    # ---- tray ------------------------------------------------------------------
    def _refresh_menu(self):
        s = self.store.settings
        for a, p in zip(self._preset_actions, s.presets):
            a.setText(f"{p.name}  ({s.fmt(p.height_mm)})" if p.height_mm is not None else f"{p.name}  (not set)")
            a.setEnabled(p.height_mm is not None and self.ctl.online)

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.panel.isVisible():
                self.panel.hide()
            else:
                self.show_panel()

    def show_panel(self):
        self.panel.adjustSize()
        geo = self.tray.geometry()
        screen = QGuiApplication.screenAt(geo.center() if geo.isValid() else QCursor.pos()) \
            or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        w, h = self.panel.width(), self.panel.sizeHint().height()
        # sit next to the tray icon, inside the work area (taskbar can be on any edge)
        anchor = geo.center() if geo.isValid() else QCursor.pos()
        x = min(max(anchor.x() - w // 2, avail.left()), avail.right() - w)
        y = avail.bottom() - h if anchor.y() > avail.center().y() else avail.top()
        self.panel.move(QPoint(x, y))
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def open_settings(self):
        self.panel.hide()
        SettingsDialog(self.ctl, self.sched).exec()

    def _on_warning(self, rule, seconds):
        self._pending_rule = rule
        self.panel.show_warning(rule, seconds)
        self.tray.showMessage(rule.name or "Scheduled desk move",
                              f"In {seconds} s: {rule.describe_action().lower()}.\nClick here to skip.",
                              QSystemTrayIcon.Information, 15000)

    def _message_clicked(self):
        if self._pending_rule:
            self.sched.skip(self._pending_rule.id)
            self.tray.showMessage("Skipped", f"“{self._pending_rule.name}” won't run today.",
                                  QSystemTrayIcon.Information, 4000)
            self._pending_rule = None

    def quit(self):
        self.sched.stop()
        if self.agent:
            self.agent.stop()
        self.ctl.close()
        self.tray.hide()
        self.app.quit()


def main(argv=None):
    p = argparse.ArgumentParser(prog="hat_desk", description="HAT Desk tray app")
    p.add_argument("--sim", action="store_true", help="simulated desk (no hardware)")
    p.add_argument("--admin-key", help="set the office admin key for this desk and exit")
    p.add_argument("--no-agent", action="store_true", help="don't accept admin connections")
    p.add_argument("--show", action="store_true", help="open the panel at start")
    a = p.parse_args(argv)

    if a.admin_key:
        store = SettingsStore()
        store.update(lambda s: setattr(s, "admin_key", a.admin_key))
        print("Admin key saved for", store.settings.desk_name)
        return 0

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("HAT Desk")
    app.setQuitOnLastWindowClosed(False)

    # second start: ask the running copy to show its panel, then leave
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if sock.waitForConnected(300):
        sock.disconnectFromServer()
        return 0

    app.setFont(theme.base_font())
    app.setStyleSheet(theme.STYLE)
    app.setWindowIcon(theme.app_icon())
    setup_logging()
    tray = TrayApp(app, simulate=a.sim, agent=not a.no_agent)
    if a.show or not QSystemTrayIcon.isSystemTrayAvailable():
        tray.show_panel()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
