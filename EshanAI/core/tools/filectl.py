"""File tools: list/find/read/write/create/rename/move/delete — with safeguards.

- delete_file ALWAYS asks (recycle bin, never a permanent delete).
- Overwriting existing content, or writing into system areas, asks first.
- Moves that would overwrite, or land in system areas, ask first.
"""
from __future__ import annotations

import ctypes
import datetime
import os
from pathlib import Path

from .registry import ToolError, ToolResult, NeedConfirmation, ConfirmationBroker
from .. import paths

TEXT_EXTS = {".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml", ".yml", ".toml",
             ".ini", ".cfg", ".css", ".csv", ".tsv", ".html", ".htm", ".xml", ".log", ".sql",
             ".ps1", ".bat", ".sh", ".env", ".gitignore", ".rtf", ".srt", ".lrc", ".url", ".desktop"}
PROTECTED_DIRS = ("c:\\windows", "c:\\program files", "c:\\program files (x86)", "c:\\programdata")


def user_roots() -> list[Path]:
    home = Path.home()
    roots = []
    for name in ("Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos"):
        p = home / name
        if p.exists():
            roots.append(p)
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if onedrive:
        for name in ("Desktop", "Documents", "Downloads", "Pictures", "Music"):
            p = Path(onedrive) / name
            if p.exists() and p not in roots:
                roots.append(p)
    return roots


def _clean_path(p: str) -> Path:
    p = str(p).strip().strip('"').strip()
    if not p:
        raise ToolError("Empty path")
    if "%" in p:
        p = os.path.expandvars(p)
    p = os.path.expanduser(p)
    path = Path(p)
    if not path.is_absolute():
        raise ToolError(f"Path must be absolute, got: {p}")
    return path


def _is_protected(path: Path) -> bool:
    low = str(path).lower()
    return any(low.startswith(d) for d in PROTECTED_DIRS)


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else (f"{n:.0f} MB" if unit == "MB" else f"{n:.1f} GB")
        n /= 1024
    return f"{n:.1f} TB"


def _entry_line(p: Path) -> str:
    try:
        if p.is_dir():
            return f"[DIR]  {p.name}/"
        st = p.stat()
        mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
        return f"[FILE] {p.name}  ({_fmt_size(st.st_size)}, {mtime})"
    except OSError:
        return f"[?]    {p.name}"


def list_dir(path: str = "") -> ToolResult:
    if not str(path).strip() or str(path).strip() in ("~", "home"):
        path = str(Path.home())
    d = _clean_path(path)
    if not d.exists():
        return ToolResult(False, f"Folder not found: {str(d)!r}", {})
    if not d.is_dir():
        return ToolResult(False, f"Not a folder: {str(d)!r} (use read_text for files)", {})
    try:
        entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        return ToolResult(False, f"Access denied to folder: {str(d)!r}", {})
    dirs = [p for p in entries if p.is_dir()]
    files = [p for p in entries if not p.is_dir()]
    lines = [f"Folder: {str(d)}"]
    lines += [_entry_line(p) for p in dirs[:40] + []]
    if len(dirs) > 40:
        lines.append(f"… {len(dirs) - 40} more folders")
    for p in files[:max(0, 60 - min(len(dirs), 40))]:
        lines.append(_entry_line(p))
    if len(files) + len(dirs) > len(lines) - 1:
        lines.append(f"… ({len(dirs)} folders, {len(files)} files total)")
    else:
        lines.append(f"({len(dirs)} folders, {len(files)} files)")
    return ToolResult(True, "\n".join(lines), {"dir": str(d), "dirs": len(dirs), "files": len(files)})


def find_file(query: str, folder: str = "", limit: int = 25) -> ToolResult:
    query = str(query).strip()
    if len(query) < 2:
        raise ToolError("Query too short")
    q = query.lower()
    if folder and str(folder).strip() not in ("", "default", "user"):
        roots = [_clean_path(folder)]
        depth = 4
    else:
        roots = user_roots()
        depth = 2
    hits: list[str] = []
    for root in roots:
        base_len = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in
                           ("node_modules", "__pycache__", "venv", "appdata")]
            if len(Path(dirpath).parts) - base_len >= depth:
                dirnames.clear()
                continue
            for name in dirnames + filenames:
                if q in name.lower():
                    hits.append(os.path.join(dirpath, name))
                    break  # matched this dir already; avoid dupes below
            else:
                for name in filenames:
                    if q in name.lower() and len(hits) < limit * 4:
                        hits.append(os.path.join(dirpath, name))
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break
    # de-dup and sort newest first
    seen = set()
    uniq = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            uniq.append(h)
    out = []
    for h in uniq[:limit]:
        try:
            st = os.stat(h)
            kind = "DIR " if os.path.isdir(h) else "FILE"
            mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
            out.append(f"{kind} {h}  ({_fmt_size(st.st_size) if kind == 'FILE' else 'folder'}, {mtime})")
        except OSError:
            out.append(f"?    {h}")
    if not out:
        return ToolResult(False, f"No items matching '{query}' found in the default locations "
                                 f"(Desktop, Documents, Downloads...).", {})
    return ToolResult(True, f"Found {len(out)} match(es) for '{query}':\n" + "\n".join(out), {"hits": len(out)})


