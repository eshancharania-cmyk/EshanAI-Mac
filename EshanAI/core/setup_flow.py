"""First-run onboarding flow for EshanAI — makes a brand-new PC ready.

A user copies EshanAI.exe somewhere (often off a USB stick) onto a
fresh Windows machine. The app itself is self-contained (UI, memory,
both neural voices), but three things belong to the WORKING SET:

  1. the Ollama engine (the local LLM runner) — download + silent
     install if missing, or just start it if installed;
  2. the brain model (config models.main, default huihui_ai/
     qwen3-abliterated:4b, ~2.6 GB) — one ollama pull with live %;
  3. the offline STT voice (whisper tiny.en int8, ~75 MB) — one
     download into the EshanAI data dir, cached forever after.

This module runs those steps sequentially in a background thread and
emits plain dict events; the UI marshals them onto the drawing thread
through the app's normal event queue. Every step is independent and
failure-tolerant: a failed step is reported in one line and the app
keeps working; the next boot re-offers setup while setup.done is unset.

Env overrides for probes/tests:
  ESHANAI_SKIP_ENGINE=1   skip steps 1-2
  ESHANAI_SKIP_VOICE=1    skip step 3
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import core.ollama_client as oc
from core import config

_IS_WIN = os.name == "nt"
_IS_MAC = sys.platform == "darwin"
_MAC_URL = "https://ollama.com/download/Ollama-darwin.zip"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if _IS_WIN else 0

OLLAMA_URL = "https://ollama.com/download/OllamaSetup.exe"


def needs_setup() -> bool:
    "True until setup.done is saved in settings."
    try:
        return not bool(config.get("setup.done", False))
    except Exception:
        return True


class SetupFlow:
    """Runs the bootstrap steps; calls on_event(dict) from its thread."""

    def __init__(self, on_event):
        self._cb = on_event

    # ---------- plumbing ----------
    def _e(self, key, ev, **kw):
        try:
            self._cb({"ev": ev, "key": key, **kw})
        except Exception:
            pass

    def engine_running(self) -> bool:
        try:
            return bool(oc.is_running(2.0))
        except Exception:
            return False

    @staticmethod
    def _engine_exe() -> str | None:
        cands = [shutil.which("ollama")]
        if _IS_MAC:
            home = Path.home()
            cands += [str(home / "Applications" / "Ollama.app" / "Contents"
                          / "Resources" / "Ollama"),
                      str(Path("/Applications/Ollama.app/Contents/Resources"
                               "/Ollama"))]
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            cands.append(str(Path(local) / "Programs" / "Ollama"
                             / "ollama.exe"))
        for c in cands:
            if c and Path(c).is_file():
                return c
        return None

    def _launch_engine(self):
        exe = self._engine_exe()
        if not exe:
            return False
        try:
            subprocess.Popen(
                [exe, "serve"],
                creationflags=_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return False
        t0 = time.time()
        while time.time() - t0 < 25.0:
            if self.engine_running():
                return True
            time.sleep(0.5)
        return False

    # ---------- step 1: engine ----------
    def step_engine(self) -> bool:
        key = "engine"
        self._e(key, "start", label="Ollama engine")
        if self.engine_running():
            self._e(key, "state", ok=True, note="already running")
            return True
        if self._engine_exe():
            ok = self._launch_engine()
            self._e(key, "state", ok=ok,
                    note="started" if ok else "installed but not responding")
            return ok
        self._e(key, "progress", frac=None, text="downloading installer …")
        dest = Path(os.environ.get("TEMP", "."))
        if not _IS_WIN:
            dest = Path("/tmp") if Path("/tmp").exists() else dest
        dest = dest / ("Ollama-darwin.zip" if _IS_MAC
                       else "OllamaSetup-EshanAI.exe")
        url = _MAC_URL if _IS_MAC else OLLAMA_URL
        try:
            import requests
            with requests.get(url, stream=True, timeout=(15, 900)) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0)
                got = 0
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=262144):
                        f.write(chunk)
                        got += len(chunk)
                        if total:
                            self._e(key, "progress", frac=got / total,
                                    text=f"downloading "
                                         f"{got // (1024 * 1024)} / "
                                         f"{total // (1024 * 1024)} MB")
            self._e(key, "progress", frac=1.0, text="installing silently …")
            if _IS_WIN:
                subprocess.Popen(
                    [str(dest), "/VERYSILENT", "/NORESTART"],
                    creationflags=_NO_WINDOW)
            else:
                # mac: the zip contains Ollama.app; drop it in ~/Applications
                import zipfile
                apps = Path.home() / "Applications"
                try:
                    apps.mkdir(exist_ok=True)
                except Exception:
                    pass
                with zipfile.ZipFile(dest) as z:
                    for name in z.namelist():
                        try:
                            z.extract(name, apps)
                        except Exception:
                            pass
            t0 = time.time()
            while time.time() - t0 < 420.0:
                if self._engine_exe():
                    break
                time.sleep(2.0)
            ok = self._launch_engine()
            self._e(key, "state", ok=ok,
                    note="installed & running" if ok
                    else "installed but not responding")
            return ok
        except Exception as e:
            self._e(key, "state", ok=False,
                    note=f"download failed ({e.__class__.__name__}) — "
                         "needs internet")
            return False

    # ---------- step 2: brain model ----------
    def step_brain(self) -> bool:
        key = "brain"
        model = str(config.get("models.main",
                               "huihui_ai/qwen3-abliterated:4b"))
        label = model.split(":")[0].split("/")[-1]
        self._e(key, "start", label=f"Brain model · {label}")
        try:
            if self.engine_running() and oc.is_installed(model):
                self._e(key, "state", ok=True, note="already downloaded")
                return True
        except Exception:
            pass
        if not self.engine_running():
            self._e(key, "state", ok=False, note="skipped (engine down)")
            return False
        try:
            import requests
            with requests.post(f"{oc.host()}/api/pull",
                               json={"model": model, "stream": True},
                               stream=True, timeout=(15, 3600)) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    d = json.loads(line.decode("utf-8", "replace"))
                    tot = d.get("total") or 0
                    got = d.get("completed") or 0
                    if tot and got:
                        self._e(key, "progress", frac=got / tot,
                                text=f"{got >> 20} / {tot >> 20} MB")
                    else:
                        self._e(key, "progress", frac=None,
                                text=str(d.get("status", ""))[:48])
                    if d.get("error"):
                        raise RuntimeError(str(d.get("error"))[:64])
            self._e(key, "state", ok=True, note="downloaded")
            return True
        except Exception as e:
            self._e(key, "state", ok=False,
                    note=f"download failed ({e.__class__.__name__}) — "
                         "needs internet")
            return False

    # ---------- step 3: offline voice ----------
    def step_voice(self) -> bool:
        key = "voice"
        self._e(key, "start", label="Offline voice (whisper)")
        if os.environ.get("ESHANAI_SKIP_VOICE") == "1":
            self._e(key, "state", ok=True, note="skipped (probe)")
            return True
        try:
            from core import speech
            if speech.stt_ready():
                self._e(key, "state", ok=True, note="already ready")
                return True
        except Exception:
            pass
        self._e(key, "progress", frac=None, text="downloading ~75 MB …")
        done = threading.Event()
        result = {"ok": False, "msg": ""}

        def _spin():
            try:
                from core import speech
                speech._whisper_model()
                result["ok"] = True
            except Exception as e:
                result["msg"] = str(e)[:64]
            done.set()

        threading.Thread(target=_spin, daemon=True).start()
        done.wait(900.0)
        if result["ok"]:
            self._e(key, "state", ok=True, note="ready")
            return True
        self._e(key, "state", ok=False,
                note=(f"download failed ({result['msg']}) — "
                      "needs internet"))
        return False

    # ---------- runner ----------
    def run(self):
        if os.environ.get("ESHANAI_SKIP_ENGINE") == "1":
            for k, lab in (("engine", "Ollama engine"),
                           ("brain", "Brain model")):
                self._e(k, "start", label=lab)
                self._e(k, "state", ok=True, note="skipped (probe)")
        else:
            self.step_engine()
            self.step_brain()
        self.step_voice()
        self._e("flow", "finished")
