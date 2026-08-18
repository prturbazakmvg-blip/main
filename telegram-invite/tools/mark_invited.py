# -*- coding: utf-8 -*-
"""Отметить тех, кого уже звали на эту конференцию в этом сезоне.

Программа и так не пишет повторно: перед каждым человеком она читает
переписку и, увидев там своё же приглашение свежее полугода, пропускает
его. Но пока отметки нет, такой человек считается «оставшимся» — счётчик
врёт, и он занимает место в партии.

Кого звали руками, а не через программу, — как раз такой случай.

    python tools/mark_invited.py --show      # посмотреть, кого нашли
    python tools/mark_invited.py             # проставить отметки
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config as config_module                    # noqa: E402
from app import openrouter, progress, softeners, tgcontacts  # noqa: E402
from app.paths import DATA_DIR                             # noqa: E402

THREADS = DATA_DIR / 'eval' / 'reactions.json'


def active_text(cfg):
    items = config_module.messages(cfg)
    if not items:
        return ''
    return items[config_module.active_index(cfg, len(items))]['text']


def invited_since(keys, since):
    """chat_id -> дата последнего нашего приглашения, не раньше since."""
    if not THREADS.exists():
        raise SystemExit('Нет собранных переписок: сначала '
                         'python tools/collect_reactions.py collect')
    data = json.loads(THREADS.read_text(encoding='utf-8'))
    found = {}
    for thread in data.get('threads', []):
        if thread.get('kind') != tgcontacts.PERSON:
            continue
        for message in thread.get('messages', []):
            if not message.get('mine'):
                continue
            when = (message.get('date') or '')[:10]
            if when < since:
                continue
            if not openrouter.mentions_invite(message.get('text') or '', keys):
                continue
            chat = str(thread.get('chat_id'))
            if when > found.get(chat, ''):
                found[chat] = when
    return found


def collect(since):
    cfg = config_module.load()
    keys = openrouter.invite_keys(active_text(cfg))
    if not keys:
        raise SystemExit('В тексте приглашения нет ничего опознаваемого — '
                         'ни названия, ни ссылки.')
    store = tgcontacts.load_store()
    rows = []
    for chat, when in invited_since(keys, since).items():
        item = store.get(chat)
        if not item or item.get('type') not in softeners.WORK_SEGMENTS:
            continue
        source = 'сегмент · {}'.format(item['type'])
        key = (item.get('username') or '').lower() or 'id{}'.format(chat)
        already = key in progress.load_done(source)
        rows.append((when, item.get('title') or chat, key, source, already))
    rows.sort()
    return keys, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since', default='',
                        help='с какой даты считать сезон, YYYY-MM-DD')
    parser.add_argument('--show', action='store_true')
    args = parser.parse_args()

    since = args.since or '2026-06-01'
    keys, rows = collect(since)
    fresh = [r for r in rows if not r[4]]
    print('Приметы приглашения: {}'.format(', '.join(sorted(keys))))
    print('Званых с {}: {} · из них без отметки: {}'.format(
        since, len(rows), len(fresh)))
    for when, title, key, source, _ in fresh:
        print('   {} {:32} {}'.format(when, title[:32], source))
    if args.show or not fresh:
        return
    for _when, _title, key, source, _ in fresh:
        progress.mark_done(source, key)
    print('\nОтмечено «уже писали»: {}. Снять — кнопкой «Забыть «уже писали»» '
          'в окне программы.'.format(len(fresh)))


if __name__ == '__main__':
    main()
