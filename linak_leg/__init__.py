"""Control a LINAK lifting column (e.g. DL6) through a CBD6S control box and a USB2LIN06 cable."""
from .leg import LegConfig, LinakLeg, list_cables, open_hid_device
from .protocol import Status, mm_to_units, parse_status
from .sim import SimulatedLeg

__all__ = ["LegConfig", "LinakLeg", "open_hid_device", "list_cables", "SimulatedLeg", "Status",
           "mm_to_units", "parse_status"]
__version__ = "0.1.0"
