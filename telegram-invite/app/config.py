# -*- coding: utf-8 -*-
"""Чтение и запись credentials.json, значения по умолчанию."""
import json
import shutil

from .paths import (CREDENTIALS_PATH, DATA_DIR, SESSION_PATH, bundled_dir,
                    ensure_dirs)

# Задержки между черновиками, секунды. Каждый черновик для незнакомого
# аккаунта — это вызов contacts.ResolveUsername, который жёстко лимитируется.
DEFAULT_MIN_DELAY = 20
DEFAULT_MAX_DELAY = 45

# Мягкий дневной потолок. Не техническое ограничение Telegram, а порог,
# после которого мы предупреждаем пользователя.
DEFAULT_DAILY_LIMIT = 150

# Индексы колонок в Google-таблице (0-based). Используются, если не нашли
# колонки по заголовкам.
DEFAULT_COLUMNS = {
    'name': 2,       # Имя
    'telegram': 5,   # Ссылка t.me или @username
    'group': 6,      # Ответственный / группа
    'done': 7,       # TRUE = уже обработан, пропускаем
}

DEFAULTS = {
    'api_id': '',
    'api_hash': '',
    'google_file': '',
    # Тексты приглашений живут здесь же, рядом с остальными настройками:
    # отдельный файл пользователю выбирать незачем.
    # messages — список {'title', 'text'}, рассылается выбранная вкладка.
    'message': '',            # старое поле, читается для переноса
    'messages': [],
    'active_message': 0,
    # что было выбрано в прошлый раз — чтобы окно открывалось там же
    # OpenRouter — для определения типа контакта по переписке
    'openrouter_key': '',
    'openrouter_model': 'google/gemini-2.5-flash-lite',
    # чем занимается владелец аккаунта — без этого модель не понимает,
    # кто в переписке подрядчик, а кто заказчик
    'openrouter_about': '',
    # названия своей компании через запятую: по ним видно, кто «коллеги»
    'openrouter_company': '',
    # Доводка текста под каждого включена сразу: ради неё программа и
    # делалась, а выключенной галочкой коллега получит голую рассылку и
    # решит, что так и задумано. Выключить можно в один клик.
    'personalize': True,
    'batch_size': 10,         # сколько черновиков за один заход
    'last_source': 'sheet',
    'last_group': '',
    'last_folder': '',
    'last_segment': '',       # тип контакта, по которому рассылали в прошлый раз
    # кому не писать: ключи контактов, снятые галочкой вручную
    'excluded': [],
    # Через сколько дней приглашение считается прошлогодним. Конференция
    # ежегодная: звали в октябре — весной можно звать снова.
    'invite_window_days': 180,
    'min_delay_seconds': DEFAULT_MIN_DELAY,
    'max_delay_seconds': DEFAULT_MAX_DELAY,
    'daily_limit': DEFAULT_DAILY_LIMIT,
    'columns': DEFAULT_COLUMNS,
    # Как подписываться в письме незнакомому: «Меня {sender_name} зовут,
    # я {sender_intro}». Заполняется при входе из профиля Telegram.
    'sender_name': '',
    'sender_intro': '',
    'sender_gender': '',
}


def bundled_key() -> str:
    """Ключ OpenRouter, вшитый в сборку, — чтобы коллегам не заводить свой.

    В исходниках этого файла нет: его создаёт скрипт сборки и сразу стирает.
    Свой ключ в «Настройках» всегда важнее вшитого.
    """
    try:
        return (bundled_dir() / 'bundled_openrouter.key').read_text(
            encoding='utf-8').strip()
    except OSError:
        return ''


def openrouter_key(config: dict) -> str:
    return (config.get('openrouter_key') or '').strip() or bundled_key()


def openrouter_model(config: dict) -> str:
    return (config.get('openrouter_model') or '').strip() or \
        DEFAULTS['openrouter_model']


