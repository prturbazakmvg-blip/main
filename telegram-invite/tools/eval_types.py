# -*- coding: utf-8 -*-
"""Проверка промпта на ваших же правках.

Каждый раз, когда вы меняете тип контакта руками, получается образец
«правильного ответа». Этот скрипт собирает такие образцы в набор и гоняет по
нему модель, чтобы видеть, где промпт ошибается.

    python tools/eval_types.py collect     # выгрузить переписки размеченных чатов
    python tools/eval_types.py run         # прогнать модель по набору
    python tools/eval_types.py run --model qwen/qwen3.7-flash
    python tools/eval_types.py show        # что лежит в наборе

Набор с перепиской НЕ лежит в репозитории: это личные сообщения. Он пишется
в ~/Library/Application Support/TelegramInvite/eval/dataset.json.
"""
import argparse
import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config as config_module            # noqa: E402
from app import openrouter, tgcontacts             # noqa: E402
from app import session as session_module          # noqa: E402
from app.paths import DATA_DIR, SESSION_PATH       # noqa: E402

EVAL_DIR = DATA_DIR / 'eval'
DATASET = EVAL_DIR / 'dataset.json'
# Своя копия входа: приложение держит свой файл сессии открытым, и два
# подключения к одному sqlite мешают друг другу. Копию Telegram принимает —
# ключ авторизации тот же.
SESSION_COPY = EVAL_DIR / 'telegram'


def _client(cfg):
    import shutil
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    source = SESSION_PATH.with_suffix('.session')
    if not source.exists():
        raise SystemExit('Нет входа в Telegram: сначала войдите в приложении.')
    shutil.copy2(source, str(SESSION_COPY) + '.session')
    session_module.SESSION_PATH = SESSION_COPY
    original = session_module.SESSION_PATH
    try:
        return session_module.build_client(cfg)
    finally:
        session_module.SESSION_PATH = original


def load_dataset():
    if not DATASET.exists():
        return []
    with open(DATASET, encoding='utf-8') as f:
        return json.load(f)


def save_dataset(rows):
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATASET, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)


def labelled(store):
    """Чаты, у которых тип поставлен человеком, — эталон.

    Плюс те, что модель разметила, а человек не тронул: молчаливое согласие
    тоже сигнал, но слабее, поэтому помечаем источник.
    """
    rows = []
    for item in store.values():
        if not isinstance(item, dict) or not item.get('type'):
            continue
        by_hand = (item.get('why') or '') == 'выставлено вручную' or item.get('by') == 'человек'
        rows.append((item, 'человек' if by_hand else 'модель'))
    return rows


async def collect(only_manual=False, refresh=False):
    cfg = config_module.load()
    store = tgcontacts.load_store()
    rows = labelled(store)
    if only_manual:
        rows = [r for r in rows if r[1] == 'человек']
    if not rows:
        print('В хранилище нет размеченных чатов. Разметьте типы в приложении.')
        return

    client = _client(cfg)
    await client.connect()
    if not await client.is_user_authorized():
        print('Нет входа в Telegram — запустите приложение и войдите.')
        return

    known = {row['id']: row for row in load_dataset()}
    for item, source in rows:
        saved = known.get(item['id'])
        if saved and saved.get('transcript') and not refresh:
            # переписку уже качали — обновляем только эталон.
            # also (равноценные ответы) проставлено руками, его не трогаем.
            saved['expected'] = item['type']
            saved['source'] = source
            saved['model_said'] = item.get('model_type', '')
            continue
        text = await tgcontacts.transcript(client, item['id'])
        about = item.get('about') or await tgcontacts.about(
            client, item['id'], item.get('kind'))
        known[item['id']] = {
            'id': item['id'],
            'title': item.get('title', ''),
            'kind': item.get('kind', ''),
            'about': about,
            'transcript': text,
            'expected': item['type'],
            'source': source,
            # что отвечала модель до правки, если приложение это запомнило
            'model_said': item.get('model_type', ''),
        }
        print('  {:<8} {:<34} эталон: {:<12} ({})'.format(
            item.get('kind', ''), (item.get('title') or '')[:34], item['type'], source))

    save_dataset(sorted(known.values(), key=lambda r: (r['kind'], r['title'])))
    await client.disconnect()
    print('\nСохранено образцов: {} -> {}'.format(len(known), DATASET))


def accepted(row):
    """Верные ответы для образца.

    Бывает, что тип честно двоякий: бывший коллега, который теперь работает
    по подряду, — и «коллеги», и «подрядчик» правда. Считать это ошибкой
    модели нечестно, поэтому у образца может быть несколько верных ответов.
    """
    return {row['expected']} | set(row.get('also') or ())


def set_also(title, kinds):
    """Отметить равноценные ответы у образца."""
    rows = load_dataset()
    found = [r for r in rows if title.lower() in (r['title'] or '').lower()]
    if len(found) != 1:
        print('Нашлось образцов: {} — уточните название'.format(len(found)))
        for r in found[:10]:
            print('   ', r['title'])
        return 1
    found[0]['also'] = list(kinds)
    save_dataset(rows)
    print('{}: верно также {}'.format(found[0]['title'], ', '.join(kinds)))
    return 0


