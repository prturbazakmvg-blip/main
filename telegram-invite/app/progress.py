# -*- coding: utf-8 -*-
"""Учёт уже обработанных контактов и дневных лимитов.

Хранится в state/. Благодаря этому повторный запуск не резолвит заново
username-ы, которые уже разобраны — а именно резолв username-ов и упирается
в лимиты Telegram.
"""
import json
from datetime import date
from pathlib import Path
from typing import Set

from .paths import STATE_DIR, ensure_dirs, safe_filename

DAILY_PATH = STATE_DIR / 'daily.json'


def _done_path(contacts_file: str) -> Path:
    stem = safe_filename(Path(contacts_file).stem)
    return STATE_DIR / 'done_{}.txt'.format(stem)


def load_done(contacts_file: str) -> Set[str]:
    path = _done_path(contacts_file)
    if not path.exists():
        return set()
    try:
        with open(path, encoding='utf-8') as f:
            return {line.strip().lower() for line in f if line.strip()}
    except OSError:
        return set()


def mark_done(contacts_file: str, username: str) -> None:
    ensure_dirs()
    path = _done_path(contacts_file)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(username.strip().lower() + '\n')
        f.flush()


def unmark_done(contacts_file: str, username: str) -> bool:
    """Снимает отметку «уже писали» с одного контакта.

    Нужна, когда черновик удалили руками или отправлять всё-таки надо.
    """
    path = _done_path(contacts_file)
    if not path.exists():
        return False
    wanted = username.strip().lower()
    try:
        with open(path, encoding='utf-8') as f:
            kept = [line for line in f if line.strip().lower() != wanted]
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(kept)
    except OSError:
        return False
    return True


def reset_done(contacts_file: str) -> bool:
    path = _done_path(contacts_file)
    if path.exists():
        path.unlink()
        return True
    return False


def _load_daily() -> dict:
    if not DAILY_PATH.exists():
        return {}
    try:
        with open(DAILY_PATH, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def today_count() -> int:
    data = _load_daily()
    try:
        return int(data.get(date.today().isoformat(), 0))
    except (TypeError, ValueError):
        return 0


def bump_today(amount: int = 1) -> int:
    ensure_dirs()
    data = _load_daily()
    key = date.today().isoformat()
    try:
        current = int(data.get(key, 0))
    except (TypeError, ValueError):
        current = 0
    data[key] = current + amount

    # Держим только последние 30 дней, чтобы файл не рос.
    for stale in sorted(data.keys())[:-30]:
        data.pop(stale, None)

    with open(DAILY_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return data[key]
