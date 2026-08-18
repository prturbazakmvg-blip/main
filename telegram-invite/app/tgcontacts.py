# -*- coding: utf-8 -*-
"""Выгрузка диалогов из Telegram и хранение их типов.

Тип определяется отдельно, через OpenRouter, и запоминается — чтобы не
платить за одну и ту же переписку дважды.
"""
import asyncio
import json
import re
from datetime import datetime

from telethon import utils
from telethon.errors.rpcerrorlist import FloodWaitError
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.contacts import GetBlockedRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl import types

from . import names
from .paths import STATE_DIR, ensure_dirs

STORE_PATH = STATE_DIR / 'contacts.json'

PERSON = 'человек'
GROUP = 'группа'
CHANNEL = 'канал'

# Служебные отправители: Telegram (777000), уведомления о входе и т.п.
SERVICE_IDS = {777000, 42777, 333000, 1087968824}


class FloodGuard:
    """Общий тормоз на чтение из Telegram.

    Читателей несколько, а лимит у аккаунта один: если Telegram сказал
    «подожди», ждать должны все сразу, иначе остальные продолжат стучаться
    и продлят наказание. Долгое ожидание означает, что аккаунт придержали
    всерьёз, — тогда прогон лучше остановить.
    """

    def __init__(self, on_wait=None, limit=300):
        self.free = asyncio.Event()
        self.free.set()
        self.on_wait = on_wait
        self.limit = limit          # дольше — не ждём, а останавливаемся
        self.waits = 0
        self.seconds = 0
        self.stopped = ''

    async def ready(self):
        await self.free.wait()

    async def pause(self, seconds):
        if not self.free.is_set():
            # кто-то уже ждёт за всех — просто ждём вместе с ним
            await self.free.wait()
            return
        self.free.clear()
        self.waits += 1
        self.seconds += seconds
        if self.on_wait is not None:
            self.on_wait(seconds)
        try:
            await asyncio.sleep(seconds + 2)
        finally:
            self.free.set()


