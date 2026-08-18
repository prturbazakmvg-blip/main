# -*- coding: utf-8 -*-
"""Разбор пачки контактов из командной строки.

То же самое, что кнопка «Определить типы (ИИ)» в приложении, но с явным
количеством и без окна — удобно, когда надо разметить сотню и посмотреть,
где модель врёт.

    python tools/classify_batch.py --limit 100
    python tools/classify_batch.py --limit 50 --kind группа

Берутся самые свежие по дате чаты без типа: они и нужнее всего для рассылки.
Ручные правки не трогаются никогда.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config as config_module            # noqa: E402
from app import openrouter, tgcontacts             # noqa: E402
from tools.eval_types import _client               # noqa: E402


def manual_examples(store, kind=tgcontacts.PERSON):
    return [(item.get('title', ''), item['type']) for item in store.values()
            if item.get('kind') == kind and item.get('type')
            and item.get('by') == 'человек'][-12:]


async def main(limit, kind, workers, redo='', fields=False):
    cfg = config_module.load()
    key = cfg.get('openrouter_key', '')
    if not key:
        raise SystemExit('Не задан ключ OpenRouter.')
    model = cfg.get('openrouter_model') or openrouter.DEFAULT_MODEL
    about = cfg.get('openrouter_about', '')
    company = cfg.get('openrouter_company', '')

    store = tgcontacts.load_store()
    examples = manual_examples(store, kind)
    if fields:
        # тип не трогаем: добираем только компанию и должность у размеченных
        pick = (lambda item: item.get('type')
                and not (item.get('company') and item.get('role')
                         and (item.get('name') or item['type'] == 'другое')))
    elif redo:
        # пересмотреть уже разобранное — например, когда появился новый тип.
        # Ручные правки не трогаем: они и есть эталон.
        pick = (lambda item: item.get('type') == redo and item.get('by') != 'человек')
    else:
        pick = (lambda item: not item.get('type'))
    todo = sorted((item for item in store.values()
                   if item.get('kind') == kind and pick(item)),
                  key=lambda item: item.get('date', ''), reverse=True)[:limit]
    if not todo:
        print('Нечего разбирать: у всех чатов этого вида тип уже стоит.')
        return

    print('модель: {}\nпримеров от вас: {}\nразбираю: {}{}\n'.format(
        model, len(examples), len(todo),
        ' (только компания и должность)' if fields else
        (' (пересмотр «{}»)'.format(redo) if redo else '')))

    client = _client(cfg)
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit('Нет входа в Telegram.')

    loop = asyncio.get_event_loop()
    gate = asyncio.Semaphore(workers)
    done = {'ok': 0, 'fail': 0, 'empty': 0}

    async def handle(item):
        async with gate:
            text = await tgcontacts.transcript(client, item['id'])
            if not item.get('about'):
                item['about'] = await tgcontacts.about(
                    client, item['id'], item.get('kind'))
            if openrouter.nothing_to_read(text, item['about']):
                done['empty'] += 1
                return
            try:
                if kind == tgcontacts.PERSON:
                    result = await loop.run_in_executor(
                        None, lambda: openrouter.classify_person(
                            key, model, item.get('title', ''), text,
                            about=about, company=company, examples=examples,
                            bio=item['about']))
                    item['company'] = result['company']
                    item['role'] = result['role']
                    if fields and not item.get('name') and item['type'] != 'другое':
                        item['name'] = result['name']
                    if not fields:
                        item['type'] = result['type']
                        item['address'] = result['address']
                        item['name'] = (result['name']
                                        if result['type'] != 'другое' else '')
                else:
                    if item['about']:
                        text = 'Описание чата: {}\n\n{}'.format(item['about'], text)
                    result = await loop.run_in_executor(
                        None, lambda: openrouter.classify_group(
                            key, model, item.get('title', ''), text, about=about))
                    item['type'] = result['fit']
                    item['topic'] = result['topic']
                    item['tone'] = result['tone']
            except Exception as e:
                done['fail'] += 1
                print('  {:<34} ошибка: {}'.format((item.get('title') or '')[:34], e))
                return
            if not fields:
                item['why'] = result['why']
            store[item['id']] = item
            done['ok'] += 1
            print('{:<34} {:<14} {:<20} {}'.format(
                (item.get('title') or '')[:34], item['type'],
                item.get('company', '')[:20], item.get('role', '')))

    for start in range(0, len(todo), 40):
        await asyncio.gather(*(handle(item) for item in todo[start:start + 40]))
        tgcontacts.save_store(store)

    tgcontacts.save_store(store)
    await client.disconnect()

    counts = {}
    for item in todo:
        if item.get('type'):
            counts[item['type']] = counts.get(item['type'], 0) + 1
    print('\nразобрано {}, не вышло {}, пропущено пустых {}'.format(
        done['ok'], done['fail'], done['empty']))
    print('по типам: {}'.format(', '.join('{} {}'.format(k, v)
                                          for k, v in sorted(counts.items()))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--kind', default=tgcontacts.PERSON,
                        choices=(tgcontacts.PERSON, tgcontacts.GROUP))
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--fields', action='store_true',
                        help='только компания и должность, тип не трогать')
    parser.add_argument('--redo', default='',
                        help='пересмотреть уже поставленный тип, например «другое»')
    args = parser.parse_args()
    asyncio.run(main(args.limit, args.kind, args.workers, args.redo, args.fields))
