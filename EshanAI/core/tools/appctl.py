"""Application control: resolve, open, close, switch, list (Windows).

Resolution layers (first hit wins):
  1. built-in alias table (Calculator, Edge, Notepad, ...)
  2. HKLM/HKCU "App Paths" registry (<name>.exe)
  3. Start Menu / Desktop .lnk fuzzy index (user + all users)
  4. PATH lookup (shutil.which)
  5. UWP/Store apps via Get-StartApps (cached in background)

Window enumeration/control uses direct ctypes (EnumWindows + DWM cloaked
filtering), which is more reliable than pygetwindow on busy/new Win11 builds.
All coordinates are physical pixels (the process sets DPI awareness at boot).
Verification is window/process-based (no vision).
"""
from __future__ import annotations

import ctypes
import difflib
import os
import re
import shutil
import subprocess
import threading
import time
import winreg
from ctypes import wintypes

import psutil
import pyautogui as pag

from .registry import ToolError, ToolResult, NeedConfirmation

pag.FAILSAFE = False
user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

DETACHED = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

WM_CLOSE = 0x0010
SW_RESTORE = 9
SW_SHOW = 5


# ------------------------- window enumeration (ctypes) -------------------------

_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def _window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd) + 1
    buf = ctypes.create_unicode_buffer(n)
    user32.GetWindowTextW(hwnd, buf, n)
    return buf.value


def _is_cloaked(hwnd: int) -> bool:
    try:
        val = ctypes.c_int(0)
        if dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(val),
                                        ctypes.sizeof(val)) == 0:
            return val.value != 0
    except Exception:
        pass
    return False


def _enumerate_raw() -> list[int]:
    out: list[int] = []
    def cb(hwnd, _l):
        if user32.IsWindowVisible(hwnd):
            out.append(hwnd)
        return True
    try:
        user32.EnumWindows(_EnumWindowsProc(cb), 0)
    except Exception:
        pass
    return out


def _pid_exe_map() -> dict[int, str]:
    """pid -> process name, one bounded pass (never throws)."""
    try:
        return {p.info["pid"]: (p.info["name"] or "") for p in psutil.process_iter(["pid", "name"])}
    except Exception:
        return {}


def visible_windows() -> list[dict]:
    """[{title,hwnd,pid,exe,active,minimized,box(l,t,w,h)}] — physical pixels."""
    active_hwnd = user32.GetForegroundWindow()
    pid_names = _pid_exe_map()
    out = []
    for hwnd in _enumerate_raw():
        try:
            title = _window_title(hwnd).strip()
            if not title or title.lower() == "none":
                continue
            if _is_cloaked(hwnd):
                continue
            rc = _RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            w, h = rc.right - rc.left, rc.bottom - rc.top
            if w <= 0 and h <= 0:
                continue
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe = (pid_names.get(pid.value) or "").lower()
            out.append({"title": title, "hwnd": hwnd, "pid": pid.value, "exe": exe,
                        "active": hwnd == active_hwnd, "minimized": bool(user32.IsIconic(hwnd)),
                        "box": (rc.left, rc.top, w, h)})
        except Exception:
            continue
    return out


def _close_window(hwnd: int) -> bool:
    try:
        return bool(user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0))
    except Exception:
        return False


def _restore_window(hwnd: int) -> None:
    try:
        user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
    except Exception:
        pass


