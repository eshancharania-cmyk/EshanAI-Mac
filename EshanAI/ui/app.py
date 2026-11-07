"""EshanAI main window: sidebar + chat + composer, event pump, confirm modals."""
from __future__ import annotations

import datetime
import os
import queue
import shutil
import subprocess
import threading
import time
import traceback

import customtkinter as ctk
import tkinter as tk

# Dark appearance MUST be active before any CTk window is constructed so the
# title bar goes dark (CTk reads mode at window creation on Windows).
ctk.set_appearance_mode("dark")

from core import config, db
from core import paths
from core import ollama_client as oc
from core.brain import Brain, TITLE_EVENTS, wait_titles_done
from core import memory as _mem
from core.speech import VoiceIO, mood_of, stt_warm, stt_ready
from ui import theme
from ui.chat import ChatArea
from ui.composer import Composer
from ui.sidebar import Sidebar
from ui.voice import VoicePanel
from ui.dialogs import (UIConfirmationBroker, ConfirmDialog, TextEntryDialog,
                        ConfirmBrokerModal, MemoryDialog, SettingsDialog)

PUMP_MS = 30                        # event pump period
CREATE_NO_WINDOW = 0x08000000

EMPTY_HINT = "What can I help you with?"


class EshanAIApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self._closing = False
        self._busy = False
        self._events: "queue.Queue[dict]" = queue.Queue()
        self._agent_thread = None
        self._warm_thread = None
        self._confirm_modal_open = False
        self._resize_job = None
        self._ollama_ok: bool | None = None
        self._font_scale = float(config.get("ui.font_scale", 1.0))

        geom = config.get("ui.geometry") or [1240, 800]
        self.geometry(f"{int(geom[0])}x{int(geom[1])}")
        self.minsize(940, 620)
        self.title("EshanAI")
        self.configure(fg_color=theme.WINDOW_BG)

        self.broker = UIConfirmationBroker()
        self.brain = Brain(self.broker)
        self._voice_panel = None
        self.voice = VoiceIO(self._emit)   # fully local mic-in / voice-out

        # ------------------------------------------------------------- layout
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = Sidebar(self, callbacks={
            "on_new_chat": self.new_chat,
            "on_select": self.select_conversation,
            "on_rename": self.rename_conversation,
            "on_delete": self.delete_conversation,
            "on_search": self.run_search,
            "on_settings": self.open_settings,
            "on_memory": self.open_memory,
        }, font_scale=self._font_scale,
           width=int(config.get("ui.sidebar_width", 300)) or 300)
        self.sidebar.grid(row=0, column=0, sticky="ns")

        ctk.CTkFrame(self, width=1, fg_color=theme.BORDER).grid(row=0, column=1, sticky="ns")

        self._build_right()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Configure>", self._on_root_configure)

        # ------------------------------------------------------- conversation
        convs = db.list_conversations()
        self.conv_id = convs[0]["id"] if convs else db.create_conversation()
        self._render_history()
        self.sidebar.refresh(db.list_conversations(), selected=self.conv_id)

        # ------------------------------------------------------- services
        self._startup_ollama_flow()      # running-check -> prewarm or overlay
        threading.Thread(target=self._status_ticker, daemon=True).start()
        self.after(PUMP_MS, self._pump)

    # ================= layout pieces =================

    def _fs(self, size: float) -> float:
        return max(9.0, size * self._font_scale)

    def _build_right(self):
        self._right = ctk.CTkFrame(self, fg_color=theme.CHAT_BG, corner_radius=0)
        self._right.grid(row=0, column=2, sticky="nsew")
        self._right.grid_rowconfigure(1, weight=1)
        self._right.grid_columnconfigure(0, weight=1)

        self._build_topbar()

        self.chat = ChatArea(self._right, font_scale=self._font_scale,
                             suggest=self.send_text)
        self.chat.grid(row=1, column=0, sticky="nsew")

        self.bottom = ctk.CTkFrame(self._right, fg_color=theme.CHAT_BG)
        self.bottom.grid(row=2, column=0, sticky="ew")
        self.bottom.grid_columnconfigure(0, weight=1)

        self.composer = Composer(self.bottom, on_send=self._on_send,
                                 font_scale=self._font_scale,
                                 on_mic=lambda: self.toggle_voice())
        self.composer.grid(row=0, column=0, sticky="ew")
        self.after(80, self.composer.focus_input)
        self.after(60, lambda: self._on_root_configure())   # center column on first map

        # first-run onboarding: fresh machine / fresh data dir
        self.onboard = None
        try:
            from core import setup_flow
            if setup_flow.needs_setup():
                self.after(400, self.open_onboarding)
        except Exception:
            self.onboard = None

    # ================= first-run onboarding =================

    def open_onboarding(self):
        if self.onboard is not None or self._closing:
            return
        from ui.onboard import OnboardingCard
        card = OnboardingCard(self._right, self,
                              on_finish=self._close_onboarding)
        self.onboard = card
        card.place(relx=0, rely=0, relwidth=1, relheight=1)
        card.lift()
        card.focus_set()

    def _close_onboarding(self):
        card, self.onboard = self.onboard, None
        if card is not None:
            try:
                card.destroy()
            except Exception:
                pass
        try:
            name = str(config.get("user.name", "")) or "friend"
        except Exception:
            name = "friend"
        self._set_status(f"Welcome, {name}! Everything lives on this PC.",
                         theme.OK)

    def _build_topbar(self):
        """Slim top bar: model label left; voice picker + Ollama chip right
        (like ChatGPT's header)."""
        bar = ctk.CTkFrame(self._right, fg_color="transparent", height=44)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_columnconfigure(1, weight=1)
        bar.grid_propagate(False)

        ctk.CTkLabel(bar, text="EshanAI", font=theme.f(self._fs(14), "bold"),
                     text_color=theme.TEXT).grid(row=0, column=0, padx=(22, 6), pady=12)

        self._voice_labels = {"amy": "Amy · female", "ryan": "Ryan · male"}
        self._voice_menu = ctk.CTkOptionMenu(
            bar, values=["Amy · female", "Ryan · male"],
            fg_color="#22242A", button_color="#2A2D34",
            button_hover_color="#343943", dropdown_fg_color="#191B1E",
            dropdown_hover_color="#23262D", text_color=theme.TEXT_2,
            font=theme.f(self._fs(10.5)), width=132,
            command=self._on_voice_pick)
        self._voice_menu.grid(row=0, column=2, sticky="e", pady=10,
                              padx=(0, 12))
        try:
            cur = str(config.get("voice.model", "amy"))
        except Exception:
            cur = "amy"
        self._voice_menu.set(self._voice_labels.get(cur,
                                                   "Amy · female"))

        chip = ctk.CTkFrame(bar, fg_color="transparent")
        chip.grid(row=0, column=3, sticky="e", padx=(0, 20))
        self._topbar_dot = tk.Canvas(chip, width=10, height=10,
                                     highlightthickness=0,
                                     bg=theme.CHAT_BG)
        self._topbar_dot_item = self._topbar_dot.create_oval(1, 1, 9, 9,
                                                             fill=theme.TEXT_3,
                                                             outline="")
        self._topbar_dot.pack(side="left", padx=(0, 7))
        self._topbar_chip_lbl = ctk.CTkLabel(chip, text="starting…",
                                             font=theme.f(self._fs(10.5)),
                                             text_color=theme.TEXT_3,
                                             anchor="e")
        self._topbar_chip_lbl.pack(side="left")

    def _on_voice_pick(self, label: str):
        """Top-right voice dropdown: persist the pick and let the next
        reply (and every reply after) use it."""
        if getattr(self, "_voice_menu", None) is not None:
            try:
                self._voice_menu.set(label)     # keep the label in sync
            except Exception:
                pass
        key = "amy" if label.startswith("Amy") else "ryan"
        try:
            config.set("voice.model", key)
            config.save()
        except Exception:
            pass
        try:
            self.voice.speaker.warm()          # ready before first use
        except Exception:
            pass
        if self._voice_panel is not None:
            try:
                name = self.voice.speaker.voice_name()
                self.voice.speaker.speak_if_enabled(
                    f"Hi! I'm {name}. I'll talk like this from now on.")
            except Exception:
                pass

    def set_chip(self, text: str, ok: bool | None = None):
        """Ollama status chip (top-right). ok=True green dot."""
        if not hasattr(self, "_topbar_dot"):
            return
        color = {True: theme.OK, False: theme.ERR, None: theme.TEXT_3}[ok]
        try:
            self._topbar_dot.itemconfig(self._topbar_dot_item, fill=color)
            self._topbar_chip_lbl.configure(text=text)
        except Exception:
            pass

    def _rebuild_right(self):
        """Recreate chat/composer after a font-scale change (not while busy)."""
        if self._busy:
            return
        try:
            self.chat.destroy()
            self.composer.destroy()
            self.bottom.destroy()
            self._right.destroy()
        except Exception:
            pass
        self._build_right()
        self._render_history()

    # ================= ollama startup flow =================

    def _startup_ollama_flow(self):
        def probe():
            ok = oc.is_running()
            self._events.put({"type": "_startup", "running": bool(ok)})
        threading.Thread(target=probe, daemon=True).start()

    def _prewarm(self):
        if os.environ.get("ESHANAI_NO_WARM") or self._warm_thread or self._closing:
            return
        self._set_status("Warming up the local model…", theme.WARN)

        def worker():
            try:
                from core.prompts import build_system_prompt
                from core.tools import REGISTRY
                ok = oc.warm_model(
                    config.get("models.main"),
                    prime_system=build_system_prompt(REGISTRY.prompt_lines()))
            except Exception:
                ok = False
            self._events.put({"type": "_warmed", "ok": ok})
        self._warm_thread = threading.Thread(target=worker, daemon=True)
        self._warm_thread.start()

    def _ensure_ollama_overlay(self):
        if getattr(self, "_overlay", None) is not None:
            return
        ov = ctk.CTkFrame(self, fg_color=theme.SIDEBAR_BG, corner_radius=0)
        ov.grid(row=0, column=0, columnspan=3, sticky="nsew")
        ov.grid_rowconfigure(0, weight=1)
        ov.grid_columnconfigure(0, weight=1)
        card = ctk.CTkFrame(ov, fg_color=theme.CHAT_BG, corner_radius=14)
        card.grid(row=0, column=0)

        ctk.CTkLabel(card, text="Welcome to EshanAI", font=theme.f(20, "bold"),
                     text_color=theme.TEXT).pack(padx=36, pady=(30, 4))
        ctk.CTkLabel(card, text="Ollama isn't running yet. EshanAI's brain is a local\n"
                                "Ollama model — nothing leaves this PC.",
                     font=theme.f(11.5), text_color=theme.TEXT_2,
                     justify="center").pack(padx=36, pady=(0, 16))
        miss = getattr(self, "_missing_model", None)
        if miss:
            ctk.CTkLabel(card, text=f'Model missing:  ollama pull "{miss}"',
                         font=theme.mono(11), text_color=theme.ACCENT_LIGHT,
                         justify="center").pack(padx=36, pady=(0, 14))
        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.pack(padx=36, pady=(0, 10))
        ctk.CTkButton(btns, text="Start Ollama", width=150, height=36, corner_radius=9,
                      fg_color=theme.ACCENT, hover_color=theme.ACCENT_LIGHT,
                      text_color="#FFFFFF", font=theme.f(12, "bold"),
                      command=self._start_ollama_service).pack(side="left", padx=5)
        ctk.CTkButton(btns, text="Check again", width=128, height=36, corner_radius=9,
                      fg_color="transparent", border_width=1, border_color=theme.BORDER,
                      hover_color=theme.BORDER, text_color=theme.TEXT_2,
                      font=theme.f(12), command=self._check_ollama).pack(side="left", padx=5)
        ctk.CTkLabel(card, text="If Ollama is not installed, download it from ollama.com.",
                     font=theme.f(10.5), text_color=theme.TEXT_3).pack(padx=36, pady=(0, 26))
        self._overlay = ov

    def _start_ollama_service(self):
        exe = shutil.which("ollama")
        if not exe:
            common = os.environ.get("LOCALAPPDATA", "")
            guess = os.path.join(common, "Programs", "Ollama", "ollama app.exe")
            guess2 = os.path.join(common, "Ollama", "ollama app.exe")
            exe = guess if os.path.exists(guess) else \
                (guess2 if os.path.exists(guess2) else None)
        if not exe:
            self._set_status("Ollama not found — install it from ollama.com", theme.ERR)
            return
        try:
            subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            self._set_status(f"Could not start Ollama: {e}", theme.ERR)
            return
        self._set_status("Starting Ollama…", theme.WARN)
        self._check_ollama()

    def _check_ollama(self):
        def worker():
            running = False
            deadline = time.time() + 20
            while time.time() < deadline and not running and not self._closing:
                running = oc.is_running()
                if not running:
                    time.sleep(1.0)
            self._events.put({"type": "_startup", "running": running})
        threading.Thread(target=worker, daemon=True).start()

    def _status_ticker(self):
        # keeps sidebar chip fresh; touches UI only via the event queue
        while not self._closing:
            try:
                running = oc.is_running(timeout=1.0)
                names = oc.loaded_names() if running else []
            except Exception:
                running, names = None, []
            self._events.put({"type": "_ollama", "running": running, "loaded": names})
            time.sleep(4.0)

    # ================= sending / agent =================

    def send_text(self, text: str):
        """Programmatic send (tests / automation). Mirrors pressing Enter."""
        self._on_send(text)

    def _on_send(self, text: str):
        if self._closing:
            return
        if not (text or "").strip():
            return
        if self._busy:
            self._set_status("EshanAI is still replying — one task at a time", theme.WARN)
            return
        if self._ollama_ok is False:
            self._ensure_ollama_overlay()
            return
        text = text.strip()
        self._busy = True
        self.composer.set_busy(True)
        self.chat.add_user_message(text)
        self.chat.begin_assistant_message()
        self._set_status("Thinking…", theme.TEXT_3)
        # NOTE: the brain persists the user row itself (with memory-prefix
        # meta); adding it here again would duplicate the bubble in history.
        db.touch_conversation(self.conv_id)
        self.sidebar.refresh(db.list_conversations(), selected=self.conv_id)

        th = threading.Thread(target=self._agent_run, args=(self.conv_id, text),
                              daemon=True)
        self._agent_thread = th
        th.start()

    def _agent_run(self, conv_id: int, text: str):
        try:
            self.brain.run_turn(conv_id, text, self._emit)
        except Exception as e:                     # never kill the agent thread silently
            try:
                self._emit({"type": "error", "message": f"Unexpected problem: {e}"})
            except Exception:
                pass
        finally:
            self._emit({"type": "_turn_done"})

    def _emit(self, ev: dict):
        if self._closing:
            return
        self._events.put(ev)

    # ================= event pump =================

    def _pump(self):
        if self._closing:
            return

        # 1) tool confirmations (agent threads park; UI shows the modal)
        if not self._confirm_modal_open:
            pending = self.broker.pop_pending()
            if pending is not None:
                self._confirm_modal_open = True
                try:
                    ConfirmBrokerModal.display(self, pending)
                finally:
                    self._confirm_modal_open = False

        # 2) drain queued UI events (bounded so one pump never stalls)
        for _ in range(200):
            try:
                ev = self._events.get_nowait()
            except queue.Empty:
                break
            try:
                self._dispatch(ev)
            except Exception as e:
                self.report_callback_exception(type(e), e, None)

        # 3) auto-title events
        while TITLE_EVENTS:
            try:
                cid, title = TITLE_EVENTS.pop(0)
                self.sidebar.update_title(cid, title)
            except Exception:
                break

        self.after(PUMP_MS, self._pump)

    def _dispatch(self, ev: dict):
        t = ev.get("type")
        if t == "delta":
            self.chat.append_stream(ev.get("text", ""))
        elif t == "status":
            self._set_status(ev.get("text", ""), theme.TEXT_3)
        elif t == "tool_call":
            if self.chat._pending_chip is not None:
                self.chat.resolve_pending_chip(False, "(no result reported)")
            self.chat.add_tool_chip(ev.get("tool", "tool"),
                                    ev.get("args_preview", ""), None)
            self._set_status(f"Running {ev.get('tool', 'tool')}…", theme.WARN)
        elif t == "tool_result":
            ok = bool(ev.get("ok"))
            if not self.chat.resolve_pending_chip(ok, ev.get("result", "")):
                self.chat.add_tool_chip(ev.get("tool", "tool"), "", ok,
                                        ev.get("result", ""))
            self._set_status(f"{ev.get('tool', 'tool')} — {'done' if ok else 'error'}",
                             theme.OK if ok else theme.ERR)
        elif t == "error":
            self._set_status(ev.get("message", "error"), theme.ERR)
            self.chat.add_status_chip(ev.get("message", ""), kind="err")
        elif t == "fallback":
            self._set_status(f'Falling back to {ev.get("model", "fast model")}…', theme.WARN)
        elif t == "assistant_final":
            content = ev.get("content", "")
            self.chat.finalize_assistant(content)
            # face mood follows the topic; voice replies only in voice mode
            if self._voice_panel is not None:
                try:
                    self._voice_panel.set_mood(mood_of(content))
                except Exception:
                    pass
                self.voice.speaker.speak_if_enabled(content)
            elapsed = ev.get("elapsed")
            if elapsed is not None:
                try:
                    self._set_status(f"Done in {float(elapsed):.0f}s · {ev.get('model', '')}",
                                     theme.TEXT_3)
                except Exception:
                    pass
        elif t == "_turn_done":
            self._busy = False
            self.composer.set_busy(False)
            self.composer.set_mic_state(
                "open" if self._voice_panel is not None else "normal")
            if not self._closing:
                self.sidebar.refresh(db.list_conversations(), selected=self.conv_id)
        elif t == "_voice_state":
            st = ev.get("state")
            if st == "recording":
                self.composer.set_mic_state("recording")
                if self._voice_panel is not None:
                    self._voice_panel.set_capture(True)
            elif st == "transcribing":
                self.composer.set_mic_state(
                    "open" if self._voice_panel is not None else "normal")
                if self._voice_panel is not None:
                    self._voice_panel.set_capture(False)
                    self._voice_panel.set_thinking()
            else:
                self.composer.set_mic_state(
                    "open" if self._voice_panel is not None else "normal")
                if self._voice_panel is not None:
                    self._voice_panel.set_idle()
        elif t == "_setup":
            if getattr(self, "onboard", None) is not None:
                self.onboard.on_flow_event(ev)
        elif t == "_stt_result":
            text = (ev.get("text") or "").strip()
            if not text:
                return
            if self._voice_panel is not None:
                self._voice_panel.set_thinking()
            if self._busy:
                # one task at a time: keep their words, don't lose them
                try:
                    self.composer._clear_placeholder()
                    self.composer.input.delete("1.0", "end")
                    self.composer.input.insert("1.0", text)
                    self.composer._autogrow()
                    self._set_status("EshanAI is still answering — your words are "
                                     "waiting in the input box", theme.WARN)
                except Exception:
                    pass
            else:
                self._on_send(text)
        elif t == "_stt_status":
            self._set_status(ev.get("msg", ""), theme.WARN)
            if self._voice_panel is not None:
                self._voice_panel.set_idle()
        elif t == "_tts_state":
            if self._voice_panel is not None:
                self._voice_panel.set_speaking(bool(ev.get("speaking")))
        elif t == "_tts_status":
            self._set_status(ev.get("msg", ""), theme.WARN)
        elif t == "_warmed":
            if ev.get("ok"):
                self._set_status("Ready", theme.OK)
            else:
                self._set_status("Model warm-up failed — send a message anyway",
                                 theme.WARN)
        elif t == "_startup":
            self._ollama_ok = bool(ev.get("running"))
            if self._ollama_ok:
                self._dismiss_overlay()
                main = config.get("models.main")
                if not oc.is_installed(main):
                    self._missing_model = main
                    self._ensure_ollama_overlay()
                else:
                    self._prewarm()
            else:
                self._ollama_ok = False
                self._ensure_ollama_overlay()
        elif t == "_ollama":
            running = ev.get("running")
            loaded = ev.get("loaded") or []
            if running is None:
                return
            if running:
                short = config.get("models.main", "").split("/")[-1]
                extra = f" · {short} loaded" if short and any(short in n for n in loaded) else ""
                self.set_chip(f"Ollama{extra}", True)
            else:
                self.set_chip("Ollama not running", False)
            self._ollama_ok = bool(running)
            if running and getattr(self, "_overlay", None) is not None:
                self._dismiss_overlay()
                self._prewarm()
            if (not running) and self._busy and not self._closing:
                pass    # brain handles its own connection errors

    def _dismiss_overlay(self):
        ov = getattr(self, "_overlay", None)
        if ov is not None:
            try:
                ov.destroy()
            except Exception:
                pass
            self._overlay = None

    def _set_status(self, text: str, color=None):
        try:
            self.composer.set_note(text)
        except Exception:
            pass

    # ================= conversations =================

    def new_chat(self):
        if self._busy:
            self._set_status("Finish the current reply first", theme.WARN)
            return
        self.conv_id = db.create_conversation()
        db.touch_conversation(self.conv_id)
        self.sidebar.refresh(db.list_conversations(), selected=self.conv_id)
        self.chat.clear()
        self.chat.show_empty_hint(EMPTY_HINT)
        self.composer.clear_text()
        self.composer.focus_input()
        self._set_status("", theme.TEXT_3)

    def select_conversation(self, conv_id: int):
        if self._busy:
            self._set_status("Finish the current reply first", theme.WARN)
            # repaint the old selection shortly (the row highlight already moved)
            self.after(60, lambda: self.sidebar.refresh(
                db.list_conversations(), selected=self.conv_id))
            return
        if conv_id == self.conv_id:
            return
        self.conv_id = conv_id
        self._render_history()
        self.sidebar.refresh(db.list_conversations(), selected=conv_id)

    def rename_conversation(self, conv_id: int):
        conv = db.get_conversation(conv_id)
        dlg = TextEntryDialog(self, "Rename conversation",
                              value=conv["title"] if conv else "")
        value = dlg.wait_dialog()
        if value:
            db.rename_conversation(conv_id, value)
            self.sidebar.refresh(db.list_conversations(), selected=self.conv_id)

    def delete_conversation(self, conv_id: int):
        if self._busy:
            self._set_status("Finish the current reply first", theme.WARN)
            return
        dlg = ConfirmDialog(self, "⚠ Delete conversation?",
                            "This permanently removes the conversation and its messages.")
        if not dlg.wait_dialog():
            return
        db.delete_conversation(conv_id)
        convs = db.list_conversations()
        if convs:
            self.conv_id = convs[0]["id"]
            self._render_history()
        else:
            self.conv_id = db.create_conversation()
            self.chat.clear()
            self.chat.show_empty_hint(EMPTY_HINT)
        self.sidebar.refresh(db.list_conversations(), selected=self.conv_id)

    def run_search(self, query: str):
        if not query:
            self.sidebar.refresh(db.list_conversations(), selected=self.conv_id)
            return
        results = {c["id"]: c for c in db.search_conversations(query)}
        for hit in db.search_messages(query, 30):
            cid = hit["conv_id"]
            if cid not in results:
                conv = db.get_conversation(cid)
                if conv:
                    results[cid] = conv
        self.sidebar.refresh(list(results.values()), selected=self.conv_id)

    def _render_history(self):
        try:
            self.chat.clear()
        except Exception:
            pass
        rows = db.get_messages(self.conv_id)
        shown = False
        for m in rows:
            role = m["role"]
            meta = m.get("meta") or {}
            if role == "user":
                self.chat.add_user_message(m.get("content") or "")
                shown = True
            elif role == "assistant":
                content = m.get("content") or ""
                if content.strip():
                    self.chat.begin_assistant_message()
                    self.chat.append_stream(content)
                    self.chat.finalize_assistant()
                    shown = True
            elif role == db.TOOL:
                ok = str(meta.get("status")) == "ok"
                self.chat.add_tool_chip(str(meta.get("tool") or "tool"), "", ok,
                                        str(meta.get("result") or ""))
        if not shown:
            self.chat.clear()
            self.chat.show_empty_hint(EMPTY_HINT)
        self.after(60, self.chat._after_add)

    # ================= panels =================

    def open_settings(self):
        dlg = SettingsDialog(self, config, self._font_scale)
        self.wait_window(dlg)
        result = dlg.result
        if not result:
            return
        try:
            config.set("generation.temperature", result["temperature"])
            config.set("agent.max_tool_steps", result["max_tool_steps"])
            config.set("agent.auto_title", result["auto_title"])
            config.set("agent.inject_memories", result["inject_memories"])
            font_changed = abs(result["font_scale"] - self._font_scale) > 0.01
            config.set("ui.font_scale", result["font_scale"])
            config.save()
        except Exception as e:
            self._set_status(f"Could not save settings: {e}", theme.ERR)
            return
        self.brain._system = None        # rebuild system prompt if any tool text changed? keep cache small
        if font_changed and not self._busy:
            self._font_scale = result["font_scale"]
            self._rebuild_right()
        self._set_status("Settings saved", theme.OK)

    def open_memory(self):
        dlg = MemoryDialog(self, db)
        dlg.focus_force()

    def apply_memory_note(self, text: str):
        """Programmatic memory add (tests / future)."""
        fact = _mem.save_fact(text, source="user")

    # ================= lifecycle =================

    def _on_root_configure(self, event=None):
        if event is not None and event.widget is not self:
            return
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.after(150, self._do_resize)

    def _do_resize(self):
        self._resize_job = None
        try:
            self.chat.on_resize()
            self.composer.on_resize()
        except Exception:
            pass

    # ================= voice mode =================

    def toggle_voice(self):
        if self._closing:
            return
        if self._voice_panel is None:
            if self._busy:
                self._set_status("EshanAI is still replying — one moment",
                                 theme.WARN)
                return
            self._open_voice()
        else:
            self._close_voice()

    def _open_voice(self):
        panel = VoicePanel(self._right, callbacks={
            "on_capture_start": self._voice_capture_start,
            "on_capture_end": self._voice_capture_end,
            "on_close": self._close_voice,
        }, font_scale=self._font_scale)
        self._voice_panel = panel
        panel.place(relx=0, rely=0, relwidth=1, relheight=1)
        panel.lift()
        panel.focus_set()
        self.composer.set_mic_state("open")
        self._warm_stt()

    def _warm_stt(self):
        if stt_ready():
            return
        self._set_status("One-time download of the local voice model "
                         "(~75 MB)…", theme.WARN)
        stt_warm(cb=lambda ok, msg: self._events.put({
            "type": "status",
            "text": "Voice brain ready" if ok
                    else f"Voice brain error: {msg}"}))

    def _voice_capture_start(self):
        if self._closing or getattr(self, "_confirm_modal_open", False):
            return
        self.voice.speaker.stop()        # barge-in: talking over the reply
        self.voice.listener.start()

    def _voice_capture_end(self):
        self.voice.listener.stop()

    def _close_voice(self):
        p, self._voice_panel = self._voice_panel, None
        self.composer.set_mic_state("normal")
        self.voice.speaker.stop()
        self.voice.listener.cancel()
        if p is not None:
            p.begin_close()
            p.after(230, p.destroy)
        self._set_status("", theme.TEXT_3)

    def _on_close(self):
        if self._closing:
            return
        self._closing = True
        try:
            self.voice.shutdown()              # stop mic + speaker threads
        except Exception:
            pass
        try:                                  # decline stranded confirmations
            for item in list(self.broker._queue):
                item["ok"] = False
                item["evt"].set()
        except Exception:
            pass
        try:
            wait_titles_done(2.0)
        except Exception:
            pass
        try:
            config.set("ui.geometry", [self.winfo_width(), self.winfo_height()])
            config.save()
        except Exception:
            pass
        self.destroy()

    def report_callback_exception(self, exc, val, tb):
        """Log UI-callback errors to file; keep the app alive."""
        try:
            log = paths.logs_dir() / "ui_errors.log"
            with open(log, "a", encoding="utf-8", errors="replace") as f:
                now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n[{now}] {exc if isinstance(exc, str) else exc.__name__}: {val}\n")
                traceback.print_exception(exc, val, tb, file=f)
        except Exception:
            pass
        try:
            self._set_status(f"Internal UI error: {getattr(exc, '__name__', exc)}", theme.ERR)
        except Exception:
            pass