def read_text(path: str, _confirmed: bool = False) -> ToolResult:
    p = _clean_path(path)
    if not p.exists():
        return ToolResult(False, f"File not found: {str(p)!r}", {})
    if p.is_dir():
        return list_dir(str(p))
    if _is_protected(p):
        return ToolResult(False, "Reading files directly inside system folders is not allowed.", {})
    if p.suffix.lower() not in TEXT_EXTS:
        return ToolResult(False, f"'{p.name}' is not a text file type ({p.suffix or 'unknown'}). "
                                 "Text formats: txt, md, py, json, csv, log, yaml, ini, html, css, ps1, bat...", {})
    try:
        if p.stat().st_size > 2_000_000:
            return ToolResult(False, "File is larger than 2 MB; refusing to read into memory.", {})
        data = p.read_bytes()
    except PermissionError:
        return ToolResult(False, f"Access denied: {str(p)!r}", {})
    except OSError as e:
        return ToolResult(False, f"Could not read: {e}", {})
    text = None
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if text is None:
        return ToolResult(False, "Could not decode file as text.", {})
    limit = 4000
    if len(text) > limit:
        text = text[:limit] + f"\n… (truncated at {limit} of {len(text)} characters)"
    leading = "" if len(data) <= limit or len(text) < limit else ""
    return ToolResult(True, f"Content of {p.name}:\n{text}", {"path": str(p), "truncated": "…" in text})


def write_text(path: str, content: str, append: str = "no", _confirmed: bool = False) -> ToolResult:
    p = _clean_path(path)
    c = str(content)
    if _is_protected(p.parent):
        if not _confirmed:
            raise NeedConfirmation("Write into system area",
                                   f"'{p}' is inside a protected system folder.")
        return ToolResult(False, "Writing into system folders is not permitted even after confirmation.", {})
    if p.exists() and append not in ("yes", "true", "y", "1"):
        if not _confirmed:
            raise NeedConfirmation("Overwrite existing file",
                                   f"'{p}' already exists ({_fmt_size(p.stat().st_size)}). Overwrite its content?")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append in ("yes", "true", "y", "1") else "w"
        with open(p, mode, encoding="utf-8", errors="replace") as f:
            f.write(c)
    except OSError as e:
        return ToolResult(False, f"Write failed: {e}", {})
    verb = "Appended to" if mode == "a" else "Wrote"
    return ToolResult(True, f"{verb} {p} ({_fmt_size(len(c.encode('utf-8')))})", {"path": str(p)})


def new_folder(path: str) -> ToolResult:
    p = _clean_path(path)
    if _is_protected(p.parent):
        raise ToolError("Refusing to create folders inside system areas.")
    try:
        p.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        if p.is_dir():
            return ToolResult(False, f"Folder already exists: {p}", {})
        return ToolResult(False, f"A file with that name already exists: {p}", {})
    except OSError as e:
        return ToolResult(False, f"Could not create folder: {e}", {})
    return ToolResult(True, f"Created folder {p}", {"path": str(p)})


def rename(path: str, new_name: str) -> ToolResult:
    p = _clean_path(path)
    new_name = (new_name or "").strip().strip('"').strip()
    if not new_name or any(c in new_name for c in "\\/:*?\"<>|"):
        raise ToolError(f"Invalid new name: {new_name!r}")
    if not p.exists():
        return ToolResult(False, f"Item not found: {str(p)!r}", {})
    target = p.with_name(new_name)
    if target.exists():
        if not _conf_confirm():
            return ToolResult(False, "Target name already exists; nothing changed.", {})
        return ToolResult(False, "A file/folder with that name already exists — rename cancelled.", {})
    try:
        p.rename(target)
    except OSError as e:
        return ToolResult(False, f"Rename failed: {e}", {})
    return ToolResult(True, f"Renamed \"{p.name}\" -> \"{new_name}\"", {"path": str(target)})


