"""LINAK leg test tool: checks the cable and the leg without any other app.

    python -m linak_leg.cli            # real cable
    python -m linak_leg.cli --sim      # simulated control box + leg, no hardware needed

Before the first run with hardware: close LINAK's own Desk Control app (only one program
can use the cable at a time), and run the normal reset with the hand control (hold DOWN
until the leg bottoms out and stops).
"""
import argparse
import sys
import time

from . import protocol as P
from .leg import LegConfig, LinakLeg, open_hid_device
from .sim import SimulatedLeg

HELP = """Commands:
  s            show position
  w            watch position live (use the hand control; Ctrl+C to stop watching)
  g <mm>       go to a height in mm, e.g.  g 250
  u <mm>       up by this many mm,        e.g.  u 50
  d <mm>       down by this many mm,      e.g.  d 50
  up <sec>     like holding UP on the handset for this many seconds,   e.g.  up 3
  down <sec>   like holding DOWN on the handset,                         e.g.  down 3
  raw          show the cable's raw status bytes
  q            quit
Ctrl+C during a move stops the leg."""


def fmt(leg):
    if leg.pos is None:
        return "  position unknown (leg offline)"
    return f"  position {leg.pos:5d}  ({leg.pos / P.UNITS_PER_MM:6.1f} mm)  {'moving' if leg.moving else '      '}"


def hold(leg, direction, seconds, out=print):
    """Like holding the handset button: confirm the direction every 0.1 s, then let go."""
    start = time.monotonic()
    try:
        while time.monotonic() - start < seconds:
            leg.command(direction)
            out("\r" + fmt(leg), end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        leg.settle()
    out()


def go_to(leg, target, out=print):
    leg.go_to(target, wait=False)
    try:
        while not leg._goal_done.wait(0.2):
            out("\r" + fmt(leg), end="", flush=True)
    except KeyboardInterrupt:
        leg.stop()
        out("\n  Stopped (Ctrl+C).", end="")
    result = leg.wait_goal(5)
    out()
    return result


def run(leg, lines=None, out=print):
    """The command loop. `lines` is an iterator of commands (for tests); default is input()."""
    read = (lambda: next(lines)) if lines is not None else (lambda: input("\n> "))
    while True:
        try:
            cmd = read().strip().lower().split()
        except (EOFError, KeyboardInterrupt, StopIteration):
            break
        if not cmd:
            continue
        c, args = cmd[0], cmd[1:]
        try:
            if c == "q":
                break
            elif c == "s":
                out(fmt(leg) + ("" if leg.online else "  - " + leg.message))
            elif c == "raw":
                st = leg.last_status
                out("  ", list(st.raw[:24]) if st else "no status yet")
            elif c == "w":
                try:
                    while True:
                        out("\r" + fmt(leg), end="", flush=True)
                        time.sleep(0.2)
                except KeyboardInterrupt:
                    out()
            elif c in ("up", "down") and args:
                secs = min(30.0, float(args[0]))
                out(f"  Holding {c.upper()} for {secs:g} s (Ctrl+C to stop) ...")
                hold(leg, 1 if c == "up" else -1, secs, out)
                out(f"  Now at {leg.pos} ({leg.mm:.1f} mm)" if leg.pos is not None else "  Leg offline")
            elif c in ("g", "u", "d") and args:
                if leg.pos is None:
                    out("  Leg offline - check the cable.")
                    continue
                amount = P.mm_to_units(float(args[0]))
                target = amount if c == "g" else leg.pos + amount if c == "u" else leg.pos - amount
                target = max(0, min(P.MAX_POSITION, target))
                out(f"  Moving to {target} ({target / P.UNITS_PER_MM:.1f} mm) ...")
                result = go_to(leg, target, out)
                note = "" if result == "reached" else f"  ({result})"
                out(f"  Now at {leg.pos} ({leg.mm:.1f} mm){note}" if leg.pos is not None else f"  {result}")
            else:
                out(HELP)
            if leg.resting:
                out(f"  The leg has run {leg.run_seconds():.0f} s in the last 18 min (DL6 limit: 120 s). "
                    "Moves are blocked until it has rested.")
        except ValueError:
            out("  Type a number, e.g.  g 250")


def main(argv=None):
    ap = argparse.ArgumentParser(description="LINAK leg test tool (DL6 + CBD6S + USB2LIN06)")
    ap.add_argument("--sim", action="store_true", help="use a simulated control box and leg")
    ap.add_argument("--min-mm", type=float, help="lowest allowed height in mm")
    ap.add_argument("--max-mm", type=float, help="highest allowed height in mm")
    ap.add_argument("--log", default="leg_errors.log", help="USB glitch log file ('' = off)")
    a = ap.parse_args(argv)

    cfg = LegConfig(log_path=a.log or None,
                    min_position=P.mm_to_units(a.min_mm) if a.min_mm is not None else None,
                    max_position=P.mm_to_units(a.max_mm) if a.max_mm is not None else None)
    print("LINAK leg test" + (" (SIMULATED leg)" if a.sim else "") + "\n")
    opener = SimulatedLeg(start_mm=100).opener if a.sim else open_hid_device
    leg, why = LinakLeg.try_open(cfg, opener)
    if leg is None:
        sys.exit(f"Can't use the leg: {why}.\n"
                 "  - Is the USB2LIN06 cable plugged into this PC and into the control box's hand-control port?\n"
                 "  - Is LINAK's Desk Control app open? Close it - only one program can use the cable.")
    time.sleep(0.9)                      # init + first status
    print(f"Connected.{fmt(leg)}\n")
    print("Keep hands and objects clear of the leg. The hand control still works at the same time.\n")
    print(HELP)
    try:
        run(leg)
    finally:
        leg.close()
    print("Bye.")


if __name__ == "__main__":
    main()
