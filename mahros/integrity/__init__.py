"""Data Integrity Agent: checks hospitals' real weekly capacity reports."""

from .rules import Flag, check_series, summarise

__all__ = ["Flag", "check_series", "summarise"]
