"""LINAK USB2LIN06 cable protocol (HID feature reports).

The cable is a plain USB HID device, so Windows needs no driver. It speaks three 64-byte
feature reports; byte 0 of every report is its report ID.

Sources: the Linux kernel patch "actuator: introduce Linak USB2LIN cable support",
gryf/linak-ctrl and ranma1988/usb2lin06 (tested with a CBD6S).
"""
from dataclasses import dataclass
from typing import Optional, Sequence

VENDOR_ID = 0x12D3
PRODUCT_ID = 0x0002

REPORT_INIT = 3      # PC -> cable, once at start, then wait INIT_WAIT seconds
REPORT_STATUS = 4    # cable -> PC, read every 0.1 to 0.3 s
REPORT_MOVE = 5      # PC -> cable
REPORT_LEN = 64
INIT_WAIT = 0.5

MOVE_UP = 0x8000     # "keep moving up", like holding the handset button; repeat every 0.1 s
MOVE_DOWN = 0x7FFF   # "keep moving down"
STOPPED = 0x8001     # status target value while the box is idle

UNITS_PER_MM = 10    # positions are in 0.1 mm
MAX_POSITION = 0x7FFE  # anything above this is a handset code, not a position

FLAG_DOWN = 0x80     # status byte 6, bit 7: moving down
FLAG_BLOCKED = 0x02  # status byte 6, bit 1: blocked


def init_report() -> list:
    buf = [0] * REPORT_LEN
    buf[0:4] = [REPORT_INIT, 0x04, 0x00, 0xFB]
    return buf


def move_report(value: int) -> list:
    """Move command: the 16-bit value, low byte first, repeated 4 times in bytes 1 to 8."""
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"move value out of range: {value}")
    lo, hi = value & 0xFF, (value >> 8) & 0xFF
    buf = [0] * REPORT_LEN
    buf[0] = REPORT_MOVE
    buf[1:9] = [lo, hi] * 4
    return buf


@dataclass(frozen=True)
class Status:
    position: int            # 0.1 mm units (a 665 mm stroke reads about 0 to 6650)
    moving: bool             # byte 7 non-zero: the real "moving" flag
    going_down: bool         # byte 6 bit 7: direction only, NOT a moving flag
    blocked: bool            # byte 6 bit 1
    target: Optional[int]    # bytes 20-21; STOPPED (0x8001) when idle
    raw: tuple

    @property
    def mm(self) -> float:
        return self.position / UNITS_PER_MM

    @property
    def idle(self) -> bool:
        return not self.moving and self.target == STOPPED


def parse_status(raw: Optional[Sequence[int]]) -> Status:
    """Decode a status report. Raises IOError on a short or empty answer, which happens
    now and then on Windows and should be treated as a single glitch, not a lost cable."""
    if raw is None or len(raw) < 8:
        raise IOError(f"short status from the cable ({0 if raw is None else len(raw)} bytes)")
    flags = raw[6]
    return Status(
        position=raw[4] | (raw[5] << 8),
        moving=raw[7] != 0,
        going_down=bool(flags & FLAG_DOWN),
        blocked=bool(flags & FLAG_BLOCKED),
        target=(raw[20] | (raw[21] << 8)) if len(raw) > 21 else None,
        raw=tuple(raw),
    )


def mm_to_units(mm: float) -> int:
    return int(round(mm * UNITS_PER_MM))
