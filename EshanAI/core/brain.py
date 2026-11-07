"""EshanAI's agent loop.

One user turn = a controlled loop:
  model (the configured one, JSON protocol) -> tool? -> execute trusted tool -> feed result
  back -> repeat until a {"reply": ...} final answer or the budget stops us.

Streaming: the model writes a single JSON object; a hand-written incremental
scanner decodes the top-level "reply" string as it arrives so the UI can stream
the answer (and show tool calls) in real time.
"""
from __future__ import annotations

import json
import re
import threading
import time
import traceback
from pathlib import Path

from . import config, db, memory
from . import ollama_client as oc
from .prompts import build_system_prompt, auto_title_prompt
from .tools import REGISTRY, NeedConfirmation, ConfirmationBroker
from .tools import appctl as _appctl

MAX_REPAIRS = 2
HISTORY_CAP = 20

_TOOLMSG_RE = None


# ---------------------------------------------------------------- streaming scanner

class PartialReply:
    """Incrementally decodes the top-level `reply` string of the JSON stream."""

    ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}

    def __init__(self) -> None:
        self.buf = ""
        self.sent = 0
        self.tool: str | None = None

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        return self._walk()

    def _scan_string(self, s: str, i: int) -> tuple[str, int | None]:
        """s[i] == '"'. Returns (decoded, end_index) or (partial, None) at stream cut."""
        out = []
        j = i + 1
        n = len(s)
        while j < n:
            c = s[j]
            if c == "\\":
                if j + 1 >= n:
                    return "".join(out), None           # cut escape at stream end
                c2 = s[j + 1]
                if c2 == "u":
                    if j + 6 > n:
                        return "".join(out), None       # cut unicode escape
                    try:
                        out.append(chr(int(s[j + 2:j + 6], 16)))
                    except ValueError:
                        return "".join(out), None
                    j += 6
                    continue
                out.append(self.ESCAPES.get(c2, c2))
                j += 2
                continue
            if c == '"':
                return "".join(out), j + 1
            out.append(c)
            j += 1
        return "".join(out), None                        # incomplete string

    def _walk(self) -> str:
        """Container-stack state machine over the JSON stream. Returns visible delta."""
        s = self.buf
        n = len(s)
        i = 0
        reply_partial: str | None = None
        stack: list[str] = []            # container kinds: 'dict' | 'list'
        mode = "strong"                  # strong(first '{') | key | colon | value
        cur_key: str | None = None
        while i < n:
            c = s[i]
            if c in " \t\r\n":
                i += 1
                continue
            if mode == "strong":
                if c == "{":
                    stack.append("dict")
                    mode, cur_key = "key", None
                i += 1
                continue
            if c == '"':
                value, end = self._scan_string(s, i)
                if end is None:
                    if len(stack) == 1 and cur_key == "reply" and mode in ("value", "key"):
                        reply_partial = value
                    break
                kind = stack[-1] if stack else "list"
                if kind == "dict" and len(stack) == 1:
                    if mode == "key":                     # a top-level key
                        cur_key = value
                    elif cur_key == "reply":
                        reply_partial = value
                    elif cur_key == "tool":
                        self.tool = value
                elif mode == "key":
                    cur_key = value                       # nested key (depth>1)
                i = end
                if mode == "key" and kind == "dict":
                    mode = "colon"
                else:
                    cur_key = None
                    top = stack[-1] if stack else "list"
                    mode = "key" if top == "dict" else "value"
                continue
            if c == "{":
                stack.append("dict")
                mode = "key"
                i += 1
                continue
            if c == "[":
                stack.append("list")
                mode = "value"
                i += 1
                continue
            if c in "}]":
                if stack:
                    stack.pop()
                mode = "value"
                i += 1
                continue
            if c == ",":
                kind = stack[-1] if stack else "list"
                mode = "key" if kind == "dict" else "value"
                cur_key = None
                i += 1
                continue
            if c == ":":
                mode = "value"
                i += 1
                continue
            i += 1                                       # numbers/true/false/null
        delta = ""
        if reply_partial is not None and len(reply_partial) > self.sent:
            delta = reply_partial[self.sent:]
            self.sent = len(reply_partial)
        return delta