async def call_slowly(action, guard=None, attempts=3):
    """Запрос с оглядкой на FLOOD_WAIT. None — не получилось."""
    for _attempt in range(attempts):
        if guard is not None:
            if guard.stopped:
                return None
            await guard.ready()
        try:
            return await action()
        except FloodWaitError as e:
            if guard is None or e.seconds > guard.limit:
                if guard is not None:
                    guard.stopped = (
                        'Telegram просит подождать {} мин — аккаунт упёрся в '
                        'лимит. Продолжать сейчас нельзя.'.format(
                            max(1, e.seconds // 60)))
                return None
            await guard.pause(e.seconds)
        except Exception:
            return None
    return None


def is_obsolete(entity):
    """Остаток от группы, переехавшей в супергруппу.

    Клиент Telegram такие чаты прячет, а API продолжает их отдавать —
    отсюда «дубли» вроде двух одинаковых групп в списке.
    """
    return bool(getattr(entity, 'migrated_to', None)) or \
        bool(getattr(entity, 'deactivated', False))


def is_dead(entity):
    """Удалённый аккаунт: имени нет, писать некому."""
    return bool(getattr(entity, 'deleted', False))


def is_nameless(entity):
    """Ни имени, ни username — опознать и обратиться не по чему.

    Так выглядят удалённые аккаунты; флаг deleted у них стоит, но полагаться
    только на флаг не стоит: строка «без имени» в списке бесполезна в любом
    случае.
    """
    name = (utils.get_display_name(entity) or '').strip()
    username = (getattr(entity, 'username', '') or '').strip()
    return not name and not username


def is_dead_record(item):
    """То же самое, но для уже сохранённой записи."""
    title = (item.get('title') or '').strip()
    return title in ('', 'без имени') and not (item.get('username') or '').strip()


async def blocked_ids(client, limit=100, max_pages=20):
    """Кого вы заблокировали. Один-два запроса, зато список чистый."""
    found = set()
    offset = 0
    for _ in range(max_pages):
        try:
            page = await client(GetBlockedRequest(offset=offset, limit=limit))
        except Exception:
            break
        users = list(getattr(page, 'users', None) or [])
        chats = list(getattr(page, 'chats', None) or [])
        for item in users + chats:
            found.add(item.id)
        if len(users) + len(chats) < limit:
            break
        offset += limit
    return found


def is_service(entity):
    """Бот, служебный аккаунт или помеченный Telegram как мошеннический."""
    if getattr(entity, 'bot', False):
        return True
    if getattr(entity, 'id', None) in SERVICE_IDS:
        return True
    if getattr(entity, 'scam', False) or getattr(entity, 'fake', False):
        return True
    if getattr(entity, 'support', False):
        return True
    return False


def load_store():
    if not STORE_PATH.exists():
        return {}
    try:
        with open(STORE_PATH, encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        clean = {}
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            # записи от прежних версий: удалённые аккаунты без имени
            if is_dead_record(value):
                continue
            # типы переименовывались: «сотрудник» -> «коллеги»,
            # «ит-компания» -> «ит и digital»
            from .openrouter import RENAMED
            if value.get('type') in RENAMED:
                value['type'] = RENAMED[value['type']]
            if value.get('model_type') in RENAMED:
                value['model_type'] = RENAMED[value['model_type']]
            # раньше ручную правку узнавали по тексту причины
            if value.get('why') == 'выставлено вручную' and not value.get('by'):
                value['by'] = 'человек'
            clean[key] = value
        if len(clean) != len(data):
            # чистим файл один раз, чтобы мусор не тянулся из запуска в запуск
            try:
                save_store(clean)
            except OSError:
                pass
        return clean
    except (OSError, json.JSONDecodeError):
        return {}


def save_store(store):
    ensure_dirs()
    with open(STORE_PATH, 'w', encoding='utf-8') as f:
        json.dump(store, f, ensure_ascii=False, indent=1)


async def download(client, on_progress=None, should_stop=None, guard=None):
    """Все живые диалоги аккаунта. Возвращает (список, что отсеяли).

    iter_dialogs сам разбивает выборку на страницы, отдельных запросов на
    каждый чат не делается. Если Telegram попросит подождать, ждём: обрыв
    на середине оставил бы список неполным.
    """
    blocked = await blocked_ids(client)
    items = []
    dropped = {'боты и служебные': 0, 'удалённые аккаунты': 0,
               'заблокированные': 0, 'пустые чаты': 0, 'переехавшие группы': 0}

    async for dialog in _dialogs(client, guard):
        if should_stop is not None and should_stop():
            break

        entity = dialog.entity
        if is_service(entity):
            dropped['боты и служебные'] += 1
            continue
        if is_obsolete(entity):
            dropped['переехавшие группы'] += 1
            continue
        if is_dead(entity) or is_nameless(entity):
            dropped['удалённые аккаунты'] += 1
            continue
        if getattr(entity, 'id', None) in blocked:
            dropped['заблокированные'] += 1
            continue
        # ни одного сообщения — в такой чат писать не о чем
        if getattr(dialog, 'message', None) is None:
            dropped['пустые чаты'] += 1
            continue

        if dialog.is_user:
            kind = PERSON
        elif dialog.is_channel and not getattr(entity, 'megagroup', False):
            kind = CHANNEL
        else:
            kind = GROUP

        items.append({
            'id': str(dialog.id),
            'title': utils.get_display_name(entity) or 'без имени',
            'username': getattr(entity, 'username', '') or '',
            'kind': kind,
            'unread': dialog.unread_count,
            'date': dialog.date.strftime('%Y-%m-%d') if dialog.date else '',
        })
        if on_progress is not None and len(items) % 50 == 0:
            on_progress(len(items))

    if on_progress is not None:
        on_progress(len(items))
    return items, {k: v for k, v in dropped.items() if v}


async def _dialogs(client, guard=None):
    """iter_dialogs, переживающий короткий FLOOD_WAIT."""
    while True:
        try:
            async for dialog in client.iter_dialogs():
                yield dialog
            return
        except FloodWaitError as e:
            if guard is None or e.seconds > getattr(guard, 'limit', 300):
                raise
            await guard.pause(e.seconds)


async def warm_entities(client, on_progress=None, guard=None):
    """Прогревает кэш сущностей: один проход по диалогам.

    Без него Telegram отвечает «Could not find the input entity»: чтобы
    прочитать переписку, клиенту нужен access_hash собеседника, а он живёт
    в кэше сессии и со временем теряется. Один проход чинит все чаты сразу.
    """
    seen = 0
    async for _dialog in _dialogs(client, guard):
        seen += 1
        if on_progress is not None and seen % 500 == 0:
            on_progress(seen)
    return seen


async def history(client, dialog_id, limit=50, guard=None):
    """Последние сообщения с датами: [(моё?, дата, текст)], новые первыми.

    None — если прочитать не удалось; пустой список — если сообщений нет.
    Разница важна: ошибку нельзя принимать за пустой чат.

    Даты нужны, чтобы отличить «звал на эту конференцию» от «звал на
    прошлую год назад»: конференция ежегодная, и через год человека зовут
    заново.
    """
    messages = await call_slowly(
        lambda: client.get_messages(int(dialog_id), limit=limit), guard)
    if messages is None:
        return None
    return [(bool(getattr(m, 'out', False)), getattr(m, 'date', None),
             (getattr(m, 'message', '') or '').strip())
            for m in (messages or [])]


def as_transcript(rows, limit=20, max_chars=2500):
    """Те же сообщения, но в виде выжимки для модели и для приветствия."""
    lines = []
    for out, _date, text in list(rows)[:limit]:
        if not text:
            continue
        lines.append('{}: {}'.format('я' if out else 'он',
                                     text.replace('\n', ' ')[:300]))
    lines.reverse()          # в переписке старые сверху
    joined = '\n'.join(lines)
    return joined[-max_chars:] if len(joined) > max_chars else joined


async def transcript(client, dialog_id, limit=20, max_chars=2500, guard=None):
    """Короткая выжимка переписки для классификации.

    Берём немного последних сообщений: для «кто это» хватает, а платить за
    длинный контекст незачем. None — прочитать не вышло, '' — сообщений нет.
    """
    lines = []
    messages = await call_slowly(
        lambda: client.get_messages(int(dialog_id), limit=limit), guard)
    if messages is None:
        return None

    for message in reversed(messages or []):
        text = (getattr(message, 'message', '') or '').strip()
        if not text:
            continue
        who = 'я' if message.out else 'он'
        lines.append('{}: {}'.format(who, text.replace('\n', ' ')[:300]))

    joined = '\n'.join(lines)
    return joined[-max_chars:] if len(joined) > max_chars else joined


async def about(client, dialog_id, kind, guard=None):
    """Описание профиля: «о себе» у человека, описание у группы или канала.

    Отдельный запрос на чат, поэтому дёргаем только при разборе типа —
    зато модель получает то, что человек сам о себе написал.
    """
    entity = await call_slowly(lambda: client.get_entity(int(dialog_id)), guard)
    if entity is None:
        return ''

    if kind == PERSON:
        full = await call_slowly(lambda: client(GetFullUserRequest(entity)), guard)
        text = getattr(getattr(full, 'full_user', None), 'about', '') or '' if full else ''
    elif isinstance(entity, types.Channel):
        full = await call_slowly(lambda: client(GetFullChannelRequest(entity)), guard)
        text = getattr(getattr(full, 'full_chat', None), 'about', '') or '' if full else ''
    else:
        return ''          # у обычных групп описания нет

    return ' '.join(text.split())[:400]


# Из заголовка чата имя для обращения: скобки с компанией и должностью
# сюда попадать не должны
TITLE_NOISE = re.compile(r'\[[^\]]*\]|\([^)]*\)|[|/].*$')
CYRILLIC_NAME = re.compile(r'^[А-ЯЁ][а-яё\-]{1,19}$')


# Разговоры про конференцию вне личной переписки: человек мог написать о ней
# в общем чате. Глобальный поиск Telegram находит это за несколько запросов —
# обходить три тысячи диалогов ради того же результата незачем.
EVENT_TALK_PATH = STATE_DIR / 'event_talk.json'


def load_event_talk():
    """{id человека: [что писал про конференцию]} или пусто."""
    try:
        with open(EVENT_TALK_PATH, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    talk = data.get('talk') if isinstance(data, dict) else None
    return talk if isinstance(talk, dict) else {}


def event_talk_age(now=None):
    """Сколько дней назад собирали. None — не собирали ни разу."""
    try:
        with open(EVENT_TALK_PATH, encoding='utf-8') as f:
            when = json.load(f).get('when', '')
        stamp = datetime.fromisoformat(when)
    except (OSError, ValueError, TypeError):
        return None
    if now is None:
        now = datetime.now(stamp.tzinfo) if stamp.tzinfo else datetime.now()
    return max(0, (now - stamp).days)


def save_event_talk(talk):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVENT_TALK_PATH, 'w', encoding='utf-8') as f:
        json.dump({'when': datetime.now().isoformat(), 'talk': talk}, f,
                  ensure_ascii=False, indent=1)


async def search_event_talk(client, words, guard=None, limit=300):
    """Кто и что писал про конференцию — по всем чатам сразу.

    Свои сообщения пропускаем: интересна чужая реакция. Возвращает
    {id отправителя: [тексты]}.
    """
    talk = {}
    for word in words:
        found = await call_slowly(
            lambda w=word: _search_all(client, w, limit), guard)
        for sender, text in (found or ()):
            texts = talk.setdefault(str(sender), [])
            if text not in texts:
                texts.append(text[:600])
    return talk


async def _search_all(client, word, limit):
    rows = []
    async for message in client.iter_messages(None, search=word, limit=limit):
        text = (getattr(message, 'message', '') or '').strip()
        sender = getattr(message, 'sender_id', None)
        if not text or sender is None or getattr(message, 'out', False):
            continue
        rows.append((sender, text))
    return rows


def item_key(item):
    """Под чем помним контакт: тот же ключ, что у адресата рассылки."""
    username = (item.get('username') or '').strip().lower()
    return username or 'id{}'.format(item.get('id', ''))


def greeting_name(item):
    """Как назвать человека в начале сообщения. Пусто — лучше, чем латиница.

    Первым берём имя, которое подобрала модель: она приводит его к
    кириллице и в именительный падеж, «Ekaterina Москвичева» -> «Екатерина».
    Если разбор не делали, пробуем вытащить имя из заголовка чата.
    """
    name = (item.get('name') or '').strip()
    if name:
        return name
    title = TITLE_NOISE.sub(' ', item.get('title') or '')
    words = [w.strip(names.TRIM) for w in title.replace(',', ' ').split()]
    words = [w for w in words if w]
    # «Deli Bag», «Brainics Support» — это не люди, обращаться там не к кому
    if any(w.lower() in names.NOT_PEOPLE for w in words[:4]):
        return ''
    words = [w for w in words if w.lower() not in names.HONORIFICS]
    if not words:
        return ''

    # Порядок слов бывает любым: «Ковригина Юлия» — это Юлия. Поэтому
    # сначала ищем знакомое имя, и только потом берём первое слово.
    cyrillic = [w for w in words[:3] if CYRILLIC_NAME.match(w)]
    for word in cyrillic:
        if names.is_first_name(word):
            return word.capitalize()

    latin = [names.to_cyrillic(w) for w in words[:3]]
    for word in latin:
        if word and names.is_first_name(word):
            return word

    if cyrillic and cyrillic[0] == words[0]:
        return words[0]
    # «Maxim Kolmogorov» — это Максим, а не повод остаться без имени
    return latin[0] if latin and latin[0] else ''


async def contacts_for(client, items, segment):
    """Из размеченных чатов — адресаты рассылки. Возвращает (список, сколько не вышло).

    Peer берём из локального кэша сессии: эти чаты уже есть в диалогах, и
    обращаться к Telegram за ними не нужно — а именно такие обращения и
    ограничивают жёстче всего.
    """
    from .contacts import Contact

    source = 'сегмент · {}'.format(segment)
    result = []
    missed = 0
    for item in items:
        try:
            peer = await client.get_input_entity(int(item['id']))
        except (ValueError, TypeError, OverflowError):
            missed += 1
            continue
        username = item.get('username') or ''
        result.append(Contact(
            username=username,
            # только кириллическое имя: «Привет, Sergey Ч[KTS]!» в шаблоне
            # выглядит хуже, чем «Привет!» без имени
            name=greeting_name(item),
            row_num=0,
            source=source,
            peer=peer,
            key=username.lower() or 'id{}'.format(item['id']),
            kind=item.get('kind', PERSON),
        ))
    return result, missed


def tg_link(item):
    """Ссылка, которую понимает настольный Telegram."""
    username = (item.get('username') or '').strip()
    if username:
        return 'tg://resolve?domain={}'.format(username)

    raw = str(item.get('id', '')).strip()
    if not raw:
        return ''
    if item.get('kind') == PERSON:
        return 'tg://openmessage?user_id={}'.format(raw)
    # у супергрупп id вида -100xxxxxxxxx, Telegram ждёт без префикса
    digits = raw.lstrip('-')
    if digits.startswith('100'):
        digits = digits[3:]
    return 'tg://openmessage?chat_id={}'.format(digits)
