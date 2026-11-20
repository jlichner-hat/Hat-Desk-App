# HAT Desk App

Control LINAK leg sets (DL6 column, CBD6S control box, USB2LIN06 cable) from a PC.

## Leg package: `linak_leg`

Control a LINAK lifting column (DL6) from a PC, through a **CBD6S** control box and LINAK's
**USB2LIN06** desk control cable. No Arduino or PLC: the cable is a standard USB HID device,
so Windows needs no driver, only `pip install hidapi`.

This is the leg code from JViz (`LinakLeg` in `arm_counter.py`), moved into its own package
with a simulated control box and leg, so it can be tested without hardware and reused by
other apps.

```
PC ──USB── USB2LIN06 ──► CBD6S hand-control port ──► DL6 column
                         (hand control stays in the other port and keeps working)
```

### What's in it

| File | What it does |
| --- | --- |
| `linak_leg/protocol.py` | USB2LIN06 reports: init, status, move; handset codes |
| `linak_leg/leg.py` | `LinakLeg`: background thread that drives the leg like the handset, with the safety limits below |
| `linak_leg/sim.py` | `SimulatedLeg`: a fake CBD6S + DL6 with the same methods as `hid.device` |
| `linak_leg/cli.py` | The leg test tool (was `linak_test.py`); `--sim` runs it without hardware |
| `LINAK_TEST.bat` | Double-click starter for the test tool; installs hidapi the first time |

### Using it

```python
from linak_leg import LinakLeg, LegConfig

leg, why = LinakLeg.try_open(LegConfig(min_position=500))   # never raises
if leg is None:
    print("no leg:", why)

# every UI frame, from the button being held:
leg.command(+1)      # +1 up, -1 down, 0 nothing held
leg.mm               # height in mm (None while offline)
leg.online, leg.message, leg.resting

leg.go_to(2500)      # go to 250.0 mm and wait: "reached", "stopped short", "cancelled", ...
leg.settle()         # stop and wait until still (before a photo)
leg.close()          # final stop
```

Positions are in 0.1 mm units (`protocol.mm_to_units(250) == 2500`).

Several leg sets on one PC: each has its own cable. `list_cables()` gives their paths; open
one with `LinakLeg.try_open(cfg, opener=lambda: open_hid_device(path))`.

Without hardware: `sim = SimulatedLeg(start_mm=100)` and `LinakLeg.try_open(cfg, sim.opener)`.

Speed is not settable: the CBD6S sets it (DL6 top speed about 38 mm/s).

`command()`, `stop()`, `go_to()` and `settle()` can be called from any thread; the leg's own
thread is the only one that touches the cable. One `go_to()` runs at a time per leg, and a new
one replaces the one running.

### Safety behaviour

The leg moves only while a button is held and the app keeps confirming it. Every other path
leads to a stop.

- **Hold to move.** Release sends the handset-style stop (one command the other way), checked
  again 0.15 s later and repeated if the leg is still moving.
- **Watchdog.** If `command()` isn't called for `watchdog` (0.5 s), the leg stops. A frozen
  app can't keep it moving. The control box's own dead-man stop is a second layer.
- **Hold limit.** A single hold stops by itself after `max_hold` (20 s).
- **Duty cycle.** After `duty_max` (120 s) of running in `duty_window` (18 min), moves are
  refused and `leg.resting` is true until the leg has rested.
- **Allowed range.** Optional `min_position` / `max_position`. A hold stops early by
  `limit_margin` (1 mm) plus the distance the leg covers in `limit_lookahead` (0.25 s) at its
  current speed, about 10 mm at full speed, so it doesn't pass the limit while the stop takes
  effect. `go_to()` targets are clamped. *Software limit only*.
- **USB glitches.** Single bad answers are ignored and logged to `leg_errors.log`; after
  `max_fails` (6) in a row the leg counts as offline, the cable is closed and reopened.
- **The CBD6S stays in charge** of overload cut-off, soft start/stop and end-of-travel limits.

Still needed before the leg moves anything over a line: a hardwired E-stop that cuts CBD6S
power, and a plant safety review.

#### Go to a height

`go_to()` uses the box's position mode and sends **the same target every 0.1 s**. Changing the
target on every cycle restarts the CBD6S soft start, which made the leg crawl on the bench.
`test_sim.py::test_changing_target_every_cycle_is_slow` reproduces that. A held button or
`stop()` cancels a `go_to()`. It has not been tried on the bench yet; check it with
`g <mm>` in the test tool.

### Test tool

```
python -m linak_leg.cli          # real cable  (or double-click LINAK_TEST.bat)
python -m linak_leg.cli --sim    # simulated leg
    --min-mm 50 --max-mm 600     # optional allowed range
```

Commands: `s` position, `w` watch live, `up 3` / `down 3` hold for 3 s, `g 250` go to 250 mm,
`u 50` / `d 50` up/down 50 mm, `raw` raw status bytes, `q` quit. Ctrl+C stops any move.

Before using real hardware: close LINAK's Desk Control app (only one program can use the
cable), and reset the leg with the hand control (hold DOWN until it bottoms out).

### Tests

```
pip install pytest
python -m pytest
```

The tests run against `SimulatedLeg` in real time (about a minute): it models 38 mm/s with a
soft start, the dead-man stop, handset codes, position mode, end of travel and failed USB
answers. Model the box first, then try it on the bench.

### Switching JViz over

In `arm_counter.py`:

1. Delete the `LinakLeg` class.
2. Import it from the package and pass the existing settings:

```python
from linak_leg import LinakLeg, LegConfig

LEG_CONFIG = LegConfig(watchdog=LEG_WATCHDOG, max_hold=LEG_MAX_HOLD, max_fails=LEG_MAX_FAILS,
                       duty_window=LEG_DUTY_WINDOW, duty_max=LEG_DUTY_MAX,
                       log_path=os.path.join(HERE, "leg_errors.log"))
...
leg, why = LinakLeg.try_open(LEG_CONFIG)
```

Nothing else changes: `command()`, `settle()`, `close()`, `pos`, `online` and `message` work
as before. `draw_leg_controls` can use `leg.resting` instead of checking the message text.
JViz's own `LEG_MAX_HOLD` check can stay; the package now enforces it too.

### Protocol reference

| Report | Direction | Purpose | Bytes |
| --- | --- | --- | --- |
| 3 | PC → cable | Init, once, then wait 0.5 s | `03 04 00 FB`, rest 0 |
| 4 | cable → PC | Status | bytes 4-5 position (LE, 0.1 mm); byte 6 bit 7 moving down, bit 1 blocked; byte 7 non-zero while moving; bytes 20-21 target (0x8001 = stopped) |
| 5 | PC → cable | Move | `05`, then the 16-bit value LE repeated 4 times |

Move values: `0x8000` keep moving up, `0x7FFF` keep moving down (repeat every 0.1 s while
held); stop = the opposite code once; `0`-`6650` go to a position.

Sources: Linux kernel patch "actuator: introduce Linak USB2LIN cable support",
[gryf/linak-ctrl](https://github.com/gryf/linak-ctrl),
[ranma1988/usb2lin06](https://github.com/ranma1988/usb2lin06) (tested with a CBD6S).
