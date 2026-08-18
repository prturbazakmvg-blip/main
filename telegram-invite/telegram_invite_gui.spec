# -*- mode: python ; coding: utf-8 -*-
"""Сборка оконной версии в .app для macOS.

Запускать: pyinstaller telegram_invite_gui.spec
"""

import os

NAME = 'telegram-invite-gui'
APP_NAME = 'Черновики приглашений.app'
VERSION = '2.5.7'

# Ключ OpenRouter кладёт скрипт сборки — чтобы коллегам не заводить свой.
# В репозитории его нет: файл создаётся на время сборки и стирается.
KEY_FILE = 'bundled_openrouter.key'
extra_datas = [(KEY_FILE, '.')] if os.path.exists(KEY_FILE) else []

a = Analysis(
    ['run_gui.py'],
    pathex=[],
    binaries=[],
    datas=([('template.txt', '.'),
            ('assets/onboarding', 'assets/onboarding')] + extra_datas),
    hiddenimports=['cryptg'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # tkinter здесь нужен, в отличие от консольной сборки
    excludes=['test', 'unittest', 'pydoc_data', 'numpy', 'PIL', 'matplotlib',
              'setuptools', 'pip'],
    noarchive=False,
)

pyz = PYZ(a.pure)

# onedir, а не onefile: внутри .app одним файлом нельзя — macOS этого не любит,
# и PyInstaller 7 сделает такую сборку ошибкой.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # окно, а не терминал
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collected = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=NAME,
)

app = BUNDLE(
    collected,
    name=APP_NAME,
    icon='assets/icon.icns',
    bundle_identifier='ru.agday.telegram-invite',
    info_plist={
        'CFBundleName': 'Черновики приглашений',
        'CFBundleDisplayName': 'Черновики приглашений',
        'CFBundleShortVersionString': VERSION,
        'CFBundleVersion': VERSION,
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
        'LSApplicationCategoryType': 'public.app-category.productivity',
    },
)