def load() -> dict:
    ensure_dirs()
    config = dict(DEFAULTS)
    config['columns'] = dict(DEFAULT_COLUMNS)

    if CREDENTIALS_PATH.exists():
        try:
            with open(CREDENTIALS_PATH, encoding='utf-8') as f:
                stored = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            raise RuntimeError(
                'Не удалось прочитать {}: {}'.format(CREDENTIALS_PATH.name, e)
            )
        if not isinstance(stored, dict):
            raise RuntimeError('{} должен содержать JSON-объект'.format(CREDENTIALS_PATH.name))
        columns = stored.pop('columns', None)
        config.update(stored)
        if isinstance(columns, dict):
            config['columns'].update(columns)

    return config


def save(config: dict) -> None:
    ensure_dirs()
    # Пишем ровно то, что в настройках. Раньше здесь стирался ключ, совпавший
    # с вшитым в сборку, — чтобы он не оседал в чужом credentials.json. Но в
    # чужой конфиг он и не попадает: поле в «Настройках» показывает только
    # свой ключ, а вшитый подставляется при чтении. Зато у того, кто собирает
    # программу, ключ совпадает с вшитым всегда — и стирался при первом же
    # сохранении, унося с собой и следующую сборку.
    with open(CREDENTIALS_PATH, 'w', encoding='utf-8') as f:
        json.dump(dict(config), f, ensure_ascii=False, indent=2)


def is_complete(config: dict) -> bool:
    return bool(str(config.get('api_id', '')).strip()) and bool(str(config.get('api_hash', '')).strip())


def delay_range(config: dict):
    """Возвращает (min, max) в секундах, с защитой от слишком агрессивных значений."""
    try:
        low = int(config.get('min_delay_seconds', DEFAULT_MIN_DELAY))
    except (TypeError, ValueError):
        low = DEFAULT_MIN_DELAY
    try:
        high = int(config.get('max_delay_seconds', DEFAULT_MAX_DELAY))
    except (TypeError, ValueError):
        high = DEFAULT_MAX_DELAY

    low = max(5, low)
    high = max(low + 1, high)
    return low, high


def invite_window_days(config: dict) -> int:
    try:
        value = int(config.get('invite_window_days', 180))
    except (TypeError, ValueError):
        return 180
    return value if value > 0 else 180


def daily_limit(config: dict) -> int:
    try:
        value = int(config.get('daily_limit', DEFAULT_DAILY_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_LIMIT
    return value if value > 0 else DEFAULT_DAILY_LIMIT


def migrate_legacy_session() -> bool:
    """Переносит max.session от прежней версии, чтобы не логиниться заново."""
    ensure_dirs()
    new_session = SESSION_PATH.with_suffix('.session')
    if new_session.exists():
        return False
    for legacy_name in ('max.session', 'anon.session'):
        legacy = DATA_DIR / legacy_name
        if legacy.exists():
            shutil.copy2(legacy, new_session)
            return True
    return False


def messages(config) -> list:
    """Список вкладок с текстами. Переносит старое одиночное поле."""
    items = config.get('messages') or []
    clean = [
        {'title': str(m.get('title') or 'Вариант'), 'text': str(m.get('text') or '')}
        for m in items if isinstance(m, dict)
    ]
    if clean:
        return clean

    legacy = (config.get('message') or '').strip()
    if legacy:
        # в прежней версии варианты разделялись строкой из трёх дефисов
        import re
        parts = [p.strip() for p in re.split(r'^-{3,}\s*$', legacy, flags=re.MULTILINE)]
        parts = [p for p in parts if p]
        return [{'title': 'Вариант {}'.format(i), 'text': p}
                for i, p in enumerate(parts, start=1)]
    return []


def active_index(config, count) -> int:
    try:
        index = int(config.get('active_message', 0))
    except (TypeError, ValueError):
        index = 0
    return index if 0 <= index < count else 0
