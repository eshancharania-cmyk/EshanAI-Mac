"""Non-vision screen awareness: resolutions, metrics, foreground window."""
from __future__ import annotations

from ctypes import windll

import pyautogui as pag

from .registry import ToolResult
from . import appctl


def screen_info() -> ToolResult:
    w, h = pag.size()
    sm = windll.user32.GetSystemMetrics
    vx, vy = sm(76), sm(77)
    vw, vh = sm(78), sm(79)
    try:
        dpi = windll.user32.GetDpiForSystem()
    except Exception:
        dpi = 96
    scale = round(dpi / 96.0, 2)
    wins = appctl.visible_windows()
    active = next((x for x in wins if x["active"]), None)
    line = (f"Primary screen {w}x{h}px (DPI scale {scale}x). "
            f"Virtual desktop: {vw}x{vh}@({vx},{vy}). "
            f"Cursor at ({pag.position().x},{pag.position().y}). ")
    if active:
        b = active["box"]
        line += f"Active window \"{active['title']}\" at x={b[0]},y={b[1]} size {b[2]}x{b[3]}. "
    line += f"{len(wins)} titled windows open."
    return ToolResult(True, line, {"screen": [w, h], "virtual": [vx, vy, vw, vh], "scale": scale})


def cursor_position() -> ToolResult:
    p = pag.position()
    return ToolResult(True, f"Cursor at ({p.x}, {p.y}).", {"x": p.x, "y": p.y})
