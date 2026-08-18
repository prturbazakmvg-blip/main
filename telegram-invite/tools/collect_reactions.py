# -*- coding: utf-8 -*-
"""Что отвечали на прошлые приглашения на конференцию.

Глобальный поиск Telegram находит все упоминания разом — и в личке, и в
группах. Обходить три тысячи диалогов ради этого не нужно: поиск стоит
несколько запросов на слово.

    python tools/collect_reactions.py collect      # собрать упоминания
    python tools/collect_reactions.py show         # что собралось
    python tools/collect_reactions.py show --chat 12345

Файл с перепиской в репозиторий не кладётся: это личные сообщения. Он живёт
в ~/Library/Application Support/TelegramInvite/eval/reactions.json.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon.errors import FloodWaitError                 # noqa: E402

from app import config as config_module                    # noqa: E402
from app import session as session_module                  # noqa: E402
from app import tgcontacts                                 # noqa: E402
from app.paths import DATA_DIR, SESSION_PATH               # noqa: E402

OUT_DIR = DATA_DIR / 'eval'
REACTIONS = OUT_DIR / 'reactions.json'
SESSION_COPY = OUT_DIR / 'telegram'

# Слова, по которым Telegram найдёт разговор именно про эту конференцию.
# Общие («конференция», «митап») дают чужие мероприятия и здесь бесполезны.
WORDS = ('agday', 'agdays', 'ai growth day', 'ai growth days', 'аgd',
         'growth days', 'agday.ru')

# Сколько сообщений после найденного читаем, чтобы увидеть ответ
AFTER = 6


def _client(cfg):
    import shutil
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    source = SESSION_PATH.with_suffix('.session')
    if not source.exists():
        raise SystemExit('Нет входа в Telegram: сначала войдите в приложении.')
    shutil.copy2(source, str(SESSION_COPY) + '.session')
    original = session_module.SESSION_PATH
    session_module.SESSION_PATH = SESSION_COPY
    try:
        return session_module.build_client(cfg)
    finally:
        session_module.SESSION_PATH = original


def load():
    if not REACTIONS.exists():
        return {'hits': [], 'threads': []}
    with open(REACTIONS, encoding='utf-8') as f:
        return json.load(f)


def save(data):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(REACTIONS, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _chat_kind(entity):
    from telethon.tl.types import Channel, Chat, User
    if isinstance(entity, User):
        return tgcontacts.PERSON
    if isinstance(entity, Chat):
        return tgcontacts.GROUP
    if isinstance(entity, Channel):
        return tgcontacts.CHANNEL if entity.broadcast else tgcontacts.GROUP
    return ''


def _chat_title(entity):
    name = getattr(entity, 'title', None)
    if name:
        return name
    parts = [getattr(entity, 'first_name', '') or '',
             getattr(entity, 'last_name', '') or '']
    return ' '.join(p for p in parts if p).strip() or str(getattr(entity, 'id', ''))


async def _search(client, word, guard):
    """Глобальный поиск по всем чатам. Пустой ответ — это нормально."""
    found = []
    try:
        async for message in client.iter_messages(None, search=word, limit=400):
            if not (message.text or '').strip():
                continue
            found.append(message)
    except FloodWaitError as e:
        print('  Telegram просит подождать {} с — пропускаю «{}»'
              .format(e.seconds, word))
    except Exception as e:
        print('  «{}»: {}: {}'.format(word, type(e).__name__, e))
    return found


async def collect(cfg):
    client = _client(cfg)
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit('Вход недействителен — войдите в приложении заново.')

    me = await client.get_me()
    guard = tgcontacts.FloodGuard(
        on_wait=lambda s: print('  жду {} с по просьбе Telegram'.format(s)))

    seen = set()
    hits = []
    for word in WORDS:
        print('Ищу «{}»...'.format(word))
        for message in await _search(client, word, guard):
            key = (message.chat_id, message.id)
            if key in seen:
                continue
            seen.add(key)
            hits.append(message)
        print('  всего найдено сообщений: {}'.format(len(hits)))

    # группируем по чату: интересен не сам факт упоминания, а что было дальше
    by_chat = {}
    for message in hits:
        by_chat.setdefault(message.chat_id, []).append(message)

    print('\nЧатов с упоминаниями: {}'.format(len(by_chat)))
    threads = []
    for number, (chat_id, messages) in enumerate(sorted(by_chat.items()), 1):
        messages.sort(key=lambda m: m.id)
        try:
            entity = await messages[0].get_chat()
        except Exception:
            entity = None
        title = _chat_title(entity) if entity is not None else str(chat_id)
        kind = _chat_kind(entity) if entity is not None else ''
        username = getattr(entity, 'username', '') or ''

        rows = []
        for message in messages:
            # ответ обычно идёт следом за приглашением
            try:
                tail = await client.get_messages(chat_id, limit=AFTER,
                                                 min_id=message.id)
            except Exception:
                tail = []
            block = [message] + sorted(tail, key=lambda m: m.id)
            for item in block:
                text = (item.text or '').strip()
                if not text:
                    continue
                sender = getattr(item, 'sender_id', None)
                rows.append({
                    'id': item.id,
                    'date': item.date.isoformat() if item.date else '',
                    'mine': sender == me.id,
                    'sender': sender,
                    'text': text[:1200],
                })

        # дубликаты: соседние упоминания дают пересекающиеся хвосты
        unique = {}
        for row in rows:
            unique[row['id']] = row
        rows = [unique[k] for k in sorted(unique)]

        threads.append({'chat_id': chat_id, 'title': title, 'kind': kind,
                        'username': username, 'messages': rows})
        print('  [{}/{}] {} — {} сообщ.'.format(number, len(by_chat), title,
                                                len(rows)))

    save({'me': me.id, 'threads': threads})
    print('\nСохранил в {}'.format(REACTIONS))
    await client.disconnect()


def show(args):
    data = load()
    threads = data.get('threads', [])
    if args.chat:
        threads = [t for t in threads if str(t['chat_id']) == str(args.chat)]
    print('Чатов: {}'.format(len(threads)))
    for thread in threads:
        print('\n=== {} [{}] {}'.format(thread['title'], thread['kind'],
                                        thread['chat_id']))
        for row in thread['messages']:
            who = 'я ' if row['mine'] else 'он'
            print('  {} {} {}'.format(who, row['date'][:10],
                                      row['text'].replace('\n', ' ⏎ ')[:220]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('collect')
    show_parser = sub.add_parser('show')
    show_parser.add_argument('--chat', default='')
    args = parser.parse_args()

    if args.command == 'collect':
        asyncio.run(collect(config_module.load()))
    else:
        show(args)


if __name__ == '__main__':
    main()
