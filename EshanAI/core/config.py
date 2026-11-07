"""Settings persistence for EshanAI. Simple defaults + JSON overwrite + validation."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import paths

_defaults: dict = {
    "created": None,                       # filled on first save
    "models": {
        "main": "huihui_ai/qwen3-abliterated:4b",
        "fallback": "qwen2.5:1.5b",
    },
    "generation": {
        "num_ctx": 8192,
        "temperature": 0.5,
        "top_p": 0.9,
        "num_predict": 512,
        "keep_alive": "60m",
        "stream": True,
        "think": False,                    # qwen3: no reasoning channel (fast turns)
    },
    "agent": {
        "max_tool_steps": 8,
        "time_budget_s": 120,
        "auto_title": True,
        "inject_memories": True,
        "max_memories_per_turn": 5,
    },
    "server": {"host": "http://localhost:11434", "connect_timeout": 1.5},
    "confirmations": {
        "delete_file": True,          # always on, cannot be disabled
        "overwrite_write": True,
        "close_protected": True,
    },
    "ui": {
        "geometry": [1240, 800],
        "font_scale": 1.0,
        "sidebar_width": 300,
        "show_model_chip": True,
        "send_on_enter": True,
    },
}

_lock = threading.RLock()
_cache: dict | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        p: Path = paths.settings_path()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        _cache = _deep_merge(_defaults, raw if isinstance(raw, dict) else {})
        if not _cache.get("created"):
            _cache["created"] = time.time()
            save()
        return _cache


def save() -> None:
    with _lock:
        cfg = _cache if _cache is not None else load()
        tmp = paths.settings_path().with_suffix(".tmp")
        paths.data_dir()  # ensure dir exists
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        import os
        os.replace(tmp, paths.settings_path())


def get(key: str, default=None):
    """get('generation.num_ctx')"""
    node: dict = load()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set(key: str, value) -> None:
    with _lock:
        cfg = load()
        parts = key.split(".")
        node = cfg
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        save()


def reset(main_model: str, fallback: str) -> None:
    """Not destructive: only model update helper used by guided setup."""
    set("models.main", main_model)
    set("models.fallback", fallback)
