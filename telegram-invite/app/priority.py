# -*- coding: utf-8 -*-
"""Кого звать первым.

Список на полторы тысячи человек за раз не обойти: черновики уходят
партиями, и за десять дней до конференции важно, чтобы в первую партию
попали те, кто ещё может переиграть свои планы.

Данные за 13–17 августа показали, где это решается. Тем, с кем год
молчали, ответили 0 из 12. Тем, кого звали в прошлый раз, — 17 из 29.
Разница не в формулировке письма, а в том, кому оно пришло.

Порядок считается по четырём величинам:

    свежесть        когда было последнее сообщение
    плотность       сколько сообщений в месяц, пока общались
    частота         сколько сообщений всего — и только взаимных
    неформальность  «ты», смайлики, короткие реплики

Первая берётся из списка диалогов, остальные три — из переписки. Читать
переписку ради этого специально не нужно: её и так читает разбор типов.
"""
import re
from datetime import date

# Веса. Свежесть весит больше всего не из принципа: холодная когорта в
# замерах не ответила вообще, а тёплая ответила в половине случаев.
WEIGHTS = {'свежесть': 0.30, 'плотность': 0.25, 'частота': 0.20,
           'неформальность': 0.25}

# Свежесть: ступеньками, а не гладко. Разница между «месяц назад» и «три»
# невелика, между «год» и «три года» — тоже; ломается всё на полугоде.
RECENCY = ((3, 1.0), (6, 0.8), (12, 0.5), (24, 0.25), (36, 0.1))
RECENCY_TAIL = 0.03

# Плотность: восемь сообщений в месяц — это уже рабочий контакт.
DENSE_ENOUGH = 8.0
# Частота: полсотни взаимных сообщений — потолок, дальше не различаем.
MANY_MESSAGES = 50.0

SHORT_MESSAGE = 60          # короткие реплики — признак живого разговора
EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-➿]')
FORMAL_MARKS = ('добрый день', 'здравствуйте', 'с уважением', 'доброе утро',
                'добрый вечер', 'коллеги,')
INFORMAL_MARKS = ('привет', 'ага', 'ок', 'слушай', 'давай', 'спс', 'норм',
                  'ща', 'сорри', 'угу')


def months_since(last_seen, today=None):
    """Сколько месяцев прошло. None — если даты нет."""
    try:
        year, month, _rest = str(last_seen).split('-', 2)
        year, month = int(year), int(month)
    except (ValueError, AttributeError):
        return None
    today = today or date.today()
    return max(0, (today.year - year) * 12 + (today.month - month))


def freshness(last_seen, today=None):
    months = months_since(last_seen, today)
    if months is None:
        return 0.0
    for limit, value in RECENCY:
        if months <= limit:
            return value
    return RECENCY_TAIL


def stats(rows):
    """Плотность, частота и неформальность — из прочитанной переписки.

    rows — то, что отдаёт tgcontacts.history: [(моё?, дата, текст)].
    Возвращает компактную запись, которая ложится в хранилище.
    """
    mine = theirs = 0
    dates = []
    informal_hits = shortish = emoji = formal = 0
    counted = 0
    for out, when, text in rows or ():
        text = (text or '').strip()
        if not text:
            continue
        if out:
            mine += 1
        else:
            theirs += 1
        if when is not None:
            dates.append(when)
        counted += 1
        lowered = text.lower()
        if len(text) <= SHORT_MESSAGE:
            shortish += 1
        if EMOJI.search(text):
            emoji += 1
        if any(mark in lowered for mark in FORMAL_MARKS):
            formal += 1
        if any(mark in lowered.split() for mark in INFORMAL_MARKS):
            informal_hits += 1

    span = 0
    if len(dates) >= 2:
        span = abs((max(dates) - min(dates)).days)
    return {
        'msgs': mine + theirs,
        'mine': mine,
        'theirs': theirs,
        'span': span,
        # доли считаем сразу: хранить сырые счётчики незачем
        'short': round(shortish / counted, 2) if counted else 0.0,
        'emoji': round(emoji / counted, 2) if counted else 0.0,
        'formal': round(formal / counted, 2) if counted else 0.0,
        'loose': round(informal_hits / counted, 2) if counted else 0.0,
    }


def _clamp(value):
    return max(0.0, min(1.0, value))


def density(warm):
    """Сколько сообщений в месяц, пока общались."""
    msgs = warm.get('msgs') or 0
    if not msgs:
        return 0.0
    span = warm.get('span') or 0
    # переписка в один день — это не «много в месяц», а один заход
    months = max(1.0, span / 30.0)
    return _clamp((msgs / months) / DENSE_ENOUGH)


def frequency(warm):
    """Сколько сообщений всего — но только взаимных.

    Двадцать наших сообщений в пустоту частотой общения не являются.
    """
    both = 2 * min(warm.get('mine') or 0, warm.get('theirs') or 0)
    if both <= 0:
        return 0.0
    import math
    return _clamp(math.log1p(both) / math.log1p(MANY_MESSAGES))


def informality(warm, address=''):
    """Насколько разговор был неформальным.

    «Ты» — половина ответа: эта форма не ставится случайно. Остальное —
    приметы живой переписки: короткие реплики, смайлики, «ага» и «слушай»
    против «добрый день» и «с уважением».
    """
    points = 0.5 if address == 'ты' else 0.0
    points += 0.2 * _clamp(warm.get('short') or 0.0)
    points += 0.1 * _clamp((warm.get('emoji') or 0.0) * 3)
    points += 0.2 * _clamp((warm.get('loose') or 0.0) * 2)
    points -= 0.2 * _clamp((warm.get('formal') or 0.0) * 2)
    return _clamp(points)


def parts(known, today=None):
    """Все четыре доли по отдельности — их же показываем в подсказке."""
    known = known or {}
    warm = known.get('warm') or {}
    return {
        'свежесть': freshness(known.get('date') or '', today),
        'плотность': density(warm),
        'частота': frequency(warm),
        'неформальность': informality(warm, known.get('address') or ''),
    }


def score(known, today=None):
    """0..100 — насколько этот человек первый в очереди."""
    values = parts(known, today)
    total = sum(WEIGHTS[name] * value for name, value in values.items())
    return int(round(100 * total))


def label(points):
    """Короткая пометка для таблицы — цифры глазами не читаются."""
    if points >= 60:
        return '●●●'
    if points >= 40:
        return '●●○'
    if points >= 20:
        return '●○○'
    return '○○○'


def explain(known, today=None):
    """Из чего сложился балл — для подсказки над строкой таблицы."""
    values = parts(known, today)
    warm = (known or {}).get('warm') or {}
    lines = ['{}: {:.0%}'.format(name, values[name])
             for name in ('свежесть', 'плотность', 'частота', 'неформальность')]
    if warm.get('msgs'):
        lines.append('сообщений {} (от него {}), общались {} дн.'.format(
            warm['msgs'], warm.get('theirs', 0), warm.get('span', 0)))
    else:
        lines.append('переписка не измерена — считаю только по дате')
    return '\n'.join(lines)
