# -*- coding: utf-8 -*-
"""Чем заканчивался прошлый разговор — короткой строкой в хранилище.

Нужно тем, с кем давно не общались и общались формально: холодный анонс
такому человеку читается как спам. Одна фраза «мы общались в октябре
прошлого года про интеграцию с 1С» снимает это.

Единственное место, где без модели не обойтись: пересказать переписку
правилами нельзя. Зато считается один раз, заранее, и результат видно в
пробном прогоне — на рассылке модель уже не участвует.

    python tools/fill_recall.py --limit 100     # заполнить, начиная с самых давних
    python tools/fill_recall.py --show          # что уже записано
"""
import argparse
import asyncio
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config as config_module                # noqa: E402
from app import openrouter, softeners, tgcontacts      # noqa: E402
from app import session as session_module              # noqa: E402
from app.paths import DATA_DIR, SESSION_PATH           # noqa: E402

SESSION_COPY = DATA_DIR / 'eval' / 'telegram'

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


def needs_recall(item, today=None):
    """Кому нужен мостик: формальный регистр и долгое молчание."""
    # Пустая строка тоже ответ: у модели про этот чат сказать нечего, и
    # спрашивать заново незачем — важно наличие поля, а не его содержимое.
    if item.get('kind') != tgcontacts.PERSON or 'recall' in item:
        return False
    if item.get('type') in ('спам', 'близкие', ''):
        return False
    tone = softeners.register(item.get('address') or 'вы', item.get('type') or '')
    if tone != 'формальный':
        return False
    months = softeners.silent_months(item.get('date') or '', today)
    return months is not None and months >= softeners.SILENT_MONTHS


def ask_one(item, transcript, key, model):
    """Тот же вопрос, что задаёт приложение по ходу рассылки."""
    return openrouter.recall_topic(key, model, transcript,
                                   item.get('about', ''))


# Промпт и отсев пустых формулировок живут в app/openrouter.py: тем же
# вопросом пользуется приложение по ходу рассылки, и расходиться им нельзя.
is_empty_phrase = openrouter.empty_recall


async def run(args):
    cfg = config_module.load()
    key = config_module.openrouter_key(cfg)
    if not key:
        raise SystemExit('Нет ключа OpenRouter в настройках.')

    store = tgcontacts.load_store()
    todo = [v for v in store.values() if isinstance(v, dict) and needs_recall(v)]
    todo.sort(key=lambda v: v.get('date') or '')
    todo = todo[:args.limit]
    if not todo:
        print('Все, кому нужен мостик, уже заполнены.')
        return
    print('Заполняю «о чём говорили» для {} человек...'.format(len(todo)))

    client = _client(cfg)
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit('Вход недействителен — войдите в приложении заново.')

    guard = tgcontacts.FloodGuard(
        on_wait=lambda s: print('  жду {} с по просьбе Telegram'.format(s)))
    pool = ThreadPoolExecutor(max_workers=4)
    loop = asyncio.get_running_loop()

    filled = 0
    for number, item in enumerate(todo, 1):
        text = await tgcontacts.transcript(client, item['id'], guard=guard)
        if text is None:
            continue
        recall = await loop.run_in_executor(
            pool, ask_one, item, text, key,
            args.model or config_module.openrouter_model(cfg))
        item['recall'] = recall
        if recall:
            filled += 1
            print('  [{}/{}] {} — {}'.format(number, len(todo),
                                             (item.get('title') or '')[:30], recall))
        if number % 10 == 0:
            tgcontacts.save_store(store)

    tgcontacts.save_store(store)
    print('\nГотово: заполнено {} из {}.'.format(filled, len(todo)))
    await client.disconnect()


def show():
    store = tgcontacts.load_store()
    rows = [v for v in store.values()
            if isinstance(v, dict) and (v.get('recall') or '').strip()]
    print('Записано напоминаний: {}'.format(len(rows)))
    for item in sorted(rows, key=lambda v: v.get('date') or '')[:60]:
        print('  {} · {:28} про {}'.format(
            (item.get('date') or '')[:7], (item.get('title') or '')[:26],
            item['recall']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--model', default='')
    parser.add_argument('--show', action='store_true')
    args = parser.parse_args()
    if args.show:
        show()
    else:
        asyncio.run(run(args))


if __name__ == '__main__':
    main()