def _conf_confirm() -> bool:
    # Simplified internal policy hook (rename-over-existence is refused outright).
    return False


def move_file(path: str, dest_folder: str, _confirmed: bool = False) -> ToolResult:
    p = _clean_path(path)
    if not p.exists():
        return ToolResult(False, f"Item not found: {str(p)!r}", {})
    d = _clean_path(dest_folder)
    if d.suffix and not d.is_dir() and d.exists():
        d = d.parent
    if not d.is_dir():
        # create destination folder on demand (safe, reversible)
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ToolResult(False, f"Destination folder could not be created: {e}", {})
    if _is_protected(d) and not _confirmed:
        raise NeedConfirmation("Move into system area", f"Move '{p}' into protected folder '{d}'?")
    target = d / p.name
    if target.exists() and not _confirmed:
        raise NeedConfirmation("Overwrite on move", f"'{target}' already exists. Overwrite it (original will be replaced)?")
    try:
        import shutil
        shutil.move(str(p), str(target))
    except OSError as e:
        return ToolResult(False, f"Move failed: {e}", {})
    return ToolResult(True, f"Moved {p.name} -> {target}", {"from": str(p), "to": str(target)})


def open_path(path: str) -> ToolResult:
    p = _clean_path(path)
    if not p.exists():
        return ToolResult(False, f"Item not found: {str(p)!r}", {})
    try:
        os.startfile(str(p))  # noqa: S606
    except OSError as e:
        return ToolResult(False, f"Could not open: {e}", {})
    return ToolResult(True, f"Opened \"{p.name}\" with its default application." if p.is_file()
                      else f"Opened folder in File Explorer: {p}", {"path": str(p)})


def open_url(url: str) -> ToolResult:
    u = str(url).strip()
    if not u.startswith(("http://", "https://")):
        if u.lower().startswith(("edge:", "about:", "ms-settings:")):
            pass
        else:
            u = "https://" + u
    import re
    if not re.match(r"^(https?://[^\s]+$|edge:|ms-settings:)", u):
        return ToolResult(False, f"Refusing to open invalid URL: {u!r}", {})
    try:
        os.startfile(u)  # noqa: S606 - default browser (user requested)
    except OSError as e:
        return ToolResult(False, f"Could not open URL: {e}", {})
    return ToolResult(True, f"Opened in default browser: {u}", {"url": u})


def _recycle_bin_delete(path_str: str) -> int:
    """Permanently safe delete: send to Recycle Bin via SHFileOperationW."""
    SHFO = ctypes.Structure
    import ctypes.wintypes as wt

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wt.HWND), ("wFunc", ctypes.c_uint),
                    ("pFrom", wt.LPCWSTR), ("pTo", wt.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wt.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wt.LPCWSTR)]

    op = SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    path = os.path.abspath(path_str) + "\x00\x00"
    op.pFrom = path
    op.fFlags = 0x40 | 0x10 | 0x04 | 0x400  # ALLOWUNDO|NOCONFIRMATION|SILENT|NOERRORUI
    op.hwnd = None
    op.pTo = None
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))


def delete_file(path: str, _confirmed: bool = False) -> ToolResult:
    p = _clean_path(path)  # note: registry confirm gate happens via NeedConfirmation below
    data_root = str(Path(paths.data_dir())).lower()
    if str(p).lower().startswith(data_root):
        return ToolResult(False, "Refusing to delete EshanAI's own internal data.", {})
    if not p.exists():
        return ToolResult(False, f"Item not found: {str(p)!r}", {})
    if _is_protected(p):
        return ToolResult(False, "Deleting files directly from system folders is not allowed.", {})
    if not _confirmed:
        kind = "folder (and everything inside)" if p.is_dir() else "file"
        raise NeedConfirmation(
            "Delete " + kind,
            f"Move \"{p}\" to the Recycle Bin? This is the only step; nothing is permanently erased.",
        )
    code = _recycle_bin_delete(str(p))
    if code == 0:
        return ToolResult(True, f"Moved \"{p.name}\" to the Recycle Bin.", {"deleted": str(p)})
    return ToolResult(False, f"Recycle operation failed (code {code:#x}). Nothing changed.", {})
