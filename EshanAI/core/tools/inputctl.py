"""Keyboard and mouse tools (pyautogui). Coordinates are physical pixels of the
primary monitor (`screen_info` gives the model the exact frame).
"""
from __future__ import annotations

import difflib

import pyautogui as pag
import pyperclip

from .registry import ToolError, ToolResult

pag.FAILSAFE = False
pag.PAUSE = 0.05  # gentle, avoids dropped events when actions chain

SCREEN: tuple[int, int] = pag.size()


def _clamp(x, y) -> tuple[int, int, bool]:
    w, h = pag.size()
    cx = max(0, min(int(x), w - 1))
    cy = max(0, min(int(y), h - 1))
    return cx, cy, (cx != int(x) or cy != int(y))


def mouse_move(x: float, y: float) -> ToolResult:
    cx, cy, clamped = _clamp(x, y)
    pag.moveTo(cx, cy, duration=0.12)
    return ToolResult(True, f"Cursor moved to ({cx}, {cy})." + (" (clamped to screen)" if clamped else ""),
                      {"x": cx, "y": cy})


def mouse_click(x: float, y: float, button: str = "left") -> ToolResult:
    button = (button or "left").lower()
    if button not in ("left", "right", "middle"):
        raise ToolError(f"Unknown mouse button '{button}' (use left/right/middle)")
    cx, cy, clamped = _clamp(x, y)
    pag.moveTo(cx, cy, duration=0.12)
    pag.click(button=button)
    note = " (clamped to screen)" if clamped else ""
    return ToolResult(True, f"{button.title()} mouse click at ({cx}, {cy}).{note}", {"x": cx, "y": cy})


def mouse_double_click(x: float, y: float) -> ToolResult:
    cx, cy, clamped = _clamp(x, y)
    pag.moveTo(cx, cy, duration=0.12)
    pag.doubleClick()
    return ToolResult(True, f"Double click at ({cx}, {cy})." + (" (clamped)" if clamped else ""), {"x": cx, "y": cy})


def mouse_scroll(dy: float, x=None, y=None) -> ToolResult:
    """dy > 0 scrolls DOWN (documented for the model), implemented inverted."""
    amount = int(dy)
    kwargs = {}
    if x is not None and y is not None:
        cx, cy, _ = _clamp(x, y)
        pag.moveTo(cx, cy, duration=0.1)
        kwargs = {"x": cx, "y": cy}
    pag.scroll(-amount, **kwargs)
    return ToolResult(True, f"Scrolled {'down' if amount > 0 else 'up'} {abs(amount)} units.", {"dy": amount})


def mouse_drag(x1, y1, x2, y2, duration: float = 0.4) -> ToolResult:
    ax, ay, _ = _clamp(x1, y1)
    bx, by, _ = _clamp(x2, y2)
    pag.moveTo(ax, ay, duration=0.15)
    pag.mouseDown()
    try:
        pag.moveTo(bx, by, duration=max(0.15, min(float(duration), 2.0)))
    finally:
        pag.mouseUp()
    return ToolResult(True, f"Dragged ({ax},{ay}) -> ({bx},{by}).", {"from": [ax, ay], "to": [bx, by]})


def _resolve_key(key: str) -> list[str]:
    """'ctrl+shift+t' -> ['ctrl','shift','t']; validates against pyautogui keys."""
    parts = [p.strip().lower() for p in key.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ToolError("Empty key combination")
    known = set(pag.KEYBOARD_KEYS)
    fixed = []
    for p in parts:
        p = {"esc": "esc", "escape": "esc", "return": "enter", "windows": "win",
             "winkey": "win", "meta": "win", "super": "win", "del": "delete",
             "backspace": "backspace", "printscreen": "printscreen",
             "pagedown": "pagedown", "pgdn": "pagedown", "pgup": "pageup"}.get(p, p)
        if p in known:
            fixed.append(p)
            continue
        close = difflib.get_close_matches(p, sorted(known), n=1, cutoff=0.6)
        if close:
            fixed.append(close[0])
        else:
            raise ToolError(f"Unknown key '{p}'. Valid keys include: "
                            f"enter, esc, tab, space, backspace, delete, home, end, "
                            f"pageup, pagedown, insert, printscreen, f1..f24, "
                            f"a..z, 0..9, ctrl/shift/alt/win + any of these.")
    return fixed


def key_press(key: str) -> ToolResult:
    keys = _resolve_key(key)
    if len(keys) == 1:
        pag.press(keys[0])
    else:
        pag.hotkey(*keys, interval=0.04)
    shown = "+".join(k.upper() for k in keys)
    return ToolResult(True, f"Pressed {shown}.", {"keys": keys})


def type_text(text: str, _confirmed: bool = False) -> ToolResult:
    text = str(text)
    if not text:
        raise ToolError("Empty text")
    import time
    try:
        saved = ""
        try:
            saved = pyperclip.paste()
        except Exception:
            pass
        pyperclip.copy(text)
        pag.hotkey("ctrl", "v", interval=0.05)
        time.sleep(0.3)
        try:
            if saved:
                pyperclip.copy(saved)
        except Exception:
            pass
        shown = text if len(text) <= 60 else text[:57] + "…"
        return ToolResult(True, f"Typed {len(text)} characters (via clipboard paste): {shown!r}",
                          {"len": len(text), "method": "clipboard"})
    except Exception as e:
        # last resort: direct key emission for ASCII
        try:
            if all(ord(c) < 128 for c in text):
                pag.write(text, interval=0.02)
                return ToolResult(True, f"Typed {len(text)} characters (direct keys): {len(text)}", {"method": "keys"})
        except Exception:
            pass
        raise ToolError(f"Typing failed: {e}") from e
