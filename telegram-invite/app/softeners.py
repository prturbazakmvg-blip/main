# -*- coding: utf-8 -*-
"""Личное начало письма — по правилам, без модели.

Раньше вступление сочиняла нейросеть, и каждый раз получалось по-разному:
то придумает факт, то поздравит второй раз, то напишет «рад, что мы на
связи». Оказалось, что вариантов всего три, и выбираются они по переписке:

    обычный          «Имя, привет!»
    пропустил        «Имя, привет! Совсем пропустил твоё сообщение…»
    зову повторно    «Имя, привет! Вижу, я к тебе опять только с конфой…»

Тело письма не трогается вообще: меняется только форма обращения.
"""
import re

from .openrouter import already_invited

# Регистр общения. Считается из двух полей, которые уже есть у каждого
# контакта: формы обращения («ты»/«вы») и типа. Модель для этого не нужна —
# «вы» само по себе означает дистанцию, а «ты» у близких и коллег звучит
# иначе, чем «ты» у подрядчика, с которым просто давно на короткой ноге.
CLOSE_KINDS = ('близкие', 'коллеги')

GREETINGS = {
    'свои': '{}, привет!',
    'рабочий': '{}, привет!',
    'формальный': '{}, добрый день!',
}
NO_NAME = {'свои': 'Привет!', 'рабочий': 'Привет!',
           'формальный': 'Добрый день!'}


def register(address='вы', kind=''):
    """«свои» / «рабочий» / «формальный» — по обращению и типу контакта."""
    if address != 'ты':
        return 'формальный'
    return 'свои' if kind in CLOSE_KINDS else 'рабочий'


# Ключ -> {регистр: фраза}. Фразы дословные: их писал человек. Разница между
# регистрами — не в вежливости, а в дистанции: смайлик и «сорри» уместны с
# теми, с кем и так болтаем, и звучат развязно в письме заказчику.
SOFTENERS = {
    'missed': {
        'свои': 'Совсем пропустил твоё сообщение, только сейчас увидел — извини 🙈',
        'рабочий': 'Пропустил твоё сообщение, увидел только сейчас — извини.',
        'формальный': 'Пропустил ваше сообщение, увидел только сейчас — извините.',
    },
    'again': {
        'свои': 'Вижу, я к тебе опять только с конфой, сорри 🙂 '
                'но вдруг в этот раз актуально.',
        'рабочий': 'Вижу, я к тебе опять только с конфой 🙂 '
                   'но вдруг в этот раз актуально.',
        'формальный': 'Пишу вам снова про конференцию — вдруг в этот раз '
                      'будет актуально.',
    },
}

# Пары «ты» / «вы» для тела письма. Порядок важен: длинное раньше короткого,
# иначе «тебе» разберётся по кускам.
FORMS = (
    ('соберёшься', 'соберётесь'), ('соберешься', 'соберетесь'),
    ('сможешь', 'сможете'), ('захочешь', 'захотите'),
    ('приходи', 'приходите'), ('пиши', 'пишите'), ('напиши', 'напишите'),
    ('дай знать', 'дайте знать'), ('расскажи', 'расскажите'),
    ('тебе', 'вам'), ('тебя', 'вас'), ('тобой', 'вами'),
    ('твоего', 'вашего'), ('твоему', 'вашему'), ('твоей', 'вашей'),
    ('твой', 'ваш'), ('твоя', 'ваша'), ('твои', 'ваши'), ('твоё', 'ваше'),
    ('твое', 'ваше'), ('ты', 'вы'),
)


# Последнее слово за собеседником — ещё не повод извиняться. «Спасибо» и
# «ок» ответа не ждут, и «совсем пропустил твоё сообщение» в ответ на них
# выглядит нелепо: человек ничего не спрашивал.
ACKS = ('спасибо', 'спс', 'благодарю', 'ок', 'окей', 'ага', 'угу', 'понял',
        'поняла', 'принял', 'принято', 'хорошо', 'договорились', 'до связи',
        'супер', 'отлично', 'класс', 'ясно', 'плюс', 'да', 'нет', 'ок)')

# Приметы того, что ответа ждали
ASKS = ('?', 'можешь', 'можете', 'подскажи', 'подскажите', 'скинь', 'скиньте',
        'пришли', 'пришлите', 'напиши', 'напишите', 'жду', 'вопрос',
        'что думаешь', 'что скажешь', 'как насчёт', 'как насчет',
        'дай знать', 'дайте знать', 'уточни', 'посмотри', 'посмотрите',
        'интересно узнать', 'расскажи', 'расскажите')