def _activate_window(hwnd: int) -> None:
    """Force-focus a window even when our process isn't the foreground process.

    Windows only lets the foreground process call SetForegroundWindow, so we
    first tap ALT (grants activation right) and, if needed, attach to the
    current foreground thread's input queue.
    """
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
        else:
            user32.ShowWindow(wintypes.HWND(hwnd), SW_SHOW)
        # tap ALT to earn the right to set foreground
        user32.keybd_event(0x12, 0, 0, 0)          # ALT down
        user32.keybd_event(0x12, 0, 2, 0)          # ALT up
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        # verify; if it failed, attach-thread fallback
        time.sleep(0.06)
        if user32.GetForegroundWindow() != hwnd:
            kernel32 = ctypes.windll.kernel32
            fg = user32.GetForegroundWindow()
            fg_thread = user32.GetWindowThreadProcessId(fg, None)
            this_thread = kernel32.GetCurrentThreadId()
            if fg_thread and fg_thread != this_thread:
                user32.AttachThreadInput(this_thread, fg_thread, True)
                try:
                    user32.BringWindowToTop(wintypes.HWND(hwnd))
                    user32.SetForegroundWindow(wintypes.HWND(hwnd))
                    user32.SetFocus(wintypes.HWND(hwnd))
                finally:
                    user32.AttachThreadInput(this_thread, fg_thread, False)
    except Exception:
        pass

ALIASES: dict[str, dict] = {
    "calculator": {"kind": "exe", "target": "calc", "check": "Calculator"},
    "calc": {"kind": "exe", "target": "calc", "check": "Calculator"},
    "notepad": {"kind": "exe", "target": "notepad", "check": "Notepad"},
    "paint": {"kind": "exe", "target": "mspaint", "check": "Paint"},
    "mspaint": {"kind": "exe", "target": "mspaint", "check": "Paint"},
    "cmd": {"kind": "exe", "target": "cmd", "check": "cmd"},
    "command prompt": {"kind": "exe", "target": "cmd", "check": "cmd"},
    "powershell": {"kind": "exe", "target": "powershell", "check": "PowerShell"},
    "file explorer": {"kind": "exe", "target": "explorer", "check": "File Explorer"},
    "explorer": {"kind": "exe", "target": "explorer", "check": "File Explorer"},
    "task manager": {"kind": "exe", "target": "taskmgr", "check": "Task Manager"},
    "taskmgr": {"kind": "exe", "target": "taskmgr", "check": "Task Manager"},
    "control panel": {"kind": "exe", "target": "control", "check": "Control Panel"},
    "settings": {"kind": "shell", "target": "ms-settings:", "check": "Settings"},
    "edge": {"kind": "exe", "target": "msedge", "check": "Edge"},
    "msedge": {"kind": "exe", "target": "msedge", "check": "Edge"},
    "chrome": {"kind": "exe", "target": "chrome", "check": "Chrome"},
    "firefox": {"kind": "exe", "target": "firefox", "check": "Firefox"},
    "word": {"kind": "exe", "target": "winword", "check": "Word"},
    "excel": {"kind": "exe", "target": "excel", "check": "Excel"},
    "powerpoint": {"kind": "exe", "target": "powerpnt", "check": "PowerPoint"},
    "outlook": {"kind": "exe", "target": "outlook", "check": "Outlook"},
    "onenote": {"kind": "exe", "target": "onenote", "check": "OneNote"},
    "vscode": {"kind": "exe", "target": "code", "check": "Visual Studio Code"},
    "code": {"kind": "exe", "target": "code", "check": "Visual Studio Code"},
    "snipping tool": {"kind": "exe", "target": "snippingtool", "check": "Snip"},
    "camera": {"kind": "shell", "target": "microsoft.windows.camera:", "check": "Camera"},
    "photos": {"kind": "shell", "target": "ms-photos:", "check": "Photos"},
    "store": {"kind": "shell", "target": "ms-windows-store:", "check": "Store"},
    "minecraft": {"kind": "shell", "target": "minecraft:", "check": "Minecraft"},
    "spotify": {"kind": "exe", "target": "spotify", "check": "Spotify"},
    "weather": {"kind": "shell", "target": "msnweather:", "check": "Weather"},
    "clock": {"kind": "shell", "target": "ms-clock:", "check": "Clock"},
    "sticky notes": {"kind": "shell", "target": "onenote-sticky-notes:", "check": "Sticky"},
    "steam": {"kind": "exe", "target": "steam", "check": "Steam"},
}

