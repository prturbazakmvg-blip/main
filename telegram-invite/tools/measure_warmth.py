# -*- coding: utf-8 -*-
"""Плотность, частота и неформальность переписки — для порядка рассылки.

То же, что кнопка «Измерить теплоту» в окне, но из терминала и на копии
сессии: можно запускать, не закрывая программу.

    python tools/measure_warmth.py            # по двум рабочим сегментам
    python tools/measure_warmth.py --show     # что получилось
"""
import argparse
import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config as config_module                # noqa: E402
from app import priority, session as session_module    # noqa: E402
from app import softeners, tgcontacts                  # noqa: E402
from app.paths import DATA_DIR, SESSION_PATH           # noqa: E402

SESSION_COPY = DATA_DIR / 'eval' / 'warmth'


def _client(cfg):
    source = SESSION_PATH.with_suffix('.session')
    if not source.exists():
        raise SystemExit('Нет входа в Telegram: сначала войдите в приложении.')
    SESSION_COPY.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, str(SESSION_COPY) + '.session')
    original = session_module.SESSION_PATH
    session_module.SESSION_PATH = SESSION_COPY
    try:
        return session_module.build_client(cfg)
    finally:
        session_module.SESSION_PATH = original


async def run(args):
    store = tgcontacts.load_store()
    todo = [v for v in store.values()
            if isinstance(v, dict) and v.get('kind') == tgcontacts.PERSON
            and v.get('type') in softeners.WORK_SEGMENTS and not v.get('warm')]
    todo.sort(key=lambda v: v.get('date') or '', reverse=True)
    todo = todo[:args.limit]
    if not todo:
        print('Всё уже измерено.')
        return
    print('Читаю переписку у {} человек...'.format(len(todo)))

    client = _client(config_module.load())
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit('Вход недействителен — войдите в приложении заново.')

    guard = tgcontacts.FloodGuard(
        on_wait=lambda s: print('  жду {} с по просьбе Telegram'.format(s)))
    limit = asyncio.Semaphore(3)
    done = {'ok': 0, 'fail': 0}

    async def handle(item):
        if guard.stopped:
            return
        async with limit:
            rows = await tgcontacts.history(client, item['id'], limit=30,
                                            guard=guard)
            if rows is None:
                done['fail'] += 1
                return
            item['warm'] = priority.stats(rows)
            done['ok'] += 1
            if done['ok'] % 100 == 0:
                print('  измерено {} из {}'.format(done['ok'], len(todo)))
                tgcontacts.save_store(store)

    await asyncio.gather(*(handle(item) for item in todo))
    tgcontacts.save_store(store)
    print('Готово: {} измерено, {} не прочиталось.'.format(done['ok'],
                                                           done['fail']))
    if guard.stopped:
        print(guard.stopped)
    await client.disconnect()


def show():
    store = tgcontacts.load_store()
    rows = [v for v in store.values()
            if isinstance(v, dict) and v.get('type') in softeners.WORK_SEGMENTS]
    rows.sort(key=lambda v: -priority.score(v))
    print('Измерено {} из {}'.format(sum(1 for v in rows if v.get('warm')),
                                     len(rows)))
    for item in rows[:40]:
        warm = item.get('warm') or {}
        print('  {:3} {}  {:28} {}  сообщ. {}'.format(
            priority.score(item), priority.label(priority.score(item)),
            (item.get('title') or '')[:28], item.get('date', ''),
            warm.get('msgs', '—')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=5000)
    parser.add_argument('--show', action='store_true')
    args = parser.parse_args()
    show() if args.show else asyncio.run(run(args))


if __name__ == '__main__':
    main()