# Длинное сообщение без вопроса ответа всё-таки ждёт: человек потратил время
LONG_ENOUGH = 200


def needs_reply(text):
    """Ждало ли это сообщение ответа."""
    cleaned = ' '.join((text or '').split())
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if any(mark in lowered for mark in ASKS):
        return True
    bare = lowered.strip('!.,)( 🙂🙏👍😀😊❤️')
    if bare in ACKS or len(cleaned) < 25:
        return False
    return len(cleaned) >= LONG_ENOUGH


def choose(transcript, base_text=''):
    """Какой софтенер уместен. Пусто — значит просто «Имя, привет!».

    Решаем кодом: у модели на этот вопрос устойчиво не получается — она
    извиняется перед всеми подряд.
    """
    lines = [line for line in (transcript or '').splitlines() if line.strip()]
    if already_invited(base_text, transcript):
        # уже звали, а он не ответил — извиняемся за повтор
        return 'again'
    if lines and lines[-1].startswith('он:'):
        # последнее слово за собеседником — но извиняемся, только если он
        # о чём-то просил или спрашивал
        if needs_reply(re.sub(r'^он:\s*', '', lines[-1])):
            return 'missed'
    return ''


# После долгого молчания холодный анонс читается как спам: человек не помнит,
# кто пишет и почему именно ему. Поэтому сначала мостик — когда и о чём
# говорили в прошлый раз.
SILENT_MONTHS = 6

# Больше года молчания — повод сказать об этом вслух. Но только если было
# что прерывать: после двух реплик «мы давно не общались» звучит как попытка
# набить знакомство, которого не было.
LONG_SILENCE_MONTHS = 12
REAL_TALK_LINES = 6


def had_real_talk(transcript):
    """Был ли разговор, а не пара реплик.

    Считаем по обеим сторонам: «спасибо» в ответ на нашу рассылку — это не
    общение, и вспоминать его не стоит.
    """
    lines = [line for line in (transcript or '').splitlines() if line.strip()]
    theirs = [line for line in lines if line.startswith('он:')]
    mine = [line for line in lines if line.startswith('я:')]
    return len(theirs) >= 2 and len(mine) >= 2 and len(lines) >= REAL_TALK_LINES


# Фраза владельца из живого приглашения: «Мы давно не общались и может у
# тебя миллион раз все изменилось».
# Так это пишет сам владелец: «Давно не общались, может тебе полезно
# будет», «давно не общались, тебе может интересно будет по теме ИИ )».
# Второй половиной всегда идёт польза, а не вежливость: «у тебя наверняка
# всё поменялось» — это наша выдумка, в его письмах такого нет.
LONG_AGO = {
    'ты': 'Давно не общались, может тебе полезно будет)',
    'вы': 'Давно не общались, возможно, вам будет полезно.',
}
LONG_AGO_ABOUT = {
    'ты': 'Давно не общались — в прошлый раз обсуждали {}. '
          'Подумал, вдруг будет интересно)',
    'вы': 'Давно не общались — в прошлый раз обсуждали {}. '
          'Подумал, что вам может быть интересно.',
}


def long_ago(address='вы', recall=''):
    """«Давно не общались…» — с темой прошлого разговора, если она есть."""
    recall = (recall or '').strip()
    if recall:
        return LONG_AGO_ABOUT[address].format(recall)
    return LONG_AGO[address]

# Формулировки взяты из живых сообщений владельца: он и сам, когда пишет
# малознакомому, начинает с «Меня Иван зовут, CEO Alto» и напоминает, где
# пересекались. Это и снимает ощущение рассылки.
BRIDGES = {
    'обычный': 'Мы общались {when}{about}.',
    'звали': 'Мы общались {when}{about} — тогда я тоже звал на нашу '
             'конференцию. В этом году она в новом формате.',
}
# Владелец представляется одной и той же формулой — шесть раз почти
# дословно: «Меня Иван зовут, я организатор AI Growth Day и гендиректор
# Alto». Важно «организатор»: это и есть право писать незнакомому.
INTRO = 'Меня {} зовут, я {}.'
INTRO_NO_NAME = 'Напомню, я {}.'


def introduce(intro, sender_name=''):
    """«Меня Иван зовут, я организатор AI Growth Day и гендиректор Alto.»"""
    what = (intro or '').strip().rstrip('.')
    if not what:
        return ''
    name = (sender_name or '').strip()
    if name:
        return INTRO.format(name, what)
    return INTRO_NO_NAME.format(what)

