"""What the computer capture recorded (``vault/capture/<date>/``), turned into activities and
searchable units: pages on screen, lines read, long stares, searches, clicks, selections."""

from .synthesize import CaptureDay, capture_days, capture_stamp, capture_units, read_capture

__all__ = ["CaptureDay", "capture_days", "capture_stamp", "capture_units", "read_capture"]
