# -*- coding: utf-8 -*-
"""Выгрузка контактов из Google-таблицы в csv-файлы по группам."""
import csv
import re
from typing import Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .net import ssl_context
from .paths import CONTACTS_DIR, ensure_dirs, safe_filename

USER_AGENT = 'Mozilla/5.0 (compatible; telegram-invite/2.0)'

# Заголовки, по которым пытаемся найти колонки автоматически.
# Сравнение строгое (полное совпадение или начало заголовка), иначе легко
# поймать не ту колонку: «Готов анонсить» не должен сойти за «Готово».
HEADER_HINTS = {
    'name': ('имя', 'name', 'фио', 'контактное лицо'),
    'telegram': ('контакт', 'telegram', 'телеграм', 'тг', 'username', 'ник'),
    'group': ('кто напишет', 'ответственный', 'ответственная', 'группа', 'менеджер', 'owner'),
    'done': ('позвали', 'готово', 'отправлено', 'обработан', 'done'),
    # необязательная: персональный текст письма прямо в таблице
    'message': ('сообщение', 'текст', 'письмо', 'message', 'приглашение'),
}

# Колонки, без которых работать нельзя
REQUIRED = ('name', 'telegram', 'group', 'done')

USERNAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]{3,31}$')


class SheetError(Exception):
    pass


def to_csv_url(sheet_url: str) -> str:
    """Ссылка на таблицу -> ссылка на csv-экспорт того же листа."""
    sheet_url = sheet_url.strip()
    if '/d/' not in sheet_url:
        raise SheetError('Ссылка не похожа на Google-таблицу: нет /d/ в адресе')

    sheet_id = sheet_url.split('/d/')[1].split('/')[0]
    if not sheet_id:
        raise SheetError('Не удалось выделить идентификатор таблицы из ссылки')

    csv_url = 'https://docs.google.com/spreadsheets/d/{}/export?format=csv'.format(sheet_id)

    if 'gid=' in sheet_url:
        gid = sheet_url.split('gid=')[1].split('&')[0].split('#')[0].split('/')[0]
        if gid.isdigit():
            csv_url += '&gid={}'.format(gid)

    return csv_url