MONTHS = ('январе', 'феврале', 'марте', 'апреле', 'мае', 'июне', 'июле',
          'августе', 'сентябре', 'октябре', 'ноябре', 'декабре')


def _parts(last_seen):
    try:
        year, month, _rest = str(last_seen).split('-', 2)
        return int(year), int(month)
    except (ValueError, AttributeError):
        return None, None


def silent_months(last_seen, today=None):
    """Сколько месяцев молчим. None — если дата неизвестна."""
    from datetime import date
    year, month = _parts(last_seen)
    if year is None:
        return None
    today = today or date.today()
    return max(0, (today.year - year) * 12 + (today.month - month))


def when_phrase(last_seen, today=None):
    """«в марте», «в октябре прошлого года», «в 2023 году»."""
    from datetime import date
    year, month = _parts(last_seen)
    if year is None or not 1 <= month <= 12:
        return ''
    today = today or date.today()
    if year == today.year:
        return 'в {}'.format(MONTHS[month - 1])
    if year == today.year - 1:
        return 'в {} прошлого года'.format(MONTHS[month - 1])
    return 'в {} году'.format(year)


def bridge(last_seen, recall='', invited=False, today=None, intro='',
           sender_name=''):
    """Напоминание о прошлом разговоре или '' — если напоминать нечем.

    intro — как представиться («Иван, CEO Alto»). Малознакомому человеку
    после года молчания важнее всего понять, кто пишет.
    """
    when = when_phrase(last_seen, today)
    if not when:
        return ''
    about = ' про {}'.format(recall.strip()) if (recall or '').strip() else ''
    text = BRIDGES['звали' if invited else 'обычный'].format(when=when,
                                                             about=about)
    opening = introduce(intro, sender_name)
    if opening:
        text = '{} {}'.format(opening, text)
    return text


# --------------------------------------------------------------------------
# «Может тебе будет полезно» — рабочий заход

# Сегменты, с которыми работаем. Остальным (подрядчики, соискатели, спам)
# заход не подбирается: конференция не про них.
WORK_SEGMENTS = ('ит и digital', 'клиент')

# Разбор 140 приглашений владельца: компанию собеседника он не назвал ни
# разу, должность — трижды. Зато «может тебе будет полезно» встречается
# постоянно и без объяснений, почему полезно. Человек решает сам.
USEFUL = {
    'свои': ('Тебе может быть полезно)',
        'Может тебе интересно будет)',
        'Подумал, вдруг будет интересно)'),
    'рабочий': ('Тебе может быть полезно)',
        'Может тебе интересно будет)',
        'Может тебе актуально будет)'),
    'формальный': ('Возможно, вам будет полезно.',
        'Подумал, что вам может быть интересно.',
        'Возможно, вам это будет актуально.'),
}

# Вопрос в конце захода — в 15 письмах из 140. Он и работает на ответ:
# на вопрос отвечают, на анонс нет. Ставим только тем, с кем правда
# общались, иначе вопрос о делах незнакомцу выглядит фальшиво.
#
# «Как сам» — оборот не для всех: он уместен, только если так в этой
# переписке уже говорили. В остальных случаях «как дела» — нейтрально и
# не притворяется большей близостью, чем есть.
# «А у тебя как дела?» — это ответ на вопрос о наших делах. Если про нас
# никто не спрашивал, «а» повисает: правильно просто «Как у тебя дела?».
# То же с «и» в «И ты как сам?».
SAM = {'м': 'ты как сам?', 'ж': 'ты как сама?', '': ''}
HOW_ARE_YOU = 'Как у тебя дела?'
HOW_ARE_YOU_BACK = 'А у тебя как дела?'
SAM_MARKS = ('как сам', 'как сама', 'как ты сам', 'ты как сам')
ABOUT_US_MARKS = ('как дела', 'как сам', 'как ты', 'как жизнь', 'как оно')
NO_QUESTION = ('формальный',)


def said_sam(transcript):
    """Говорили ли в этой переписке «как сам»."""
    lowered = (transcript or '').lower()
    return any(mark in lowered for mark in SAM_MARKS)


def asked_about_us(transcript):
    """Спрашивал ли собеседник, как дела у нас.

    От этого зависит «а» и «и» в вопросе: они уместны только в ответ.
    """
    for line in (transcript or '').splitlines():
        if not line.startswith('он:'):
            continue
        lowered = line.lower()
        if any(mark in lowered for mark in ABOUT_US_MARKS):
            return True
    return False