PROTECTED_PROCESSES = {
    "explorer", "cmd", "powershell", "python", "pythonw", "ollama", "ollama app",
    "system", "registry", "winlogon", "csrss", "dwm", "wininit", "services",
    "lsass", "smss", "fontdrvhost", "svchost", "sihost", "taskhostw",
    "runtimebroker", "searchapp", "shellexperiencehost", "startmenuexperiencehost",
    "applicationframehost", "msedgewebview2", "conhost", "usercpl",
    "intelgraphicscontrol", "esif", "spoolsv", "audiodg", "wudfhost", "lsaiso",
}

_lnk_index: dict[str, str] | None = None
_uwp_index: dict[str, str] | None = None
_lock = threading.Lock()


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


# ------------------------- indexes -------------------------

def _start_menu_dirs() -> list[str]:
    home = os.environ.get("APPDATA", "")
    pd = os.environ.get("ProgramData", "")
    dirs = []
    for base in (home, pd):
        if base:
            root = os.path.join(base, "Microsoft", "Windows", "Start Menu")
            if os.path.isdir(root):
                dirs.append(root)
    return dirs


def build_lnk_index() -> dict[str, str]:
    """stem-normalized -> full .lnk path."""
    idx: dict[str, str] = {}
    for root in _start_menu_dirs() + [os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")]:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().endswith(".lnk"):
                    stem = os.path.splitext(f)[0]
                    key = norm(stem)
                    if key and key not in idx:
                        idx[key] = os.path.join(dirpath, f)
    return idx


def build_uwp_index() -> dict[str, str]:
    """App name -> shell:AppsFolder\\<AppID> via Get-StartApps (may take ~2s)."""
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }"],
            capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        idx = {}
        for line in (ps.stdout or "").splitlines():
            if "|" not in line:
                continue
            name, appid = line.split("|", 1)
            if appid and ("!" in appid or appid.startswith("Microsoft.")):
                idx[norm(name)] = f"shell:AppsFolder\\{appid}"
        return idx
    except Exception:
        return {}


def app_paths_lookup(name: str) -> str | None:
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for wow in (0, winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                key = winreg.OpenKey(hive,
                                     rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}.exe",
                                     0, winreg.KEY_QUERY_VALUE | wow)
                val, _ = winreg.QueryValueEx(key, "")
                winreg.CloseKey(key)
                if val:
                    return os.path.expandvars(val.strip('"'))
            except OSError:
                continue
    return None


def ensure_index(background: bool = False) -> tuple[dict[str, str], dict[str, str]]:
    global _lnk_index, _uwp_index
    if _lnk_index is None:
        with _lock:
            if _lnk_index is None:
                _lnk_index = build_lnk_index()
                if background:
                    def _u():
                        global _uwp_index
                        _uwp_index = build_uwp_index()
                    threading.Thread(target=_u, daemon=True).start()
                else:
                    _uwp_index = build_uwp_index()
    return _lnk_index, _uwp_index or {}


def start_background_index() -> None:
    ensure_index(background=True)


def resolve(name: str) -> dict | None:
    """Return {'kind','target','display','check'} or None."""
    name = str(name).strip()
    if name.lower().endswith(".lnk") and os.path.exists(name):
        return {"kind": "lnk", "target": name, "display": os.path.splitext(os.path.basename(name))[0],
                "check": os.path.splitext(os.path.basename(name))[0]}
    key = norm(name)
    if not key:
        raise ToolError("Empty application name")
    hit = ALIASES.get(key)
    if hit:
        return {"kind": hit["kind"], "target": hit["target"], "check": hit["check"], "display": name}
    if os.path.isabs(name) and os.path.exists(name):
        return {"kind": "exe", "target": name, "check": os.path.splitext(os.path.basename(name))[0],
                "display": name}
    exe = name if name.lower().endswith(".exe") else f"{name}.exe"
    p = app_paths_lookup(os.path.splitext(exe)[0])
    if p and os.path.exists(p):
        return {"kind": "exe", "target": p, "check": name, "display": name}
    lnk, uwp = ensure_index()
    if key in lnk:
        return {"kind": "lnk", "target": lnk[key], "check": name, "display": name}
    lnk_keys = list(lnk.keys())
    matches = difflib.get_close_matches(key, lnk_keys, n=1, cutoff=0.6)
    if matches:
        return {"kind": "lnk", "target": lnk[matches[0]], "check": name, "display": name}
    sub = [k for k in lnk_keys if key in k or k in key][0:1]
    if sub:
        return {"kind": "lnk", "target": lnk[sub[0]], "check": name, "display": name}
    if key in uwp:
        return {"kind": "shell", "target": uwp[key], "check": name, "display": name}
    uwp_matches = difflib.get_close_matches(key, list(uwp.keys()), n=1, cutoff=0.5)
    if uwp_matches:
        return {"kind": "shell", "target": uwp[uwp_matches[0]], "check": name, "display": name}
    which = shutil.which(name)
    if which:
        return {"kind": "exe", "target": which, "check": name, "display": name}
    return None


