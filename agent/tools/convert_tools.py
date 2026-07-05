"""Unit-conversion tools: length, weight, temperature, data size."""

from __future__ import annotations

from .base import Tool, ToolResult

_LENGTH = {  # to metres
    "mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
    "in": 0.0254, "ft": 0.3048, "yd": 0.9144, "mi": 1609.344,
}
_WEIGHT = {  # to grams
    "mg": 0.001, "g": 1.0, "kg": 1000.0, "oz": 28.3495, "lb": 453.592, "t": 1_000_000.0,
}
_DATA = {  # to bytes
    "b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4,
}


def _convert(value: float, frm: str, to: str, table: dict[str, float]) -> ToolResult:
    frm, to = frm.lower(), to.lower()
    if frm not in table or to not in table:
        return ToolResult(output=f"Unknown unit. Valid: {', '.join(table)}", success=False)
    result = value * table[frm] / table[to]
    return ToolResult(output=f"{value} {frm} = {result:g} {to}")


def convert_length(value: float, frm: str, to: str) -> ToolResult:
    return _convert(value, frm, to, _LENGTH)


def convert_weight(value: float, frm: str, to: str) -> ToolResult:
    return _convert(value, frm, to, _WEIGHT)


def convert_data(value: float, frm: str, to: str) -> ToolResult:
    return _convert(value, frm, to, _DATA)


def convert_temp(value: float, frm: str, to: str) -> ToolResult:
    frm, to = frm.upper(), to.upper()
    # Normalise to Celsius first.
    if frm == "C":
        c = value
    elif frm == "F":
        c = (value - 32) * 5 / 9
    elif frm == "K":
        c = value - 273.15
    else:
        return ToolResult(output="Units: C, F, K", success=False)
    if to == "C":
        out = c
    elif to == "F":
        out = c * 9 / 5 + 32
    elif to == "K":
        out = c + 273.15
    else:
        return ToolResult(output="Units: C, F, K", success=False)
    return ToolResult(output=f"{value}{frm} = {out:g}{to}")


def get_convert_tools() -> list[Tool]:
    schema = {
        "type": "object",
        "properties": {
            "value": {"type": "number"},
            "frm": {"type": "string"},
            "to": {"type": "string"},
        },
        "required": ["value", "frm", "to"],
    }
    return [
        Tool("convert_length", "Convert length units (mm, cm, m, km, in, ft, yd, mi).", schema, convert_length),
        Tool("convert_weight", "Convert weight units (mg, g, kg, oz, lb, t).", schema, convert_weight),
        Tool("convert_data", "Convert data-size units (b, kb, mb, gb, tb).", schema, convert_data),
        Tool("convert_temp", "Convert temperature between C, F and K.", schema, convert_temp),
    ]