def small_talk(tone, name='', transcript=''):
    """Вопрос собеседнику — в его роде и в тех словах, которые уже звучали."""
    if tone in NO_QUESTION:
        return ''
    back = asked_about_us(transcript)
    if said_sam(transcript):
        from . import names as names_module
        sam = SAM.get(names_module.gender(name)) or ''
        if sam:
            return ('И {}'.format(sam) if back else sam.capitalize())
    return HOW_ARE_YOU_BACK if back else HOW_ARE_YOU


def useful(tone, seed=''):
    """Короткое обещание пользы. Вариант постоянный для одного человека.

    Разные формулировки нужны, чтобы полторы тысячи писем не были
    побуквенно одинаковыми, но у конкретного человека при повторном
    прогоне текст не должен меняться.
    """
    options = USEFUL.get(tone) or USEFUL['формальный']
    return options[sum(ord(ch) for ch in str(seed)) % len(options)]


# --------------------------------------------------------------------------
# «В прошлый раз звал спикером»

# Приметы того, что человека звали не просто прийти, а выступить. Ищем
# только в своих сообщениях: «хочу выступить» от него — это другое, его
# ловит reactions.
SPEAKER_MARKS = ('выступить', 'выступишь', 'подавай доклад', 'доклад подать',
                 'подай доклад', 'велком с докладом', 'велком спикером',
                 'буду рад докладу', 'в спикеры', 'спикером', 'с докладом')


def invited_as_speaker(transcript):
    """Звали ли мы этого человека выступить."""
    for line in (transcript or '').splitlines():
        if not line.startswith('я:'):
            continue
        lowered = line.lower()
        if any(mark in lowered for mark in SPEAKER_MARKS):
            return True
    return False


# Условие обязательно: «хочешь выступить?» просто так владелец не пишет
# никогда — всегда «если у вас с ИИ что-то уже получилось».
SPEAKER_AGAIN = {
    'свои': 'В прошлый раз звал тебя спикером — если с ИИ у вас что-то '
            'внедрили, велком с докладом)',
    'рабочий': 'В прошлый раз звал тебя спикером — если с ИИ у вас что-то '
               'внедрили, велком с докладом.',
    'формальный': 'В прошлый раз звал вас спикером — если с ИИ у вас что-то '
                  'внедрили, будем рады докладу.',
}


# Слова о себе, у которых есть род. Заменяются только в том, что дописываем
# мы: тело письма пишет человек, и туда лезть нельзя.
SENDER_FORMS = (
    ('пропустил', 'пропустила'), ('звал', 'звала'), ('рад', 'рада'),
    ('сам', 'сама'), ('готов', 'готова'), ('увидел', 'увидела'),
    ('подумал', 'подумала'), ('написал', 'написала'), ('забыл', 'забыла'),
)


def to_sender(text, sender='м'):
    """Приводит фразы о себе к роду отправителя."""
    if sender != 'ж':
        return text
    return _swap(text, dict(SENDER_FORMS))


def _swap(text, pairs):
    """Меняет формы обращения, сохраняя заглавные буквы."""
    def replace(match):
        word = match.group(0)
        target = pairs[word.lower()]
        return target.capitalize() if word[0].isupper() else target

    keys = sorted(pairs, key=len, reverse=True)
    pattern = re.compile(r'\b(' + '|'.join(re.escape(k) for k in keys) + r')\b',
                         re.IGNORECASE)
    return pattern.sub(replace, text)


def to_address(text, address):
    """Приводит тело письма к «ты» или к «вы»."""
    if address == 'ты':
        return _swap(text, {formal: informal for informal, formal in FORMS})
    return _swap(text, {informal: formal for informal, formal in FORMS})