def download(sheet_url: str, timeout: int = 30) -> str:
    csv_url = to_csv_url(sheet_url)
    request = Request(csv_url, headers={'User-Agent': USER_AGENT})

    try:
        with urlopen(request, timeout=timeout, context=ssl_context()) as response:
            content_type = response.headers.get('Content-Type', '')
            raw = response.read()
    except HTTPError as e:
        if e.code in (401, 403, 404):
            raise SheetError(
                'Google вернул {}. Откройте доступ к таблице по ссылке '
                '(Настройки доступа -> Все, у кого есть ссылка -> Читатель).'.format(e.code)
            )
        raise SheetError('Google вернул ошибку {}'.format(e.code))
    except URLError as e:
        raise SheetError('Не удалось соединиться с Google: {}'.format(e.reason))

    # Закрытая таблица отдаёт 200 и html страницу логина. Без этой проверки
    # она молча парсится как csv и получается пустой результат.
    if 'text/html' in content_type.lower():
        raise SheetError(
            'Вместо таблицы пришла html-страница входа. Откройте доступ к таблице '
            'по ссылке (Настройки доступа -> Все, у кого есть ссылка -> Читатель).'
        )

    for encoding in ('utf-8-sig', 'utf-8', 'cp1251'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def detect_columns(headers: List[str], fallback: Dict[str, int]):
    """Ищем колонки по заголовкам, при неудаче берём индексы из настроек.

    Возвращает (индексы, пояснение по каждой колонке для показа пользователю).
    """
    resolved = dict(fallback)
    explained = {}
    lowered = [h.strip().lower().rstrip('?:. ') for h in headers]

    for key, hints in HEADER_HINTS.items():
        found = None
        for index, header in enumerate(lowered):
            if not header:
                continue
            if header in hints or any(header.startswith(hint) for hint in hints):
                found = index
                break

        if found is not None:
            resolved[key] = found
            explained[key] = (found, headers[found].strip(), 'по заголовку')
        elif key in REQUIRED:
            index = resolved[key]
            name = headers[index].strip() if index < len(headers) else '?'
            explained[key] = (index, name, 'по номеру из настроек')
        else:
            # необязательной колонки просто нет — это нормально
            resolved.pop(key, None)

    return resolved, explained


def clean_username(raw: str) -> str:
    value = (raw or '').strip()
    if not value:
        return ''
    value = re.sub(r'^https?://', '', value, flags=re.IGNORECASE)
    value = re.sub(r'^(www\.)?t(elegram)?\.me/', '', value, flags=re.IGNORECASE)
    value = value.split('?')[0].split('/')[0]
    value = value.lstrip('@').strip()
    return value


class Skipped(object):
    """Копит однотипные пропуски, чтобы не сыпать тысячей одинаковых строк."""

    def __init__(self):
        self.counts = {}
        self.examples = {}

    def add(self, reason: str, row_num: int, detail: str = ''):
        self.counts[reason] = self.counts.get(reason, 0) + 1
        examples = self.examples.setdefault(reason, [])
        if len(examples) < 3:
            examples.append('строка {}{}'.format(row_num, ': ' + detail if detail else ''))

    def as_lines(self) -> List[str]:
        lines = []
        for reason in sorted(self.counts, key=lambda r: -self.counts[r]):
            lines.append('{} — {} шт. (например {})'.format(
                reason, self.counts[reason], '; '.join(self.examples[reason])))
        return lines

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def parse(content: str, columns: Dict[str, int]):
    """Разбирает csv. Возвращает (группы, замечания, пояснение по колонкам)."""
    lines = content.splitlines()
    if not lines:
        raise SheetError('Таблица пустая')

    reader = csv.reader(lines)
    try:
        headers = next(reader)
    except StopIteration:
        raise SheetError('Таблица пустая')

    resolved, explained = detect_columns(headers, columns)
    needed = max(resolved[key] for key in REQUIRED)
    message_col = resolved.get('message')
    grouped = {}
    skipped = Skipped()
    seen = set()

    for row_num, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) <= needed:
            skipped.add('в строке меньше колонок, чем нужно', row_num)
            continue

        if row[resolved['done']].strip().upper() == 'TRUE':
            skipped.add('отмечены как уже позванные', row_num)
            continue

        raw_contact = row[resolved['telegram']]
        username = clean_username(raw_contact)
        if not username:
            skipped.add('не указан telegram', row_num)
            continue
        if not USERNAME_RE.match(username):
            skipped.add('в колонке telegram не username', row_num, '"{}"'.format(raw_contact.strip()[:40]))
            continue
        if username.lower() in seen:
            skipped.add('дубль username', row_num, '@' + username)
            continue
        seen.add(username.lower())

        name = row[resolved['name']].strip()
        group = row[resolved['group']].strip() or 'не назначено'
        message = ''
        if message_col is not None and message_col < len(row):
            message = row[message_col].strip()

        grouped.setdefault(group, []).append([username, name, message, row_num])

    return grouped, skipped, explained


def write_groups(grouped: Dict[str, List[List[str]]]):
    """Пишет по csv на группу в contacts/. Возвращает [(путь, группа, сколько)]."""
    ensure_dirs()
    created = []
    used_names = {}

    for group in sorted(grouped, key=lambda g: -len(grouped[g])):
        rows = grouped[group]
        base = safe_filename(group)
        # Две разные группы могут дать одинаковое безопасное имя файла.
        count = used_names.get(base, 0)
        used_names[base] = count + 1
        file_name = base if count == 0 else '{}_{}'.format(base, count + 1)

        path = CONTACTS_DIR / '{}.csv'.format(file_name)
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['username', 'name'])
            writer.writerows([row[0], row[1]] for row in rows)
        created.append((str(path), group, len(rows)))

    return created


def refresh(sheet_url: str, columns: Dict[str, int]):
    content = download(sheet_url)
    grouped, skipped, explained = parse(content, columns)
    if not grouped:
        raise SheetError(
            'В таблице не нашлось ни одного подходящего контакта. '
            'Проверьте, те ли колонки берутся (Настройки).'
        )
    created = write_groups(grouped)
    return created, grouped, skipped, explained
