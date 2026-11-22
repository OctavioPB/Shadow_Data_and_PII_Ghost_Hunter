"""Helpers shared by CSV export endpoints."""

from __future__ import annotations

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: str) -> str:
    """Neutralize spreadsheet formula injection (cells starting with = + - @ …).

    Exports are opened by DPOs in Excel/Sheets; a value such as
    ``=HYPERLINK(...)`` coming from an event payload would otherwise execute.
    """
    if value and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value
