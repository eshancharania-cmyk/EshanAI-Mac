"""Completely local voice for EshanAI — mic in whisper out.

    you talk  ->  faster-whisper STT  (local Whisper on CPU; one-time
                  ~75 MB model download into the EshanAI data dir)
    it talks  ->  Piper neural TTS: two bundled young voices picked
                  from the top-right dropdown — Amy (female) / Ryan
                  (male), ~60 MB each, fully offline. Windows SAPI5
                  (David/Zira) remains the automatic fallback.

No cloud, no accounts, no network calls during a session (except the
one-time whisper model fetch, cached permanently afterwards).

Threading contract
------------------
Listener: one worker thread per push-to-talk press. Pulls mic blocks
through a sounddevice InputStream, energy-gates speech (adaptive noise
floor, trailing-silence end), then transcribes. Everything for the UI
travels through the caller's `emit(dict)` callback (the app event queue).

Speaker: one daemon thread consuming a queue; neural Piper renders
sentence-sized audio chunks straight into the sound stream (barge-in
cuts at any ~150 ms boundary), falling back to a fresh SAPI engine per
utterance (pyttsx3's shared engine isn't loop-safe) when Piper isn't
available; stop() interrupts either way.
"""
from __future__ import annotations

import queue
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np

from core import config, paths

SR = 16000                    # whisper-native sample rate
BLOCK_MS = 32                 # capture block size
BLOCK = BLOCK_MS * SR // 1000  # frames per block

PRE_SPEECH_MAX = 700          # blocks (≈22 s) waiting for speech to start
MIN_SPEECH_MS = 350           # speech shorter than this = "too quiet/short"
SILENCE_AFTER_MS = 850        # trailing silence that ends the utterance
MAX_MS = 25000                # absolute utterance length cap
MAX_SPOKEN_CHARS = 700        # don't read essays out loud


# ------------------------------------------------------------ spoken text
_MD_PATTERNS = [
    (re.compile(r"```.*?```", re.S), "\n"),    # fenced code blocks -> gap
    (re.compile(r"`([^`]*)`"), r"\1"),         # inline code -> its text
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),  # [text](url) -> text
    (re.compile(r"https?://\S+"), ""),         # bare urls (unreadable aloud)
    (re.compile(r"[*_#>]{1,3}"), ""),          # bold/italic/heading markers
    (re.compile(r"^\s*[-*]\s+", re.M), "- "),  # tidy dash bullets
]


def speakable_text(text: str) -> str:
    """Markdown-free plain prose for the voice, cut at a sentence boundary."""
    if not text:
        return ""
    out = text
    for pat, repl in _MD_PATTERNS:
        out = pat.sub(repl, out)
    out = re.sub(r"\n{2,}", "\n", out)
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    if len(out) <= MAX_SPOKEN_CHARS:
        return out
    cut = out[:MAX_SPOKEN_CHARS]
    for sep in (". ", "! ", "? ", "; ", ": ", "\n"):
        pos = cut.rfind(sep)
        if pos > MAX_SPOKEN_CHARS * 0.5:
            return cut[:pos + len(sep)].strip() + " — ask me to continue."
    return cut.strip() + "… ask me to continue."


# ------------------------------------------------------------ reply mood
_MOODS = {
    "happy": ("great", "nice", "awesome", "perfect", "love", "lovely", "happy",
              "thanks", "thank you", "funny", "lol", "ha ", "success",
              "works", "enjoy", "excited", "😊", "🙂", "😄", "congrat",
              "well done", "ready"),
    "sad": ("sorry", "sad", "bad news", "failed", "fail", "broken",
            "problem", "error", "unfortunately", "can't", "cannot",
            "won't", "afraid", "worried", "unavailable", "missing",
            "mistake", "lost", "uninstall", "crash"),
    "angry": ("angry", "furious", "ridiculous", "absurd", "unacceptable",
              "refuse", "dangerous", "reckless", "shut down", "no way",
              "stop that", "forbidden"),
}


