# -*- coding: utf-8 -*-
"""Все пути считаются относительно места, где лежит исполняемый файл.

В собранном PyInstaller-бинарнике __file__ указывает во временную папку
распаковки, которая стирается после выхода. Сессию, креды и csv туда класть
нельзя, поэтому рабочей папкой всегда считаем каталог рядом с exe.
"""
import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, 'frozen', False)


def app_dir() -> Path:
    """Каталог, рядом с которым лежат данные пользователя."""
    if is_frozen():
        exe = Path(sys.executable).resolve()
        # macOS .app: .../Foo.app/Contents/MacOS/foo -> кладём рядом с .app
        parts = exe.parts
        for i in range(len(parts) - 1, 0, -1):
            if parts[i].endswith('.app'):
                return Path(*parts[:i])
        return exe.parent
    return Path(__file__).resolve().parent.parent


def bundled_dir() -> Path:
    """Каталог с файлами, вшитыми в бинарник (шаблон по умолчанию и т.п.)."""
    if is_frozen():
        return Path(getattr(sys, '_MEIPASS', app_dir()))
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    """Постоянное место для входа, настроек и прогресса.

    Раньше всё лежало рядом с приложением, и это ломалось: копий .app на диске
    оказывается несколько (рабочая, dist, release), у каждой своё состояние, а
    macOS запускает ту, на которую нажали. Плюс папку рядом сносит пересборка.
    Теперь место одно и не зависит от того, откуда запущено приложение.
    """
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'TelegramInvite'
    if os.name == 'nt':
        base = os.environ.get('APPDATA') or str(Path.home())
        return Path(base) / 'TelegramInvite'
    return Path.home() / '.telegram-invite'


DATA_DIR = user_data_dir()
LEGACY_DIR = app_dir()          # где данные лежали в прежних версиях
CONTACTS_DIR = DATA_DIR / 'contacts'
STATE_DIR = DATA_DIR / 'state'
TEMPLATES_DIR = DATA_DIR / 'templates'
SESSION_PATH = STATE_DIR / 'telegram'
CREDENTIALS_PATH = DATA_DIR / 'credentials.json'
LOG_PATH = STATE_DIR / 'log.txt'


def ensure_dirs() -> None:
    for d in (DATA_DIR, CONTACTS_DIR, STATE_DIR, TEMPLATES_DIR):
        os.makedirs(d, exist_ok=True)


def migrate_from_legacy() -> list:
    """Переносит вход и настройки из папки рядом с приложением.

    Делается один раз: если в новом месте файла ещё нет, а в старом есть.
    Возвращает список того, что перенесли, — чтобы написать об этом в лог.
    """
    import shutil

    ensure_dirs()

    # Переносить уже нечего — и заглядывать в папку рядом с приложением тоже.
    # Если .app лежит в Загрузках или на Рабочем столе, любое обращение туда
    # поднимает системный запрос доступа к папке, а после каждой пересборки
    # macOS спрашивает заново.
    if CREDENTIALS_PATH.exists() and SESSION_PATH.with_suffix('.session').exists():
        return []

    if LEGACY_DIR.resolve() == DATA_DIR.resolve():
        return []

    moved = []

    legacy_creds = LEGACY_DIR / 'credentials.json'
    if legacy_creds.exists() and not CREDENTIALS_PATH.exists():
        shutil.copy2(legacy_creds, CREDENTIALS_PATH)
        moved.append('ключи')

    for name in ('telegram.session', 'telegram.session-journal'):
        legacy = LEGACY_DIR / 'state' / name
        target = STATE_DIR / name
        if legacy.exists() and not target.exists():
            shutil.copy2(legacy, target)
            if name.endswith('.session'):
                moved.append('вход в Telegram')

    legacy_state = LEGACY_DIR / 'state'
    if legacy_state.is_dir():
        for item in legacy_state.glob('*.txt'):
            target = STATE_DIR / item.name
            if not target.exists():
                shutil.copy2(item, target)
        for item in legacy_state.glob('daily.json'):
            target = STATE_DIR / item.name
            if not target.exists():
                shutil.copy2(item, target)

    legacy_template = LEGACY_DIR / 'template.txt'
    target_template = DATA_DIR / 'template.txt'
    if legacy_template.exists() and not target_template.exists():
        shutil.copy2(legacy_template, target_template)
        moved.append('текст приглашения')

    legacy_templates = LEGACY_DIR / 'templates'
    if legacy_templates.is_dir():
        for item in legacy_templates.glob('*.txt'):
            target = TEMPLATES_DIR / item.name
            if not target.exists():
                shutil.copy2(item, target)

    return moved


def safe_filename(name: str) -> str:
    """Название группы из таблицы -> имя файла, безопасное на Windows и macOS."""
    cleaned = []
    for ch in name.strip():
        if ch in '<>:"/\\|?*' or ord(ch) < 32:
            cleaned.append('_')
        elif ch.isspace():
            cleaned.append('_')
        else:
            cleaned.append(ch)
    result = ''.join(cleaned).strip('._')[:50].lower()
    return result or 'без_названия'
