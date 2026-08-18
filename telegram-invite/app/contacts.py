# -*- coding: utf-8 -*-
"""Чтение списков контактов из csv."""
import csv
from pathlib import Path
from typing import List

from . import sheets
from .paths import CONTACTS_DIR, ensure_dirs
from .sheets import USERNAME_RE, clean_username

HEADER_MARKERS = ('username', 'telegram', 'тг', 'телеграм', 'ник')


class Contact(object):
    # source — откуда взят контакт: отметка «обработано» ложится именно туда
    # peer  — готовый InputPeer, если человек уже есть в диалогах (папки
    #         Telegram). Тогда его не надо искать через ResolveUsername
    # key   — под чем помним «уже обработан»: username или id для тех,
    #         у кого username нет вовсе
    # message — персональный текст из колонки таблицы, если он там есть
    # kind — человек / группа / канал: черновик можно положить в любой чат
    __slots__ = ('username', 'name', 'row_num', 'source', 'peer', 'key',
                 'message', 'kind')

    def __init__(self, username, name, row_num, source='', peer=None, key='',
                 message='', kind='человек'):
        self.username = username
        self.name = name
        self.row_num = row_num
        self.source = source
        self.peer = peer
        self.key = key or (username or '').lower()
        self.message = message
        self.kind = kind

    @property
    def label(self):
        return '@' + self.username if self.username else (self.name or self.key)

    def __repr__(self):
        return '<Contact @{} {}>'.format(self.username, self.name)


def available_files() -> List[str]:
    ensure_dirs()
    files = sorted(CONTACTS_DIR.glob('*.csv'))
    # csv, лежащие рядом с программой от прежней версии, тоже показываем.
    files += sorted(p for p in CONTACTS_DIR.parent.glob('*.csv'))
    seen = set()
    result = []
    for path in files:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            result.append(str(path))
    return result


def read(file_name: str, delimiter: str = ','):
    """Возвращает (список контактов, список замечаний)."""
    contacts = []
    warnings = []

    try:
        with open(file_name, newline='', encoding='utf-8-sig') as f:
            rows = list(csv.reader(f, delimiter=delimiter))
    except UnicodeDecodeError:
        with open(file_name, newline='', encoding='cp1251') as f:
            rows = list(csv.reader(f, delimiter=delimiter))
    except OSError as e:
        raise RuntimeError('Не удалось прочитать {}: {}'.format(file_name, e))

    if not rows:
        return contacts, ['Файл {} пустой'.format(Path(file_name).name)]

    start = 0
    first_cell = (rows[0][0] if rows[0] else '').strip().lower()
    if any(marker in first_cell for marker in HEADER_MARKERS):
        start = 1

    seen = set()

    for row_num, row in enumerate(rows[start:], start=start + 1):
        if not row or not any(cell.strip() for cell in row):
            continue

        username = clean_username(row[0])
        if not username:
            warnings.append('строка {}: пустой username'.format(row_num))
            continue
        if not USERNAME_RE.match(username):
            warnings.append('строка {}: "{}" не похож на username'.format(row_num, username))
            continue
        if username.lower() in seen:
            warnings.append('строка {}: @{} уже был выше — пропущен'.format(row_num, username))
            continue
        seen.add(username.lower())

        name = row[1].strip() if len(row) > 1 else ''
        if not name:
            warnings.append('строка {}: у @{} нет имени, в тексте будет пусто'.format(row_num, username))

        contacts.append(Contact(username, name, row_num, source=str(file_name)))

    return contacts, warnings


def read_folder(folder):
    """Читает все csv из папки по алфавиту. Возвращает (контакты, замечания, файлы)."""
    folder = Path(folder)
    files = sorted(folder.glob('*.csv'))
    if not files:
        return [], ['В папке {} нет ни одного csv'.format(folder.name)], []

    contacts = []
    warnings = []
    seen = set()

    for path in files:
        try:
            items, file_warnings = read(str(path))
        except RuntimeError as e:
            warnings.append(str(e))
            continue

        warnings.extend('{}: {}'.format(path.name, w) for w in file_warnings)
        for contact in items:
            # Один и тот же человек может стоять в двух списках сразу
            if contact.username.lower() in seen:
                warnings.append('{}: @{} уже есть в другом списке — пропущен'.format(
                    path.name, contact.username))
                continue
            seen.add(contact.username.lower())
            contacts.append(contact)

    return contacts, warnings, [str(p) for p in files]


def from_sheet(sheet_url, columns):
    """Живое чтение таблицы: ничего не сохраняем на диск.

    Возвращает ({группа: [Contact]}, пропущенные, пояснение по колонкам).
    Таблицу правят постоянно, поэтому промежуточные csv только вводят
    в заблуждение — берём её как есть на момент нажатия кнопки.
    """
    content = sheets.download(sheet_url)
    grouped, skipped, explained = sheets.parse(content, columns)

    result = {}
    for group, rows in grouped.items():
        source = 'таблица · {}'.format(group)
        result[group] = [
            Contact(username=username, name=name, row_num=row_num,
                    source=source, message=message)
            for username, name, message, row_num in rows
        ]
    return result, skipped, explained