def mood_of(text: str) -> str:
    """Cheap local sentiment: which face should EshanAI wear for this piece
    of text? 'happy' | 'sad' | 'angry' | 'neutral'."""
    t = (text or "").lower()
    best, best_hits = "neutral", 0
    for mood, words in _MOODS.items():
        hits = sum(t.count(w) for w in words)
        if mood == "happy":
            hits += 1 if "!" in t else 0
        if hits > best_hits:
            best, best_hits = mood, hits
    return best


# ------------------------------------------------------------ STT model
_model = None
_model_lock = threading.Lock()


def _whisper_model():
    """Load (once, thread-safely) the local whisper model. First use downloads
    it into the EshanAI data dir; after that it is permanent and offline."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        from faster_whisper import WhisperModel
        name = config.get("voice.stt_model", "tiny.en")
        _model = WhisperModel(name, device="cpu", compute_type="int8",
                              download_root=str(paths.speech_dir()))
        return _model


def transcribe_pcm(pcm: np.ndarray) -> str:
    """np.float32 mono waveform -> text."""
    m = _whisper_model()
    segments, _info = m.transcribe(
        pcm, language="en", beam_size=1, condition_on_previous_text=False)
    return "".join(s.text for s in segments).strip()


def stt_ready() -> bool:
    """True once the whisper model is loaded in this process."""
    return _model is not None


def stt_warm(cb=None):
    """Pre-load the model in the background (nicer first voice click)."""
    def job():
        try:
            _whisper_model()
            if cb:
                cb(True, "")
        except Exception as e:
            if cb:
                cb(False, str(e))
    threading.Thread(target=job, daemon=True).start()


def stt_warm(cb=None):
    """Pre-load the model in the background (nicer first voice click)."""
    def job():
        try:
            _whisper_model()
            if cb:
                cb(True, "")
        except Exception as e:
            if cb:
                cb(False, str(e))
    threading.Thread(target=job, daemon=True).start()


# ------------------------------------------------------------ Listener
class Listener:
    """Push-to-talk capture driven by the voice panel:

        start()         space pressed  -> capturing begins
        stop()          space released -> end capture, transcribe, emit result
        cancel()        panel closed too early -> throw away everything

    States: idle | recording | transcribing
    """

    def __init__(self, emit):
        self._emit = emit
        self._thread: threading.Thread | None = None
        self._done = threading.Event()          # tells the capturer to end
        self._discard = threading.Event()       # ...and throw the audio away
        self.state = "idle"

    # ---- public (called from the UI thread) ----
    def start(self) -> bool:
        if self.state != "idle":
            return False
        self._done.clear()
        self._discard.clear()
        self.state = "recording"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self.state == "recording":
            self._done.set()

    def cancel(self) -> None:
        if self.state == "recording":
            self._discard.set()
            self._done.set()
        self.state = "idle"
        self._emit({"type": "_voice_state", "state": "idle"})

    def shutdown(self) -> None:
        self.cancel()

    # ---- worker ----
    def _run(self):
        try:
            import sounddevice as sd
        except Exception as e:
            self._fail(f"Microphone stack unavailable: {e}")
            return
        chunks: queue.Queue[np.ndarray] = queue.Queue(maxsize=400)
        ended = threading.Event()

        def cb(indata, frames, t_, status):
            try:
                chunks.put(indata[:, 0].copy(), block=False)
            except queue.Full:
                pass
            if ended.is_set():
                raise sd.CallbackStop

        try:
            stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                                    blocksize=BLOCK, callback=cb)
            stream.start()
        except Exception as e:
            self._fail(f"Could not open the microphone ({e.__class__.__name__}) — "
                       "check Windows mic permissions")
            return
        self._emit({"type": "_voice_state", "state": "recording"})

        collected: list[np.ndarray] = []
        spoken_blocks = 0
        sil_ms = 0
        max_rms = 0.0
        started = False
        floor: list[float] = []
        thresh = 0.012
        captured_ms = 0

        try:
            while True:
                try:
                    blk = chunks.get(timeout=0.25)
                except queue.Empty:
                    continue
                rms = float(np.sqrt(np.mean(blk * blk)))
                captured_ms += BLOCK_MS
                if not started:
                    if len(floor) < 30:               # ~1 s noise-floor learning
                        floor.append(rms)
                        if len(floor) == 30:
                            fl = float(np.min(floor))
                            thresh = max(0.010, fl * 2.6, fl + 0.010)
                if rms > thresh:
                    if not started:
                        started = True
                        if len(collected) > 125:      # keep ≈4 s of pre-roll
                            collected = collected[-125:]
                    spoken_blocks += 1
                    sil_ms = 0
                    max_rms = max(max_rms, rms)
                elif started:
                    sil_ms += BLOCK_MS
                collected.append(blk)
                if (started and spoken_blocks * BLOCK_MS >= MIN_SPEECH_MS
                        and sil_ms >= SILENCE_AFTER_MS) \
                        or self._done.is_set() or captured_ms >= MAX_MS:
                    ended.set()
                    break
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

        if self._discard.is_set():
            self.state = "idle"
            self._emit({"type": "_voice_state", "state": "idle"})
            return
        if not started or spoken_blocks * BLOCK_MS < MIN_SPEECH_MS \
                or max_rms < thresh * 1.05:
            self.state = "idle"
            self._emit({"type": "_voice_state", "state": "idle"})
            self._emit({"type": "_stt_status", "ok": False,
                        "msg": "Speech too quiet or too short — hold SPACE and "
                               "speak a bit louder"})
            return

        pcm = np.concatenate(collected).astype(np.float32)
        self.state = "transcribing"
        self._emit({"type": "_voice_state", "state": "transcribing"})
        try:
            text = transcribe_pcm(pcm)
        except Exception as e:
            self._fail(f"voice brain error: {e.__class__.__name__}")
            return
        self.state = "idle"
        self._emit({"type": "_voice_state", "state": "idle"})
        if text:
            self._emit({"type": "_stt_result", "text": text})
        else:
            self._emit({"type": "_stt_status", "ok": False,
                        "msg": "I couldn't make out the words — try again"})

    def _fail(self, msg: str):
        self.state = "idle"
        self._emit({"type": "_voice_state", "state": "idle"})
        self._emit({"type": "_stt_status", "ok": False, "msg": msg})


# ------------------------------------------------------------ Speaker
class Speaker:
    """Local SAPI5 voice. speak(text) queues; stop() interrupts instantly."""

    ENGINES = []

    def __init__(self, emit):
        self._emit = emit
        self._q: "queue.Queue[str | None]" = queue.Queue()
        self._cur_engine = None
        self._lock = threading.Lock()
        self.speaking = False
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def speak_if_enabled(self, text: str):
        try:
            enabled = bool(config.get("voice.tts", True))
        except Exception:
            enabled = True
        if not enabled:
            return
        text = speakable_text(text)
        if text:
            self._q.put(text)

    def stop(self):
        """Barge-in: drop queued lines and cut the current one."""
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self._stopped.set()
        with self._lock:
            eng = self._cur_engine
        if eng is not None and eng != "piper":
            # the neural voice stops via _stopped (playback loop checks it)
            try:
                eng.stop()
            except Exception:
                pass

    def shutdown(self):
        try:
            self._q.put_nowait(None)
        except Exception:
            pass
        self.stop()

    PIPER_VOICES = {              # key -> (bundled onnx file, human name)
        "amy":  ("en_US-amy-medium.onnx", "Amy"),
        "ryan": ("en_US-ryan-medium.onnx", "Ryan"),
    }

    def warm(self):
        """Load the SELECTED neural voice in the background right after
        boot (and after a dropdown switch) so the first reply isn't
        late; harmless if it can't load."""
        def job():
            self._piper_voice()
        threading.Thread(target=job, daemon=True).start()

    def voice_name(self) -> str:
        "Human name of the currently selected neural voice."
        key = str(config.get("voice.model", "amy"))
        key = key if key in self.PIPER_VOICES else "amy"
        return self.PIPER_VOICES[key][1]

    def _piper_voice(self, key: str | None = None):
        "Cached lazy-load of a bundled neural voice; None when absent."
        if key is None:
            key = str(config.get("voice.model", "amy"))
        key = key if key in self.PIPER_VOICES else "amy"
        cache = getattr(self, "_piper_cache", None)
        if cache is None:
            cache = self._piper_cache = {}
        if key in cache:
            return cache[key]
        fname = self.PIPER_VOICES[key][0]
        obj = None
        try:
            model = paths.data_dir() / "tts" / fname
            if not model.is_file():
                # frozen exe: the voice ships as a bundled resource
                bundle = getattr(sys, "_MEIPASS", None)
                if bundle:
                    cand = Path(bundle) / "tts" / fname
                    if cand.is_file():
                        model = cand
            if model.is_file():
                import piper
                from piper import PiperVoice
                espeak_data = (Path(piper.__file__).parent /
                               "espeak-ng-data")
                kw = {}
                if espeak_data.is_dir():
                    kw["espeak_data_dir"] = espeak_data
                obj = PiperVoice.load(str(model), **kw)
        except Exception:
            obj = None
        cache[key] = obj
        return obj

    def _piper_speak(self, text: str) -> bool:
        """Neural voice: sentence-sized chunks straight into the sound
        stream (barge-in cuts within ~150 ms). True = handled, False =
        caller should fall back to the Windows desktop voice."""
        try:
            voice = self._piper_voice()
            if voice is None:
                return False
            import sounddevice as sd
            from piper import SynthesisConfig
            with self._lock:
                self._cur_engine = "piper"
            sr = voice.config.sample_rate
            rate = int(config.get("voice.rate", 178)) or 178
            length = max(0.78, min(1.45, 192.0 / rate))
            cfg = SynthesisConfig(volume=0.95, length_scale=length)
            stream = sd.OutputStream(samplerate=sr, channels=1,
                                     dtype="int16")
            stream.start()
            try:
                for ch in voice.synthesize(text, cfg):
                    pcm = ch.audio_int16_array
                    step = max(1, int(sr * 0.15))
                    for i in range(0, len(pcm), step):
                        if self._stopped.is_set():
                            return True
                        stream.write(pcm[i:i + step])
                while stream.active and not self._stopped.is_set():
                    time.sleep(0.03)
            finally:
                try:
                    stream.abort()
                    stream.close()
                except Exception:
                    pass
            return True
        except Exception as e:
            if not self._stopped.is_set():
                self._emit({"type": "_tts_status", "ok": False,
                            "msg": (f"neural voice skipped: "
                                    f"{e.__class__.__name__}")})
            return False

    def _sapi_speak(self, text: str):
        "Windows desktop voice fallback (Microsoft David/Zira)."
        import pyttsx3
        eng = None
        try:
            eng = pyttsx3.init()
            with self._lock:
                self._cur_engine = eng
            eng.setProperty("rate", int(config.get("voice.rate", 178)))
            eng.say(text)
            eng.runAndWait()
        finally:
            with self._lock:
                self._cur_engine = None
            try:
                if eng is not None:
                    eng.stop()
            except Exception:
                pass

    def _run(self):
        while True:
            item = self._q.get()
            if item is None:
                return
            self._stopped.clear()
            self.speaking = True
            self._emit({"type": "_tts_state", "speaking": True})
            try:
                if not self._piper_speak(item):
                    self._sapi_speak(item)
            except Exception as e:
                if not self._stopped.is_set():
                    self._emit({"type": "_tts_status", "ok": False,
                                "msg": f"voice output error: {e.__class__.__name__}"})
            finally:
                with self._lock:
                    self._cur_engine = None
                self.speaking = False
                self._emit({"type": "_tts_state", "speaking": False})


# ------------------------------------------------------------ facade
class VoiceIO:
    """One object owning both directions; all UI traffic flows through emit()."""

    def __init__(self, emit):
        self.emit = emit
        self.listener = Listener(emit)
        self.speaker = Speaker(emit)
        self.speaker.warm()                 # neural voice ready before use

    def shutdown(self):
        self.listener.shutdown()
        self.speaker.shutdown()
