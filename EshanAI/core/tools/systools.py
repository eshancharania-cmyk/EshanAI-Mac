"""System-information tool and persistent-memory (remember) tool."""
from __future__ import annotations

import platform
import socket
import time

import psutil

from .registry import ToolError, ToolResult
from .. import memory, db


def system_info() -> ToolResult:
    try:
        uname = platform.uname()
        cpu = platform.processor() or uname.processor
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\") if platform.system() == "Windows" else psutil.disk_usage("/")
        load = f"{uname.system} {uname.release}|{uname.version.split('(')[0].strip()}"
        battery = psutil.sensors_battery()
        bat = f" Battery {round(battery.percent)}% ({'plugged' if battery.power_plugged else 'on battery'})." if battery else ""
        boot = psutil.boot_time()
        hours = (time.time() - boot) / 3600
        info = [
            f"{load}, {uname.machine}",
            f"CPU: {cpu}",
            f"CPU cores: {psutil.cpu_count(logical=False)} physical / {psutil.cpu_count(logical=True)} logical "
            f"(load {psutil.cpu_percent(interval=0.2):.0f}%)",
            f"RAM: {round(mem.total / (1 << 30), 1)} GB total, {round(mem.used / (1 << 30), 1)} GB used ({mem.percent}%), "
            f"{round(mem.available / (1 << 30), 1)} GB available",
            f"Disk C: {round(disk.total / (1 << 30), 0):.0f} GB total, {round(disk.free / (1 << 30), 0):.0f} GB free",
            f"Uptime: {hours:.1f}h. Host: {socket.gethostname()}. User: {psutil.Process().username()}.{bat}"
        ]
        gpu = _gpu_name()
        if gpu:
            info.append(f"GPU: {gpu}")
        return ToolResult(True, "\n".join(info), {})
    except Exception as e:
        raise ToolError(f"system_info failed: {e}") from e


def _gpu_name() -> str | None:
    try:
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout.strip()
        return out or None
    except Exception:
        return None


def remember(fact: str, _confirmed: bool = False) -> ToolResult:
    fact = (fact or "").strip()
    if len(fact) < 3 or len(fact.split()) < 2:
        raise ToolError("That note is too short to remember (write it as a small sentence).")
    mem_id = memory.save_fact(fact, source="agent")
    if mem_id is None:
        return ToolResult(False, "Could not save that memory (empty or too short).", {})
    total = len(db.get_memories(limit=1000))
    return ToolResult(True, f"Saved to persistent memory: \"{fact}\". You now have {total} stored item(s).",
                      {"memory_id": mem_id})


def memory_search(query: str) -> ToolResult:
    """Find stored memories matching a fragment (used to recall specifics)."""
    q = str(query).strip()
    if len(q) < 2:
        raise ToolError("Give a word or two to search memory.")
    hits = db.find_memories(q, limit=12)
    if not hits:
        return ToolResult(False, f"No stored memories mention '{q}'.", {})
    lines = [f"Memory results for '{q}':"]
    for h in hits:
        lines.append(f"- {h['content']}")
    return ToolResult(True, "\n".join(lines), {"count": len(hits)})
