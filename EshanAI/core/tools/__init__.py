"""EshanAI trusted tool registry bootstrap.

All tool implementations are explicit, non-evaluating code with validated
arguments. The model can only NAME a tool and provide arguments.
"""
from __future__ import annotations

from .registry import REGISTRY, ToolSpec, ToolResult, ToolError, NeedConfirmation, ConfirmationBroker
from . import appctl, inputctl, screenctl, filectl, systools


def start_background() -> None:
    """Kick off the slow app index (Start Menu walk, UWP scan) at app startup."""
    appctl.start_background_index()


def _spec(name, desc, args, req, impl, danger=""):
    REGISTRY.register(ToolSpec(name, desc, args, req, impl, danger))


# ---------------- applications ----------------
_spec("open_app", "open an installed app by name (Calculator, Edge, Notepad, Chrome, explorer, Settings...)",
      {"name": "app name str"}, ["name"], lambda a, b: appctl.open_app(a["name"]))
_spec("close_app", "close an app's windows (normal close; asks before force-terminating)",
      {"name": "app name str"}, ["name"], lambda a, b: appctl.close_app(a["name"], a.get("_confirmed", False)))
_spec("switch_app", "bring an app's window into focus",
      {"name": "app/window title str"}, ["name"], lambda a, b: appctl.switch_app(a["name"]))
_spec("list_apps", "list visible windows with pids", {}, [], lambda a, b: appctl.list_apps())
_spec("active_window", "title+position of the focused window", {}, [], lambda a, b: appctl.active_window())
_spec("wait_window", "wait until an app window appears/exists",
      {"name": "app or window title str", "timeout": "seconds int, default 6"}, ["name"],
      lambda a, b: appctl.wait_window(a["name"], a.get("timeout", 6)))

# ---------------- keyboard / mouse ----------------
_spec("type_text", "type text into the focused window (uses clipboard, restores it)",
      {"text": "text to type str"}, ["text"], lambda a, b: inputctl.type_text(a["text"]))
_spec("press_key", "press a key or shortcut; e.g. enter, esc, ctrl+s, ctrl+shift+t, win",
      {"key": "key combo str"}, ["key"], lambda a, b: inputctl.key_press(a["key"]))
_spec("move_mouse", "move the cursor",
      {"x": "int", "y": "int"}, ["x", "y"],
      lambda a, b: inputctl.mouse_move(a["x"], a["y"]))
_spec("click", "click at screen coordinates (physical pixels)",
      {"x": "int", "y": "int", "button": "left(default)/right/middle"}, ["x", "y"],
      lambda a, b: inputctl.mouse_click(a["x"], a["y"], a.get("button", "left")))
_spec("double_click", "double click at coordinates", {"x": "int", "y": "int"}, ["x", "y"],
      lambda a, b: inputctl.mouse_double_click(a["x"], a["y"]))
_spec("right_click", "right click at coordinates", {"x": "int", "y": "int"}, ["x", "y"],
      lambda a, b: inputctl.mouse_click(a["x"], a["y"], "right"))
_spec("scroll", "scroll at cursor/coords; dy>0 = scroll down",
      {"dy": "int units", "x": "int optional", "y": "int optional"}, ["dy"],
      lambda a, b: inputctl.mouse_scroll(a["dy"], a.get("x"), a.get("y")))
_spec("drag", "drag from (x1,y1) to (x2,y2)",
      {"x1": "int", "y1": "int", "x2": "int", "y2": "int"}, ["x1", "y1", "x2", "y2"],
      lambda a, b: inputctl.mouse_drag(a["x1"], a["y1"], a["x2"], a["y2"]))

# ---------------- screen (no vision: metrics & windows only) ----------------
_spec("screen_info", "screen resolution, DPI scale, cursor, active window", {}, [],
      lambda a, b: screenctl.screen_info())
_spec("cursor_position", "current cursor coordinates", {}, [],
      lambda a, b: screenctl.cursor_position())

# ---------------- files ----------------
_spec("list_dir", "list folder contents", {"path": "absolute path str (empty=user home)"}, [],
      lambda a, b: filectl.list_dir(a.get("path", "")))
_spec("find_file", "search file/folder names in user folders or a given folder",
      {"query": "substring str", "folder": "absolute folder str, optional"}, ["query"],
      lambda a, b: filectl.find_file(a["query"], a.get("folder", "")))
_spec("read_text", "read a text file (txt md py json csv log html...) up to 4k chars",
      {"path": "absolute path str"}, ["path"], lambda a, b: filectl.read_text(a["path"]))
_spec("write_text", "create/overwrite-or-append a text file (asks before overwrite)",
      {"path": "absolute path str", "content": "content str",
       "append": "yes/no (default no)"}, ["path", "content"],
      lambda a, b: filectl.write_text(a["path"], a["content"], a.get("append", "no"),
                                      a.get("_confirmed", False)))
_spec("new_folder", "create a folder (fails if exists)", {"path": "absolute path str"}, ["path"],
      lambda a, b: filectl.new_folder(a["path"]))
_spec("rename", "rename a file or folder in place",
      {"path": "absolute path str", "new_name": "new file name str"}, ["path", "new_name"],
      lambda a, b: filectl.rename(a["path"], a["new_name"]))
_spec("move_file", "move a file/folder into a destination folder (asks before overwrite)",
      {"path": "absolute path str", "dest_folder": "destination folder str"}, ["path", "dest_folder"],
      lambda a, b: filectl.move_file(a["path"], a["dest_folder"], a.get("_confirmed", False)))
_spec("delete_file", "move a file/folder to the Recycle Bin; ALWAYS asks the user first",
      {"path": "absolute path str"}, ["path"],
      lambda a, b: filectl.delete_file(a["path"], a.get("_confirmed", False)),
      danger="always")
_spec("open_path", "open a file (default app) or folder (explorer)",
      {"path": "absolute path str"}, ["path"], lambda a, b: filectl.open_path(a["path"]))
_spec("open_url", "open a URL in the default browser",
      {"url": "http(s)://... str"}, ["url"], lambda a, b: filectl.open_url(a["url"]))

# ---------------- system / memory ----------------
_spec("system_info", "OS, CPU, RAM, disk, battery, GPU", {}, [], lambda a, b: systools.system_info())
_spec("remember", "save a lasting fact about the user or machine to persistent memory",
      {"fact": "the fact, one sentence str"}, ["fact"], lambda a, b: systools.remember(a["fact"]))
_spec("memory_search", "search stored memories for a keyword",
      {"query": "keyword str"}, ["query"], lambda a, b: systools.memory_search(a["query"]))


__all__ = ["REGISTRY", "ToolResult", "ToolError", "NeedConfirmation", "ConfirmationBroker",
           "appctl", "inputctl", "screenctl", "filectl", "systools", "start_background"]
