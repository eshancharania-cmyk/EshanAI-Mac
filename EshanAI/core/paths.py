"""Application data locations and portable-mode detection for EshanAI."""
from __future__ import annotations

import os
import sys
from pathlib import Path

AppName = "EshanAI"


def _with_flag(searched: list[Path]) -> Path | None:
    """Return dir containing portable.flag, else None."""
    for base in searched:
        if base and (base / "portable.flag").exists():
            return base
    return None


def exe_dir() -> Path:
    """Directory of the packaged exe, or of this source file's ancestors."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent  # EshanAI/ source root


def candidates() -> list[Path]:
    out = [exe_dir()]
    cwd = Path.cwd()
    if cwd not in out:
        out.append(cwd)
    return [p for p in out if p]


def is_portable() -> bool:
    return _with_flag(candidates()) is not None


def data_dir() -> Path:
    """Where settings.json / eshanai.db / logs live.

    Order: ESHANAI_DATA env  >  portable.flag next to exe/cwd  >  %LOCALAPPDATA%\\EshanAI
    """
    env = os.environ.get("ESHANAI_DATA")
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    flag = _with_flag(candidates())
    if flag:
        p = flag / "EshanAI_Data"
        p.mkdir(parents=True, exist_ok=True)
        return p
    if sys.platform == "darwin":                 # mac: standard per-user app dir
        p = (Path.home() / "Library" / "Application Support" / AppName)
        p.mkdir(parents=True, exist_ok=True)
        return p
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    p = Path(base) / AppName
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "eshanai.db"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def speech_dir() -> Path:
    """Local voice models (whisper weights etc.) — never sent anywhere."""
    p = data_dir() / "speech"
    p.mkdir(parents=True, exist_ok=True)
    return p
