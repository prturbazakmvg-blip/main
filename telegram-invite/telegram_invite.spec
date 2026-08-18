# -*- mode: python ; coding: utf-8 -*-
"""Сборка одного исполняемого файла. Запускать: pyinstaller telegram_invite.spec"""
import sys

NAME = 'telegram-invite'

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    # Шаблон по умолчанию кладём внутрь: при первом запуске он будет
    # скопирован наружу, рядом с исполняемым файлом.
    datas=[('template.txt', '.')],
    hiddenimports=['cryptg'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Тянутся транзитивно, но в консольной программе не нужны и весят много.
    excludes=[
        'tkinter', 'test', 'unittest', 'pydoc_data',
        'numpy', 'PIL', 'matplotlib', 'setuptools', 'pip',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX резко повышает шанс ложного срабатывания антивируса
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
