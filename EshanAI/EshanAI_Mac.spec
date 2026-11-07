# -*- mode: python ; coding: utf-8 -*-
# macOS build of EshanAI (built on GitHub Actions macos-13, Intel x64).
# Result: EshanAI.app (one drag-able app folder) — runs on Intel Macs and
# on Apple Silicon via Rosetta. Twins EshanAI.spec minus Windows-only bits:
#   - no SAPI5/comtypes (Windows TTS fallback; Piper-first means it's never
#     needed, and pyttsx3 falls back to NSSpeechSynthesis on mac if ever)
#   - no .ico/version_info (mac icon omitted for the first public builds)
from PyInstaller.utils.hooks import (collect_all, collect_dynamic_libs,
                                     collect_data_files, collect_submodules)

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ---------------------------------------------------------------- voice
# faster-whisper (local STT): ships native ctranslate2; av decodes audio;
# onnxruntime carries silero VAD deps.
tmp_ret = collect_all('faster_whisper')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
binaries += collect_dynamic_libs('ctranslate2')
binaries += collect_dynamic_libs('onnxruntime')
binaries += collect_dynamic_libs('av')
hiddenimports += collect_submodules('av')
hiddenimports += ['ctranslate2', 'tokenizers', 'numpy', 'PIL.ImageTk']

# TTS fallback (only reached if Piper misbehaves; lazy import in speech.py)
hiddenimports += ['pyttsx3', 'pyttsx3.drivers', 'pyttsx3.drivers.nsss']

# neural voice (Piper): the wheel bundles espeak-ng pronunciation data;
# Amy + Ryan ship as our own assets copied to tts/ in the bundle
datas += collect_data_files('piper')
datas += [('assets/tts/en_US-amy-medium.onnx', 'tts'),
          ('assets/tts/en_US-amy-medium.onnx.json', 'tts'),
          ('assets/tts/en_US-ryan-medium.onnx', 'tts'),
          ('assets/tts/en_US-ryan-medium.onnx.json', 'tts')]
hiddenimports += ['piper', 'pathvalidate']

# mic capture — sounddevice bundles the PortAudio dylib
tmp_ret = collect_all('sounddevice')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('_sounddevice_data')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=['comtypes', 'win32api', 'win32com', 'win32con', 'win32gui',
              'win32evtlog', 'win32file', 'pyttsx3.drivers.sapi5'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='EshanAI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    name='EshanAI',
)
app = BUNDLE(
    coll,
    name='EshanAI.app',
    icon=None,
)
