# -*- coding: utf-8 -*-
"""Разбор ответов на прошлые приглашения: какие бывают реакции и сколько их.

Считаем не на глаз: модель раскладывает каждый ответ по видам, а мы смотрим
на итог. От этого зависит, какие оговорки вообще имеет смысл писать.

    python tools/analyze_reactions.py            # разложить и показать итог
    python tools/analyze_reactions.py --kind далеко   # что попало в один вид
"""
import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config as config_module            # noqa: E402
from app import openrouter                         # noqa: E402
from app.paths import DATA_DIR                     # noqa: E402

REPLIES = DATA_DIR / 'eval' / 'replies.json'
LABELLED = DATA_DIR / 'eval' / 'replies_labelled.json'

KINDS = (
    'придёт',        # согласился, взял билет, «буду»
    'подумает',      # интересно, но пока не решил
    'потом',         # ответит позже, сейчас занят
    'занят',         # именно в эти даты не может: отпуск, своё мероприятие
    'далеко',        # другой город или страна, не ездит
    'передаст',      # сам не пойдёт, но передаст коллеге
    'спикер',        # хочет выступить или предлагает тему
    'спросил',       # просит программу, ссылку, условия
    'был',           # вспоминает прошлую конференцию
    'отказ',         # прямое «нет»
    'мимо',          # ответ не про конференцию
)

SYSTEM = """Ты разбираешь ответы людей на приглашение на конференцию.

Верни JSON: {"вид": "<один из списка>", "цитата": "<до 10 слов из ответа>"}

Виды:
- придёт — согласился, собирается, купил билет
- подумает — интересно, но решения нет
- потом — обещал ответить позже, сейчас не до того
- занят — не может именно в эти даты: отпуск, своё мероприятие, дела
- далеко — другой город или страна, не ездит на выездные
- передаст — сам не пойдёт, но покажет коллегам
- спикер — хочет выступить, предлагает тему доклада
- спросил — просит программу, ссылку, условия участия
- был — вспоминает прошлую конференцию, хвалит или ругает
- отказ — прямое «нет» без причины
- мимо — ответ не про конференцию вообще

Только JSON, без пояснений."""


def classify(row, key, model):
    try:
        answer = openrouter.ask(key, model, SYSTEM, row['ответ'][:600],
                                max_tokens=120)
        data = openrouter.parse_json(answer) or {}
    except Exception as e:
        return dict(row, вид='ошибка', почему='{}: {}'.format(type(e).__name__, e))
    kind = str(data.get('вид', '')).strip().lower()
    return dict(row, вид=kind if kind in KINDS else 'мимо',
                цитата=str(data.get('цитата', ''))[:120])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', default='')
    parser.add_argument('--model', default='google/gemini-2.5-flash-lite')
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()

    if LABELLED.exists() and not args.refresh:
        rows = json.load(open(LABELLED, encoding='utf-8'))
    else:
        cfg = config_module.load()
        key = (cfg.get('openrouter_key') or '').strip()
        if not key:
            raise SystemExit('Нет ключа OpenRouter в настройках.')
        source = json.load(open(REPLIES, encoding='utf-8'))
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(lambda r: classify(r, key, args.model), source))
        json.dump(rows, open(LABELLED, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)

    if args.kind:
        for row in rows:
            if row.get('вид') == args.kind:
                print('-', row['ответ'][:200])
        return

    counts = Counter(row.get('вид', '?') for row in rows)
    total = sum(counts.values())
    print('Ответов разобрано: {}\n'.format(total))
    for kind, count in counts.most_common():
        print('  {:10} {:3}  {:.0f}%'.format(kind, count, 100.0 * count / total))


if __name__ == '__main__':
    main()
