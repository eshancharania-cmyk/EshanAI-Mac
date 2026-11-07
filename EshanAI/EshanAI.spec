# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import (collect_all, collect_dynamic_libs,
                                     collect_data_files, collect_submodules)

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ---------------------------------------------------------------- voice
# faster-whisper (local STT): ships native ctranslate2; av decodes audio
# (we feed numpy wavs ourselves, but faster_whisper imports av at import
# time so it must be bundled); onnxruntime carries silero VAD deps.
tmp_ret = collect_all('faster_whisper')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
binaries += collect_dynamic_libs('ctranslate2')
binaries += collect_dynamic_libs('onnxruntime')
binaries += collect_dynamic_libs('av')
hiddenimports += collect_submodules('av')
hiddenimports += ['ctranslate2', 'tokenizers', 'numpy', 'PIL.ImageTk']

# TTS (SAPI5) — pyttsx3 drivers are imported dynamically by name and travel
# via hiddenimports; pyttsx3 itself carries no extra data files on Windows
# (and collect_data_files + include_py_files crashes / returns nothing here)
hiddenimports += ['pyttsx3', 'pyttsx3.drivers', 'pyttsx3.drivers.sapi5',
                  'comtypes', 'comtypes.stream']

# neural voice (Piper): the wheel bundles all espeak-ng pronunciation data
# inside the piper package (collect_data_files picks it up); Amy's ONNX
# voice ships as our own asset copied to tts/ in the bundle
datas += collect_data_files('piper')
datas += [('assets/tts/en_US-amy-medium.onnx', 'tts'),
          ('assets/tts/en_US-amy-medium.onnx.json', 'tts'),
          ('assets/tts/en_US-ryan-medium.onnx', 'tts'),
          ('assets/tts/en_US-ryan-medium.onnx.json', 'tts')]
hiddenimports += ['piper', 'pathvalidate']

# mic capture — sounddevice bundles the PortAudio DLL
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
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='EshanAI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='build/version_info.txt',
    icon=['assets/eshanai.ico'],
)
