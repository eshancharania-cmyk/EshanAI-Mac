"""Ollama HTTP client for EshanAI.

Requests-based (installed), gives full control over streaming, format:"json",
keep_alive, and timeouts. All long-running work happens in worker threads.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, Generator

import requests

from . import config

OllamaNotRunning = requests.ConnectionError


class OllamaError(Exception):
    pass


class ModelNotInstalled(OllamaError):
    def __init__(self, model: str):
        super().__init__(f"Model not installed: {model}")
        self.model = model


class _Stop(IOError):
    """Raised internally when consuming is aborted by the caller/watchdog."""


def host() -> str:
    return config.get("server.host", "http://localhost:11434")


def is_running(timeout: float | None = None) -> bool:
    timeout = timeout or config.get("server.connect_timeout", 1.5)
    try:
        r = requests.get(f"{host()}/api/version", timeout=timeout)
        return r.ok
    except Exception:
        return False


def version() -> str | None:
    try:
        return requests.get(f"{host()}/api/version", timeout=3).json().get("version")
    except Exception:
        return None


def installed_models() -> list[dict]:
    try:
        r = requests.get(f"{host()}/api/tags", timeout=5)
        r.raise_for_status()
        return r.json().get("models", [])
    except Exception:
        return []


def model_names() -> list[str]:
    return [m.get("name", "") for m in installed_models()]


def is_installed(model: str) -> bool:
    names = model_names()
    return model in names or any(n.split(":")[0] == model.split(":")[0] and n == model for n in names)


def loaded() -> list[dict]:
    try:
        r = requests.get(f"{host()}/api/ps", timeout=5)
        return r.json().get("models", [])
    except Exception:
        return []


def loaded_names() -> list[str]:
    return [m.get("name", "") for m in loaded()]


def unload(model: str) -> bool:
    try:
        requests.post(f"{host()}/api/generate", json={"model": model, "keep_alive": 0}, timeout=10)
        return True
    except Exception:
        return False


def warm_model(model: str, keep_alive: str | None = None, timeout: float = 240,
               prime_system: str | None = None) -> bool:
    """Fully warm a model: load weights AND allocate the KV cache with the
    same num_ctx the app uses, so the FIRST real turn skips both one-time
    costs (~10-40s on this CPU). When prime_system is given, the tiny warm
    chat includes the REAL system prompt so its token prefix is already
    prompt-cached before the first user turn. Returns True when both
    succeeded."""
    ka = keep_alive if keep_alive is not None \
        else config.get("generation.keep_alive", "60m")
    try:
        requests.post(f"{host()}/api/generate",
                      json={"model": model, "keep_alive": ka}, timeout=timeout)
        names = loaded_names()
        if not any(name == model or name.split(":")[0] == model.split(":")[0]
                   for name in names):
            return False                       # never reported as loaded
        # tiny chat with the SAME num_ctx: forces the KV cache pool allocation
        msgs: list[dict] = [{"role": "user", "content": "OK"}]
        if prime_system:
            msgs = [{"role": "system", "content": prime_system},     # prefix cache
                    {"role": "user", "content": "OK"}]
        ctx = int(config.get("generation.num_ctx", 8192))
        payload = {"model": model, "keep_alive": ka, "stream": False,
                   "messages": msgs,
                   "options": {"num_ctx": ctx, "num_predict": 1, "temperature": 0.0}}
        if config.get("generation.think", False) is False:
            payload["think"] = False
        r = requests.post(f"{host()}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
        return True
    except Exception:
        return False


def pull(model: str, on_progress: Callable[[dict], None] | None = None) -> bool:
    """Stream a pull (used only from the guided-setup screen after user approval)."""
    try:
        r = requests.post(f"{host()}/api/pull", json={"model": model, "stream": True},
                          stream=True, timeout=3600)
        for line in r.iter_lines():
            if not line:
                continue
            try:
                d = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            if on_progress:
                on_progress(d)
            if d.get("error"):
                raise OllamaError(d["error"])
        return True
    except Exception:
        return False


class StreamingWatchdog:
    """Aborts a streaming response when no token arrives within `stall_s`."""

    def __init__(self, stall_s: float = 90.0):
        self.stall_s = stall_s
        self.last = time.time()
        self._triggered = threading.Event()

    def beat(self) -> None:
        self.last = time.time()

    def trigger(self) -> None:
        self._triggered.set()

    def check(self) -> bool:
        return self._triggered.is_set() or (time.time() - self.last > self.stall_s)


def chat_stream(
    model: str,
    messages: list[dict],
    options: dict | None = None,
    format_json: bool = True,
    keep_alive: str | None = None,
    stall_s: float = 120.0,
) -> Generator[dict, None, None]:
    """Yield dicts: {'token': str} for content deltas and a final {'done': meta}.

    Raises OllamaNotRunning on connection failure, ModelNotInstalled when the
    server reports the model is missing.
    """
    if not is_running():
        raise OllamaNotRunning("Ollama is not running")
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "keep_alive": keep_alive if keep_alive is not None else config.get("generation.keep_alive", "15m"),
        "options": options or {},
    }
    if config.get("generation.think", False) is False:
        payload["think"] = False        # models with a think channel: skip it
    if format_json:
        payload["format"] = "json"
    wd = StreamingWatchdog(stall_s)
    r = None
    try:
        r = requests.post(f"{host()}/api/chat", json=payload, stream=True, timeout=(5, None))
        if r.status_code == 404:
            raise ModelNotInstalled(model)
        r.raise_for_status()

        def watchdog():
            while not wd._triggered.is_set():
                if wd.check():
                    try:
                        r.close()
                    except Exception:
                        pass
                    return
                time.sleep(0.5)

        threading.Thread(target=watchdog, daemon=True).start()

        for raw in r.iter_lines():
            if not raw:
                continue
            try:
                d = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            msg = d.get("message") or {}
            c = msg.get("content") or ""
            if msg.get("thinking"):
                # ignore hidden reasoning tokens but keep the connection alive
                wd.beat()
            if c:
                wd.beat()
                yield {"token": c}
            if d.get("done"):
                wd.trigger()
                yield {"done": True,
                       "meta": {k: d.get(k) for k in ("total_duration", "load_duration",
                                                      "prompt_eval_duration", "eval_duration",
                                                      "eval_count", "prompt_eval_count")}}
                return
        wd.trigger()
        yield {"done": True, "meta": {}}
    except OllamaNotRunning:
        raise
    except requests.ConnectionError as e:
        if wd._triggered.is_set() and r is not None:
            raise OllamaError(f"model stalled (no tokens for {stall_s:.0f}s)")
        raise OllamaNotRunning(f"Ollama connection lost: {e}")
    except ModelNotInstalled:
        raise
    except requests.RequestException as e:
        raise OllamaError(f"Ollama request failed: {e}")
    finally:
        if r is not None:
            try:
                r.close()
            except Exception:
                pass


def chat_once(model: str, messages: list[dict], options: dict | None = None,
              format_json: bool = True, timeout: float = 180.0,
              images: list[str] | None = None) -> tuple[str, dict]:
    """Non-streaming chat; returns (content, meta)."""
    if not is_running():
        raise OllamaNotRunning("Ollama is not running")
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": options or {},
    }
    if config.get("generation.think", False) is False:
        payload["think"] = False
    if images:
        payload["messages"][-1]["images"] = images
    if format_json:
        payload["format"] = "json"
    r = requests.post(f"{host()}/api/chat", json=payload, timeout=timeout)
    if r.status_code == 404:
        raise ModelNotInstalled(model)
    r.raise_for_status()
    d = r.json()
    return (d.get("message") or {}).get("content", ""), {
        k: d.get(k) for k in ("total_duration", "load_duration", "prompt_eval_duration",
                              "eval_duration", "eval_count", "prompt_eval_count")
    }