# ------------------------- windows helpers -------------------------

def tokens_match(text: str, needle: str) -> bool:
    t = norm(needle)
    return bool(t) and t in norm(text)


def find_windows_for(name: str, wins: list[dict] | None = None) -> list[dict]:
    wins = wins if wins is not None else visible_windows()
    hits = [w for w in wins if tokens_match(w["title"], name)]
    if not hits:
        hits = [w for w in wins if w["exe"] and tokens_match(os.path.splitext(w["exe"])[0], name)]
    return hits


def find_processes_for(name: str) -> list[psutil.Process]:
    """Processes whose exe basename matches the app-ish name (excluding protected)."""
    key = norm(name)
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            base = os.path.splitext(p.info["name"] or "")[0]
            if not base:
                continue
            nb = norm(base)
            if nb and (nb == key or key in nb or nb in key) and len(key) >= 3:
                if base.lower() in PROTECTED_PROCESSES:
                    continue
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


# ------------------------- launch & verify -------------------------

def _spawn(resolution: dict) -> None:
    kind, target = resolution["kind"], resolution["target"]
    if kind == "lnk":
        os.startfile(target)  # noqa: S606 (user-approved app)
    elif kind == "shell":
        subprocess.Popen(["explorer.exe", target], creationflags=DETACHED)
    elif kind == "exe":
        exe = target
        if not os.path.isabs(exe):
            exe = shutil.which(exe) or exe
        try:
            subprocess.Popen([exe], cwd=os.path.dirname(exe) or None,
                             creationflags=DETACHED)
        except FileNotFoundError:
            # last resort: shell-resolve (protocol-only targets)
            os.startfile(exe)


_LATEST_LAUNCH: dict[str, float] = {}          # resolved target -> monotime


def _scan_matches(resolution: dict, name: str) -> list[dict]:
    """Visible windows matching the resolved app (check token, name, target exe)."""
    wins = visible_windows()
    needles = [resolution.get("check") or "", name]
    tgt = os.path.splitext(resolution.get("target") or "")[0]
    if tgt:
        needles.append(tgt)
    seen: set[int] = set()
    out: list[dict] = []
    for needle in needles:
        if not needle:
            continue
        for w in find_windows_for(needle, wins):
            if w["hwnd"] not in seen:
                seen.add(w["hwnd"])
                out.append(w)
    return out


def _focus_best(hits: list[dict]) -> str:
    target = max(hits, key=lambda w: (not w["minimized"], w["active"]))
    try:
        if target["minimized"]:
            _restore_window(target["hwnd"])
        _activate_window(target["hwnd"])
    except Exception:
        pass
    return target["title"]


