# -*- coding: utf-8 -*-
"""Какие начала писем получаются — на реальных контактах и переписках.

Нужен, чтобы читать формулировки глазами, а не догадываться по коду. Ничего
не отправляет и в Telegram не ходит: берёт хранилище контактов и переписки,
уже сохранённые набором для проверки промпта.

    python tools/preview_openings.py              # по 4 примера на каждый случай
    python tools/preview_openings.py --case мостик --limit 20
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config as config_module                # noqa: E402
from app import names, softeners, tgcontacts          # noqa: E402
from app.paths import DATA_DIR                        # noqa: E402

DATASET = DATA_DIR / 'eval' / 'dataset.json'
# Здесь переписки целиком, а не обрезок: без них не проверить «давно не
# общались» — оно смотрит, сколько реплик было с каждой стороны.
THREADS = DATA_DIR / 'eval' / 'reactions.json'


def transcripts():
    """id чата -> выжимка переписки, как её видит рассылка."""
    found = {}
    if THREADS.exists():
        data = json.load(open(THREADS, encoding='utf-8'))
        for thread in data.get('threads', []):
            rows = [(m.get('mine'), None, (m.get('text') or '').strip())
                    for m in thread.get('messages', [])]
            rows.reverse()          # в файле старые сверху, в истории — новые
            found[str(thread.get('chat_id'))] = tgcontacts.as_transcript(rows)
    if DATASET.exists():
        for row in json.load(open(DATASET, encoding='utf-8')):
            found.setdefault(str(row.get('id')), row.get('transcript') or '')
    return found


def template():
    path = DATA_DIR / 'template.txt'
    if not path.exists():
        path = Path(__file__).resolve().parent.parent / 'template.txt'
    return path.read_text(encoding='utf-8')


def case_of(note):
    """Короткое имя случая — по пометке, которую вернул сборщик."""
    if 'напомнил' in note:
        return 'мостик'
    if 'вспомнил' in note:
        return 'память'
    if 'пропущенное' in note:
        return 'извинение'
    if 'повторно' in note:
        return 'повтор'
    return note.split(',')[0].strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', default='')
    parser.add_argument('--limit', type=int, default=4)
    args = parser.parse_args()

    base = template()
    store = tgcontacts.load_store()
    talk = tgcontacts.load_event_talk()
    texts = transcripts()
    cfg = config_module.load()
    own = [p.strip() for p in (cfg.get('openrouter_company') or '').split(',')
           if p.strip()]

    groups = defaultdict(list)
    for chat_id, transcript in texts.items():
        item = store.get(chat_id) or {}
        if item.get('kind') != tgcontacts.PERSON:
            continue
        # работаем только по двум сегментам
        if item.get('type') not in softeners.WORK_SEGMENTS:
            continue
        name = names.trusted((item.get('name') or '').strip(),
                             item.get('title') or '') \
            or tgcontacts.greeting_name(item)
        text, note = softeners.build(
            base.replace('{NAME}', name or 'друг'),
            name=name,
            address=item.get('address') or 'вы',
            kind=item.get('type') or '',
            transcript=transcript,
            talk=talk.get(chat_id) or (),
            last_seen=item.get('date') or '',
            recall=item.get('recall') or '',
            sender=cfg.get('sender_gender') or 'м',
            intro=cfg.get('sender_intro') or '',
            sender_name=cfg.get('sender_name') or '',
            title=item.get('title') or '')
        opening = text.split('\n\n')[0]
        groups[case_of(note)].append((item.get('title') or '', note, opening))

    order = sorted(groups, key=lambda k: -len(groups[k]))
    for case in order:
        if args.case and case != args.case:
            continue
        items = groups[case]
        print('\n=== {} — {} шт.'.format(case, len(items)))
        for title, note, opening in items[:args.limit]:
            print('\n  {} · {}'.format(title[:40], note))
            print('  {}'.format(opening))


if __name__ == '__main__':
    main()