def _extract_object(text: str):
    """Return the first balanced {...} object parsed as JSON, tolerating noise/fences."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty output")
    text = re.sub(r"^\s*```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text).strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("not an object")
    n = len(text)
    i = start
    depth = 0
    in_str = False
    esc = False
    while i < n:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
        i += 1
    raise ValueError("unbalanced object")


# ---------------------------------------------------------------- brain

class Brain:
    def __init__(self, broker: ConfirmationBroker | None = None):
        self.broker = broker or ConfirmationBroker()
        self._system: str | None = None

    def system_prompt(self) -> str:
        if self._system is None:
            self._system = build_system_prompt(REGISTRY.prompt_lines())
        return self._system

    # ------------------------------------------------------------- history

    def build_messages(self, conv_id: int, new_user_content: str) -> list[dict]:
        rows = db.get_messages(conv_id)
        rows = rows[-HISTORY_CAP:]
        msgs: list[dict] = [{"role": "system", "content": self.system_prompt()}]
        for row in rows:
            if row["role"] == "user":
                content = row["content"]
                meta = row.get("meta") or {}
                if meta.get("sent_content"):        # memory prefix from a previous turn
                    content = meta["sent_content"]
                if row["id"] == rows[-1]["id"]:
                    content = new_user_content      # the live turn uses fresh prefix
                msgs.append({"role": "user", "content": content})
            elif row["role"] == "assistant":
                msgs.append({"role": "assistant", "content": row["content"]})
            elif row["role"] == "tool":
                meta = row.get("meta") or {}
                msgs.append({"role": "tool",
                             "content": json.dumps({"tool": meta.get("tool"), "status": meta.get("status"),
                                                    "result": meta.get("result")}, ensure_ascii=False)})
            # event rows are skipped (UI-only)
        # drop leading non-user strangeness
        while msgs and msgs[1:2] and msgs[1]["role"] == "tool":
            msgs.pop(1)
        return msgs

    # ------------------------------------------------------------- the loop

    def run_turn(self, conv_id: int, text: str, on_event) -> None:
        """Drive one full user turn. on_event(dict) receives progress events."""
        t0 = time.time()
        db.add_message(conv_id, "user", text)
        cfg_tool = config.get("agent", {})

        notes = memory.relevant(text, limit=cfg_tool.get("max_memories_per_turn", 5)) \
            if cfg_tool.get("inject_memories", True) else []
        sent_content = text
        if notes:
            sent_content = "<context from EshanAI's memory>\n- " + "\n- ".join(notes) + \
                           "\n<end context. Use it when relevant; don't mention it explicitly>\n\n" + text

        msgs = self.build_messages(conv_id, sent_content)
        # persist how the user turn was sent (cache stability across turns)
        _persist_sent_prefix(conv_id, sent_content, db.get_messages(conv_id)[-1]["id"])

        gen = config.get("generation", {})
        opts = {"num_ctx": gen.get("num_ctx", 8192),
                "temperature": gen.get("temperature", 0.6),
                "top_p": gen.get("top_p", 0.9),
                "num_predict": gen.get("num_predict", 512)}
        keep_alive = gen.get("keep_alive", "15m")

        main = config.get("models.main")
        fallback = config.get("models.fallback")
        model = main
        fallback_used = False

        used_remember = False
        steps = 0
        final = None
        max_steps = int(cfg_tool.get("max_tool_steps", 8))
        seen_calls: dict[str, int] = {}      # canonical "tool(args)" -> times used
        canon_ok: dict[str, bool] = {}       # canonical call -> did it succeed?
        name_run: dict[str, int] = {}        # consecutive same-name calls
        last_name: str | None = None
        receipts: list[str] = []             # "tool: ok/error — summary" for honest wrap-ups

        if not oc.is_running():
            msg = "Ollama is not running (checked just now). Start Ollama and try again."
            on_event({"type": "error", "message": msg})
            return

        def doc_fallback(reason: str) -> bool:
            nonlocal model, fallback_used
            if fallback_used:
                return False
            fallback_used = True
            model = fallback
            db.add_event("fallback", reason)
            on_event({"type": "fallback", "model": fallback, "reason": reason})
            return True

        try:
            while True:
                steps += 1
                budget = float(cfg_tool.get("time_budget_s", 120))
                exceeded = time.time() - t0 > budget
                if (steps > max_steps + 5 or time.time() - t0 > budget + 60) and final is None:
                    # absolute safeguard against an endless tool cycle
                    tried = "; ".join(receipts[:6]) or "no tool actions were completed"
                    final = (f"I stopped this task to avoid repeating failed steps. Actions reported this "
                             f"turn: {tried}.")
                    on_event({"type": "status", "text": "Stopped — repeating actions blocked"})
                    break
                if exceeded and steps > 1:
                    msgs.append({"role": "user", "content": "<system budget reached: no more tool calls. "
                                                            "Reply now with one JSON object using reply, "
                                                            "summarising what happened, honestly.>"})
                    on_event({"type": "status", "text": "Time budget reached — wrapping up"})

                partial = PartialReply()
                out = ""
                on_event({"type": "status", "text": f"Thinking ({model.split('/')[-1]})…"})
                try:
                    for ev in oc.chat_stream(model, msgs, options=opts, format_json=True,
                                             keep_alive=keep_alive):
                        if "token" in ev:
                            out += ev["token"]
                            delta = partial.feed(ev["token"])
                            if delta:
                                on_event({"type": "delta", "text": delta})
                        elif "done" in ev:
                            meta = ev.get("meta") or {}
                            tt = round((meta.get("eval_duration") or 0) / 1e9, 1)
                            on_event({"type": "status", "text": f"Model used {tt}s generating"})
                            break
                except oc.OllamaNotRunning as e:
                    on_event({"type": "error", "message": f"Ollama is not responding: {e}"})
                    return
                except oc.ModelNotInstalled:
                    if model == main:
                        doc_fallback(f"Main model {main} not installed")
                        continue
                    on_event({"type": "error", "message": f"Model {model} is not installed."})
                    return

                # ---- finalize response JSON
                obj = None
                repair = None
                try:
                    obj = _extract_object(out)
                except Exception as e:
                    repair = f"invalid JSON: {e}"

                if obj is not None and not isinstance(obj, dict):
                    repair = "JSON was not an object"
                    obj = None

                if obj is None:
                    _fail_note = (f"(The previous response was invalid: {repair}. Remember: output EXACTLY ONE "
                                  f"JSON object — reply for answers, or tool+args for actions.)")
                    db.add_event("repair", _fail_note)
                    msgs.append({"role": "assistant", "content": out or ""})
                    msgs.append({"role": "user", "content": _fail_note})
                    if repair and steps > MAX_REPAIRS and not fallback_used:
                        doc_fallback(f"Repeated malformed JSON ({repair})")
                        continue
                    if steps > MAX_REPAIRS + 2:
                        on_event({"type": "error", "message": "Model repeatedly failed to answer in the required format."})
                        return
                    continue

                tool_name = obj.get("tool")
                if isinstance(tool_name, dict):      # model wrote {"tool": {name, args}}
                    tool_name, tool_args = tool_name.get("name"), tool_name.get("args", obj)
                else:
                    tool_args = obj.get("args")
                if tool_name and not isinstance(tool_args, dict):
                    tool_args = {}
                if tool_name:
                    tool_name = re.sub(r"\s+", "", str(tool_name)).lower()   # normalize typos
                    if not tool_args:
                        tool_args = {}
                    canon = f"{tool_name}({json.dumps(tool_args, ensure_ascii=False, sort_keys=True)})"
                    repeated = seen_calls.get(canon, 0)
                    seen_calls[canon] = repeated + 1
                    consec = (name_run.get(tool_name, 0) + 1) if tool_name == last_name else 1
                    name_run[tool_name] = consec
                    last_name = tool_name
                    prev_ok = canon_ok.get(canon)
                    if repeated >= 2 or (repeated >= 1 and prev_ok) or consec >= 4 \
                            or steps > max_steps + 1:
                        # hard stop: force a final answer instead of another tool cycle
                        on_event({"type": "status", "text": "Step limit reached — wrapping up"})
                        msgs.append({"role": "assistant", "content": json.dumps(obj, ensure_ascii=False)})
                        msgs.append({"role": "tool",
                                     "content": json.dumps({
                                         "tool": tool_name, "status": "error",
                                         "result": "STOP: that action was already carried out earlier this turn "
                                                   "and its result is in front of you. Do not call more tools. "
                                                   "Reply NOW with one final answer in a single JSON object "
                                                   "{\"reply\": ...} describing what actually happened."
                                     }, ensure_ascii=False)})
                        continue
                    args_preview = json.dumps(tool_args, ensure_ascii=False)[:180]
                    on_event({"type": "tool_call", "tool": tool_name, "args_preview": args_preview,
                              "step": steps})
                    result = REGISTRY.execute(tool_name, tool_args, self.broker)
                    canon_ok[canon] = bool(result.ok)
                    receipts.append(f"{tool_name}: {'ok' if result.ok else 'ERROR'} — {result.output[:70]}")
                    used_remember |= (tool_name == "remember" and result.ok)
                    db.add_message(conv_id, "tool", "",
                                   meta={"tool": tool_name, "args_preview": args_preview,
                                         "status": "ok" if result.ok else "error",
                                         "result": result.output})
                    on_event({"type": "tool_result", "tool": tool_name,
                              "ok": result.ok, "result": result.output})
                    msgs.append({"role": "assistant", "content": json.dumps(obj, ensure_ascii=False)})
                    msgs.append({"role": "tool",
                                 "content": json.dumps({"tool": tool_name, "status": "ok" if result.ok else "error",
                                                        "result": result.output}, ensure_ascii=False)})
                    time.sleep(0.05)
                    continue

                final = str(obj.get("reply") or "").strip()
                if not final:
                    msgs.append({"role": "assistant", "content": out})
                    msgs.append({"role": "user",
                                 "content": "Use reply (an answer for me) or tool (an action). Empty replies are useless."})
                    continue
                break
        except Exception as e:
            db.add_event("error", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}")
            on_event({"type": "error", "message": f"Unexpected problem: {e}"})
            return

        # ---- store final assistant message
        db.add_message(conv_id, "assistant", final, model=model)
        elapsed = round(time.time() - t0, 1)
        on_event({"type": "assistant_final", "content": final, "model": model,
                  "fallback": fallback_used, "elapsed": elapsed, "steps": steps})

        # ---- auto-save if user asked to remember but the model forgot
        save_req = memory.looks_like_save_request(text)
        if save_req and not used_remember and not fallback_used:
            fact = memory.fact_from_save_request(text)
            if fact:
                memory.save_fact(fact, source="auto")
                db.add_event("auto_memory", fact)
                on_event({"type": "status", "text": f"Auto-saved to memory: \"{fact}\""})
                db.add_message(conv_id, "tool", "",
                               meta={"tool": "remember (auto)", "args_preview": fact[:160],
                                     "status": "ok", "result": f"Saved: {fact}"})

        # ---- auto title (first quick exchange)
        self.maybe_auto_title(conv_id)

    # ---------------------------------------------------------------- titles

    def maybe_auto_title(self, conv_id: int) -> None:
        """Queue a title generation (serialized by the worker, retried next turn on miss)."""
        if not config.get("agent.auto_title", True):
            return
        conv = db.get_conversation(conv_id)
        if not conv or conv["title_generated"]:
            return
        try:
            rows = db.get_messages(conv_id)
            users = [m["content"] for m in rows if m["role"] == "user"]
            if users:
                _enqueue_title(conv_id, users[0][:250])
        except Exception:
            pass


TITLE_EVENTS: list[tuple[int, str]] = []

# ---- serialized title generation: one worker, no drops, retriable misses ----
import queue as _queue

_title_queue: "_queue.Queue[tuple[int, str]]" = _queue.Queue()
_title_pending: set[int] = set()
_title_lock = threading.Lock()
_worker_started = threading.Event()


def _title_worker() -> None:
    while True:
        conv_id, user_text = _title_queue.get()
        try:
            _generate_title(conv_id, user_text)
        except Exception:
            pass
        finally:
            with _title_lock:
                _title_pending.discard(conv_id)
            _title_queue.task_done()


def _generate_title(conv_id: int, user_text: str) -> None:
    """Titles use the small fallback model on purpose: they must never
    evict the main model's KV cache between turns (that cost ~40s extra
    prompt eval per new conversation when titles shared the main model)."""
    rows = db.get_messages(conv_id)
    helpers = [m["content"] for m in rows if m["role"] == "assistant"]
    prompt = auto_title_prompt(user_text, helpers[0] if helpers else "")
    title_model = config.get("models.fallback")
    title = ""
    for attempt in range(2):        # one retry if the model returns junk
        try:
            content, _ = oc.chat_once(
                title_model, prompt,
                options={"num_ctx": 1024, "num_predict": 20, "temperature": 0.2},
                format_json=True, timeout=60)
            raw = json.loads(content).get("reply", "") if content else ""
            title = str(raw).strip().strip('"').strip("'").strip()[:64]
            if title.lower() not in ("", "reply", "title"):
                break
        except Exception:
            title = ""
    if title:
        db.set_title(conv_id, title, generated=True)
        TITLE_EVENTS.append((conv_id, title))


def _enqueue_title(conv_id: int, user_text: str) -> None:
    if not _worker_started.is_set():
        with _title_lock:
            if not _worker_started.is_set():
                threading.Thread(target=_title_worker, daemon=True).start()
                _worker_started.set()
    with _title_lock:
        if conv_id in _title_pending:
            return
        _title_pending.add(conv_id)
    _title_queue.put((conv_id, user_text))


def wait_titles_done(timeout: float = 30.0) -> bool:
    """Block until every queued title is generated (used by tests/UI shutdown)."""
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        if _title_queue.unfinished_tasks == 0:
            return True
        _t.sleep(0.2)
    return False


def _persist_sent_prefix(conv_id: int, sent: str, msg_id: int) -> None:
    """Record the exact model-facing text of this user row so later turns replay it identically."""
    try:
        rows = db.get_messages(conv_id)
        ours = next((r for r in reversed(rows) if r["id"] == msg_id), None)
        if not ours or ours["content"] == sent:
            return
        db.set_message_meta(msg_id, {"sent_content": sent})
    except Exception:
        pass