def open_app(name: str, timeout: float = 14.0) -> ToolResult:
    # ---- 1) ALREADY OPEN? scan running apps first — never spawn a duplicate.
    resolution = resolve(name)
    if resolution is None:
        return ToolResult(
            False,
            f"Could not find an application matching '{name}'. Report this to the user and do not "
            f"open any other app instead.",
            {})
    display = resolution.get("display") or name
    pre = _scan_matches(resolution, name)
    if pre:
        title = _focus_best(pre)
        time.sleep(0.4)
        act = next((w for w in visible_windows() if w["active"]), None)
        focused = act["title"] if act else title
        return ToolResult(
            True,
            f"'{display}' was already open (window \"{title}\"; screen now shows \"{focused}\"). "
            "Brought forward, NOT opened again. Do not call open_app for it again.",
            {"already_open": True, "window": title, "focused": focused,
             "pid": pre[0]["pid"], "display": display})

    # ---- 2) just launched seconds ago? don't spawn a second instance
    tkey = (resolution.get("target") or "") + "|" + display
    last = _LATEST_LAUNCH.get(tkey, 0.0)
    if 0 < time.monotonic() - last < 4.0:
        return ToolResult(
            True,
            f"'{display}' was launched moments ago and is still loading. Do NOT open it again. "
            "If you must confirm, call wait_window once instead.",
            {"in_flight": True, "display": display})

    # ---- 3) spawn + verify by scanning running apps afterwards
    before_wins = {(w["title"], w["pid"]) for w in visible_windows()}
    before_pids = set(psutil.pids())
    t0 = time.time()
    try:
        _spawn(resolution)
        _LATEST_LAUNCH[tkey] = time.monotonic()
    except Exception as e:
        return ToolResult(False, f"Failed to launch '{display}': {e}", {})
    time.sleep(0.6)                      # give the window server a moment
    deadline = time.time() + timeout
    settled_pid: int | None = None       # process-level match (fallback)
    opened_pid, opened_title = None, None
    while time.time() < deadline:
        for w in visible_windows():
            if (w["title"], w["pid"]) in before_wins:
                continue
            check = resolution.get("check") or name
            title_ok = tokens_match(w["title"], check) or tokens_match(w["title"], name)
            if title_ok:
                opened_pid, opened_title = w["pid"], w["title"]
                break
        if opened_pid is not None:
            break
        if settled_pid is None:
            for pid, pname in _pid_exe_map().items():
                if pid in before_pids:
                    continue
                base = os.path.splitext(pname or "")[0]
                if base and tokens_match(base, resolution.get("check") or name):
                    settled_pid = pid     # process up; keep waiting for the window
                    break
        time.sleep(0.3)
    dt = round(time.time() - t0, 1)
    if opened_pid is not None:
        return ToolResult(
            True,
            f"'{display}' is open and verified (window: \"{opened_title}\", pid {opened_pid}, {dt}s). "
            "It is running — do not open it again.",
            {"verified": True, "window": opened_title, "pid": opened_pid, "display": display},
        )
    if settled_pid is not None:
        return ToolResult(
            True,
            f"'{display}' is running (process pid {settled_pid}, {dt}s); "
            "no window title appeared yet — it may still be loading or run in the background. "
            "Do not open it again.",
            {"verified": True, "pid": settled_pid, "display": display},
        )
    # no NEW window: a single-instance app may have focused its OTHER instance
    post = _scan_matches(resolution, name)
    if post and any((w["title"], w["pid"]) in before_wins for w in post):
        title = _focus_best(post)
        return ToolResult(
            True,
            f"'{display}' was already running — its window \"{title}\" is now on screen. "
            "No new instance was started and no retry is needed.",
            {"already_open": True, "window": title, "pid": post[0]["pid"], "display": display},
        )
    return ToolResult(
        False,
        f"Could not launch '{display}' (no process or window appeared in {timeout:.0f}s). "
        "Report this to the user; do not retry the same call.",
        {"verified": False},
    )


# ------------------------- close / switch / list -------------------------