def build(base_text, name='', address='вы', transcript='', talk=(), kind='',
          last_seen='', recall='', today=None, sender='м', intro='',
          company='', role='', own=(), title='', username='',
          sender_name=''):
    """Готовое сообщение. Возвращает (текст, что сделали).

    Тело идёт дословно — меняется только форма обращения. Это главное
    отличие от прежней схемы: факты, даты и промокод потерять нельзя.

    talk — что человек говорил про конференцию вне личной переписки
    (в группах). Личная переписка важнее: она адресована нам.

    kind — тип контакта с вкладки «Контакты». Вместе с обращением задаёт
    регистр: с близкими здороваемся не так, как с заказчиком.

    last_seen — дата последнего сообщения, recall — о чём тогда говорили.
    Нужны формальным контактам, с которыми давно не общались: без
    напоминания анонс читается как рассылка.

    sender — род того, кто пишет: «пропустил» или «пропустила». Берётся из
    профиля Telegram при входе; программой пользуются не только мужчины.

    company и role — должность и компания из разбора переписки. Из них
    складывается «почему зову именно тебя»; own — свои же компании, чтобы
    не звать на свою конференцию собственного партнёра.
    """
    from . import names, reactions
    from .openrouter import strip_greeting

    body = to_address(strip_greeting(base_text).strip(), address)
    tone = register(address, kind)

    # Прошлая реакция важнее дежурного «зову повторно»: она конкретна и
    # показывает, что разговор помнят.
    from .openrouter import invite_keys
    reaction = (reactions.from_transcript(transcript, invite_keys(base_text))
                or reactions.from_talk(talk))
    memory = reactions.note(reaction, address, names.gender(name))

    # Молчание вспоминаем только там, где было что прерывать: после пары
    # реплик и «мы общались в марте про...», и «давно не общались» — это
    # придуманное знакомство.
    # Молчание вспоминаем только там, где было что прерывать: после пары
    # реплик и «мы общались в марте про...», и «давно не общались» — это
    # придуманное знакомство.
    silence = silent_months(last_seen, today)
    talked = had_real_talk(transcript)
    remind = ''
    bare_intro = False
    if not memory and silence is not None and talked:
        if silence >= LONG_SILENCE_MONTHS:
            remind = long_ago(address, recall)
            if tone == 'формальный':
                opening = introduce(intro, sender_name)
                remind = '{} {}'.format(opening, remind) if opening else remind
        elif tone == 'формальный' and silence >= SILENT_MONTHS:
            remind = bridge(last_seen, recall,
                            invited=already_invited(base_text, transcript),
                            today=today, intro=intro, sender_name=sender_name)
    elif not memory and tone == 'формальный' and (intro or '').strip() \
            and silence is not None and silence >= LONG_SILENCE_MONTHS:
        # переписки толком не было — общего прошлого не вспоминаем, но
        # представиться после года молчания всё равно нужно
        remind = introduce(intro, sender_name)
        bare_intro = True

    # Звали выступить — зовём снова, но с условием: «хочешь выступить?»
    # просто так владелец не пишет никогда. И только по двум сегментам:
    # спикеров он ищет среди клиентов и заметных ит-компаний.
    speaker = ''
    if not memory and kind in WORK_SEGMENTS and invited_as_speaker(transcript):
        speaker = SPEAKER_AGAIN[tone]

    key = '' if (memory or remind or speaker) else choose(transcript, base_text)

    # Рабочий заход владельца — короткое обещание пользы без объяснений.
    # Ставим последним: если уже сказано что-то конкретное, оно сильнее.
    useful_line = ''
    if kind in WORK_SEGMENTS and not (memory or speaker or key) \
            and (bare_intro or not remind):
        useful_line = useful(tone, seed=name or base_text[:20])

    greeting = GREETINGS[tone].format(name) if name else NO_NAME[tone]
    for extra in (memory, remind, speaker, useful_line):
        if extra:
            greeting = '{} {}'.format(greeting, extra)
    if key:
        greeting = '{} {}'.format(greeting, SOFTENERS[key][tone])
    # Вопрос собеседнику — в 15 письмах из 140, и он прямо работает на
    # ответ. Но только тем, с кем правда общались: незнакомцу «и ты как
    # сам?» звучит фальшиво.
    greeting = to_sender(greeting, sender)
    # Вопрос приписываем уже после правки рода отправителя: «сам» в нём —
    # про собеседника, и род у него свой. Иначе у женщины-отправителя
    # «И ты как сам?» превращалось бы в «сама» независимо от адресата.
    question = small_talk(tone, name, transcript) if talked else ''
    if question and '?' not in greeting:
        greeting = '{} {}'.format(greeting, question)

    notes = {'missed': 'извинился за пропущенное сообщение',
             'again': 'извинился, что зову повторно'}.get(key, '')
    if memory:
        notes = 'вспомнил прошлый ответ: {}'.format(reaction)
    if remind:
        notes = 'напомнил о прошлом разговоре ({} мес. молчания)'.format(silence)
    if speaker:
        notes = 'звал спикером — зову снова'
    if useful_line:
        notes = '{}; «может быть полезно»'.format(notes) if notes \
            else '«может быть полезно»'
    notes = '{}, {}'.format(tone, notes) if notes else tone
    if not name:
        notes += '; имени нет'
    return '{}\n\n{}'.format(greeting, body), notes