def ask_one(row, key, model, about, company='', examples=()):
    """Один образец через боевые функции — тестируем то, что работает в приложении."""
    text = row['transcript']
    if row['kind'] == tgcontacts.PERSON:
        # сам себя в примерах видеть не должен — иначе это списывание
        others = [(t, k) for t, k in examples if t != row['title']]
        result = openrouter.classify_person(key, model, row['title'], text,
                                            about=about, company=company,
                                            examples=others, bio=row.get('about', ''))
        return result['type'], result['why']
    if row.get('about'):
        text = 'Описание чата: {}\n\n{}'.format(row['about'], text)
    result = openrouter.classify_group(key, model, row['title'], text, about=about)
    return result['fit'], result['why']


def run(model=None, only=None, workers=6, repeat=1):
    rows = load_dataset()
    if only:
        rows = [r for r in rows if r['kind'] == only]
    # чаты без единого слова приложение теперь не разбирает — и проверять
    # на них нечего: по имени тип не угадать
    empty = [r for r in rows if openrouter.nothing_to_read(r['transcript'], r.get('about'))]
    rows = [r for r in rows if r not in empty]
    if not rows:
        print('Набор пуст. Сначала: python tools/eval_types.py collect')
        return 1

    cfg = config_module.load()
    key = cfg.get('openrouter_key', '')
    if not key:
        print('Не задан ключ OpenRouter в настройках приложения.')
        return 1
    model = model or cfg.get('openrouter_model') or openrouter.DEFAULT_MODEL
    about = cfg.get('openrouter_about', '')
    company = cfg.get('openrouter_company', '')

    print('модель: {}\nкомпания: {}\nобразцов: {} x {} прогон(а)'.format(
        model, company or '(не задана)', len(rows), repeat))
    if empty:
        print('не в счёт (переписки нет): {}'.format(len(empty)))

    # Один и тот же вопрос модель отвечает не всегда одинаково даже при
    # temperature=0, поэтому меряем не удачный прогон, а устойчивость.
    examples = [(r['title'], r['expected']) for r in load_dataset()
                if r['source'] == 'человек' and r['kind'] == tgcontacts.PERSON]
    print('примеров от вас в промпте: до {}\n'.format(min(12, len(examples))))

    jobs = [(row, attempt) for row in rows for attempt in range(repeat)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        got = list(pool.map(
            lambda job: ask_one(job[0], key, model, about, company, examples), jobs))

    results = {}
    for (row, _attempt), (answer, why) in zip(jobs, got):
        results.setdefault(row['id'], []).append((answer, why))

    stable_hits = 0
    hits = 0
    confusion = {}
    for row in rows:
        answers = results[row['id']]
        ok_set = accepted(row)
        good = sum(1 for a, _w in answers if a in ok_set)
        stable_hits += good == repeat
        hits += good
        for answer, _why in answers:
            if answer not in ok_set:
                pair = (row['expected'], answer)
                confusion[pair] = confusion.get(pair, 0) + 1
        mark = '✓' if good == repeat else ('~' if good else '✗')
        variants = ', '.join(sorted({a for a, _w in answers}))
        wanted = row['expected'] + (' / ' + ' / '.join(row['also'])
                                    if row.get('also') else '')
        print('{} {:<34} ждали {:<24} ответ {:<24} {}'.format(
            mark, (row['title'] or '')[:34], wanted[:24], variants[:24],
            answers[0][1][:34]))

    total = len(rows)
    by_hand = [r for r in rows if r['source'] == 'человек']
    hand_ok = sum(1 for r in by_hand
                  if all(a in accepted(r) for a, _w in results[r['id']]))
    print('\nвсегда верно: {}/{} ({:.0f}%)   ответов верных: {}/{}'.format(
        stable_hits, total, 100.0 * stable_hits / total, hits, total * repeat))
    if by_hand:
        print('из них правленных руками: {}/{} ({:.0f}%)'.format(
            hand_ok, len(by_hand), 100.0 * hand_ok / len(by_hand)))
    if confusion:
        print('\nпутает:')
        for (want, answer), n in sorted(confusion.items(), key=lambda kv: -kv[1]):
            print('  {} -> {}: {}'.format(want, answer, n))
    return 0 if stable_hits == total else 2


def show():
    rows = load_dataset()
    print('образцов: {}'.format(len(rows)))
    for row in rows:
        print('  {:<8} {:<34} {:<24} ({}) переписки {} симв.'.format(
            row['kind'], (row['title'] or '')[:34],
            ' / '.join(sorted(accepted(row)))[:24],
            row['source'], len(row['transcript'])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('collect', 'run', 'show', 'also'))
    parser.add_argument('rest', nargs='*',
                        help='also: «часть имени» тип [тип ...]')
    parser.add_argument('--model', default=None)
    parser.add_argument('--only', default=None, choices=('человек', 'группа', 'канал'))
    parser.add_argument('--repeat', type=int, default=1,
                        help='run: сколько раз спросить каждый образец')
    parser.add_argument('--refresh', action='store_true',
                        help='collect: перечитать переписки заново')
    parser.add_argument('--manual', action='store_true',
                        help='collect: только правленные руками')
    args = parser.parse_args()

    if args.command == 'collect':
        asyncio.run(collect(only_manual=args.manual, refresh=args.refresh))
        return 0
    if args.command == 'show':
        show()
        return 0
    if args.command == 'also':
        if len(args.rest) < 2:
            print('Пример: python tools/eval_types.py also Сюртуков коллеги')
            return 1
        return set_also(args.rest[0], args.rest[1:])
    return run(model=args.model, only=args.only, repeat=args.repeat)


if __name__ == '__main__':
    sys.exit(main())