def close_app(name: str, _confirmed: bool = False) -> ToolResult:
    wins = find_windows_for(name)
    if not wins:
        procs = find_processes_for(name)
        if not procs:
            return ToolResult(False, f"No running application or process matches '{name}'.", {})
        pname = os.path.splitext((procs[0].info.get("name") or "?"))[0]
        if pname.lower() in PROTECTED_PROCESSES and not _confirmed:
            raise NeedConfirmation(
                f"Close '{pname}'",
                f"'{pname}' is a protected/system-ish process ({len(procs)} found). Closing it may disturb your session.",
            )
        killed = 0
        for p in procs[:8]:
            try:
                p.terminate()
                killed += 1
            except Exception:
                pass
        time.sleep(1.0)
        gone = all(not p.is_running() for p in procs[:8])
        return ToolResult(gone,
                          f"Terminated {killed} process(es) matching '{name}'."
                          + ("" if gone else " Some processes survived."),
                          {"terminated": killed})
    closed = 0
    for w in wins[:8]:
        if _close_window(w["hwnd"]):
            closed += 1
    time.sleep(1.2)
    remaining = [w for w in visible_windows() if w["title"] in {x["title"] for x in wins}]
    if remaining and not _confirmed:
        raise NeedConfirmation(
            f"Force close '{name}'",
            f"{len(remaining)} window(s) did not close with a normal close request. Force-terminate them?",
        )
    ok = not remaining
    return ToolResult(
        ok,
        (f"Closed {closed} window(s) of '{name}'. All are closed." if ok
         else f"Closed {closed} window(s) of '{name}'; {len(remaining)} still open."),
        {"closed": closed, "remaining": len(remaining)},
    )


def switch_app(name: str) -> ToolResult:
    wins = find_windows_for(name)
    if not wins:
        return ToolResult(False, f"No window matching '{name}' is open right now.", {})
    target = max(wins, key=lambda w: (not w["minimized"], w["active"]))
    try:
        if target["minimized"]:
            _restore_window(target["hwnd"])
        _activate_window(target["hwnd"])
    except Exception as e:
        return ToolResult(False, f"Could not switch: {e}", {})
    time.sleep(0.5)
    active = visible_windows()
    act = next((w for w in active if w["active"]), None)
    title = act["title"] if act else ""
    ok = tokens_match(title, name) or title in {w["title"] for w in wins}
    return ToolResult(ok,
                      (f"Switched to \"{title}\"." if ok else f"Requested focus on \"{target['title']}\" but the active window is now \"{title}\"."),
                      {"active": title})


def list_apps(limit: int = 14) -> ToolResult:
    wins = visible_windows()
    wins.sort(key=lambda w: (not w["active"], -((w["box"][2] or 0) * (w["box"][3] or 0))))
    lines = []
    for w in wins[:limit]:
        mark = "[ACTIVE] " if w["active"] else ("[MIN] " if w["minimized"] else "")
        b = w["box"]
        lines.append(f"{mark}\"{w['title']}\" pid={w['pid'] or '?'} {w['exe'] or '?'}")
    if not lines:
        return ToolResult(True, "No windows with titles are currently visible.", {})
    mem = psutil.virtual_memory()
    lines.append(f"[system] RAM used {mem.percent}% of {mem.total // (1 << 30)} GB")
    return ToolResult(True, "\n".join(lines), {"count": len(wins)})


def active_window() -> ToolResult:
    wins = visible_windows()
    act = next((w for w in wins if w["active"]), None)
    if act is None:
        return ToolResult(False, "No active window detected.", {})
    b = act["box"]
    return ToolResult(
        True,
        f"Active window \"{act['title']}\" ({act['exe'] or '?'}, pid {act['pid'] or '?'}) "
        f"at x={b[0]} y={b[1]} size={b[2]}x{b[3]}.",
        {"title": act["title"], "pid": act["pid"], "exe": act["exe"], "box": list(b)},
    )


def wait_window(name: str, timeout: float = 6.0) -> ToolResult:
    deadline = time.time() + max(0.5, min(timeout, 20.0))
    while time.time() < deadline:
        wins = find_windows_for(name)
        if wins:
            t = max(wins, key=lambda w: (not w["minimized"], w["active"]))
            return ToolResult(True, f"Window \"{t['title']}\" is present.", {"title": t["title"]})
        time.sleep(0.4)
    return ToolResult(False, f"No window matching '{name}' appeared within {timeout:.0f}s.", {})
