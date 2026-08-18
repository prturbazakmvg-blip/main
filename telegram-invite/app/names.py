# -*- coding: utf-8 -*-
"""Имя для обращения: латиница -> кириллица.

В Telegram половина контактов подписана латиницей — «Maxim Kolmogorov».
Писать «Привет, Maxim!» в русском тексте некрасиво, а выбрасывать имя жалко:
это Максим. Перевод делается кодом, а не моделью, — так он одинаков всегда.
"""
import re

LATIN_WORD = re.compile(r'^[A-Za-z\-]{2,20}$')

# Пунктуация, которую снимаем с краёв слова
TRIM = '.,!?:;«»"\''

# Имена, которые по общим правилам получаются криво
EXCEPTIONS = {
    'yuri': 'Юрий', 'yury': 'Юрий', 'yuriy': 'Юрий', 'iurii': 'Юрий',
    'nataly': 'Наталья', 'natalia': 'Наталья', 'natalya': 'Наталья',
    'natasha': 'Наташа', 'maria': 'Мария', 'mariya': 'Мария',
    'julia': 'Юлия', 'yulia': 'Юлия', 'juliya': 'Юлия',
    'evgeny': 'Евгений', 'evgeniy': 'Евгений', 'eugene': 'Евгений',
    'george': 'Георгий', 'georgy': 'Георгий',
    'alex': 'Александр', 'alexander': 'Александр', 'aleksandr': 'Александр',
    'sasha': 'Саша', 'alexandra': 'Александра',
    'michael': 'Михаил', 'mike': 'Михаил', 'misha': 'Миша',
    'john': 'Джон', 'james': 'Джеймс', 'jane': 'Джейн',
    'andrew': 'Андрей', 'andrey': 'Андрей', 'andrei': 'Андрей',
    'nikolay': 'Николай', 'nikolai': 'Николай', 'kolya': 'Коля',
    'dmitry': 'Дмитрий', 'dmitri': 'Дмитрий', 'dima': 'Дима',
    'anatoly': 'Анатолий', 'valery': 'Валерий', 'vitaly': 'Виталий',
    'arkady': 'Аркадий', 'gennady': 'Геннадий', 'vasily': 'Василий',
    'grigory': 'Григорий', 'sergy': 'Сергей', 'serge': 'Сергей',
    'peter': 'Пётр', 'petr': 'Пётр', 'pyotr': 'Пётр',
    'elena': 'Елена', 'helen': 'Елена', 'lena': 'Лена',
    'ksenia': 'Ксения', 'kseniya': 'Ксения', 'oxana': 'Оксана',
    'tatyana': 'Татьяна', 'tatiana': 'Татьяна', 'tanya': 'Таня',
    'daria': 'Дарья', 'darya': 'Дарья', 'sofia': 'София',
    'anastasia': 'Анастасия', 'anastasiya': 'Анастасия', 'nastya': 'Настя',
    'olga': 'Ольга', 'liubov': 'Любовь', 'lyubov': 'Любовь',
    'ilya': 'Илья', 'iliya': 'Илья', 'ilia': 'Илья',
    'artemy': 'Артемий', 'arseny': 'Арсений', 'timofey': 'Тимофей',
    'kate': 'Катя', 'katya': 'Катя', 'katerina': 'Катерина',
    'artem': 'Артём', 'artyom': 'Артём', 'semyon': 'Семён',
    'fedor': 'Фёдор', 'fyodor': 'Фёдор', 'alena': 'Алёна',
    'aleksey': 'Алексей', 'alexei': 'Алексей', 'liza': 'Лиза',
    # разобрано по живым контактам: тут общие правила дают чепуху
    'viacheslav': 'Вячеслав', 'vyacheslav': 'Вячеслав',
    'eduard': 'Эдуард', 'edward': 'Эдуард', 'edwin': 'Эдвин',
    'elvira': 'Эльвира', 'ella': 'Элла', 'ellie': 'Элли', 'ellina': 'Эллина',
    'emil': 'Эмиль', 'eldar': 'Эльдар', 'edgar': 'Эдгар',
    'chris': 'Крис', 'christina': 'Кристина', 'christopher': 'Кристофер',
    'anthony': 'Энтони', 'arthur': 'Артур',
    'jenya': 'Женя', 'zhenya': 'Женя', 'juli': 'Юля', 'jul': 'Юля',
    'jacob': 'Джейкоб', 'jack': 'Джек', 'jim': 'Джим', 'joe': 'Джо',
    'alice': 'Алиса', 'alisa': 'Алиса', 'nastia': 'Настя', 'katia': 'Катя',
    'olesia': 'Олеся', 'olesya': 'Олеся', 'ksenia': 'Ксения',
    'kseniia': 'Ксения', 'uliana': 'Ульяна', 'ulyana': 'Ульяна',
    'aleksander': 'Александр', 'alexandr': 'Александр', 'sander': 'Александр',
    'natalie': 'Наталья', 'sophie': 'Софи', 'marie': 'Мари', 'irene': 'Ирина',
    'valerie': 'Валерия', 'eugenia': 'Евгения', 'eugeny': 'Евгений',
    'cyril': 'Кирилл', 'kirill': 'Кирилл', 'kiryl': 'Кирилл',
    'ilyas': 'Ильяс', 'ilias': 'Ильяс', 'albert': 'Альберт',
    'alvina': 'Альвина', 'ainur': 'Айнур', 'aynur': 'Айнур',
    'galymzhan': 'Галымжан', 'neil': 'Нил', 'mike': 'Майк',
    'anzhelika': 'Анжелика', 'olya': 'Оля', 'sonya': 'Соня',
    'nadya': 'Надя', 'nadia': 'Надя', 'vika': 'Вика', 'anya': 'Аня',
    'zhenia': 'Женя', 'lera': 'Лера', 'tolya': 'Толя',
}

# Окончания разбираем до общих правил: «Aleksei» это Алексей, а не Алексеи,
# «Anastasiia» — Анастасия. Внутри слова так менять нельзя: «Diana» должна
# остаться Дианой.
ENDINGS = (
    ('iia', 'ия'), ('iya', 'ия'), ('iia', 'ия'), ('yia', 'ия'),
    ('ia', 'ия'), ('ya', 'я'),
    ('ii', 'ий'), ('iy', 'ий'), ('yi', 'ий'),
    ('ei', 'ей'), ('ey', 'ей'), ('ay', 'ай'), ('ai', 'ай'), ('oi', 'ой'),
)

# Слова-приметы того, что это не человек, а компания или чат
NOT_PEOPLE = frozenset('''
support manager office agency studio development service services solution
solutions entertainment club store bag lounge house group media digital tech
team shop bot admin assistant assistance central sales marketing partner
project conf events event school academy consulting company corp
guest lounge cloud labs lab dev devs online expo forum awards summit
'''.split())

# Обращения: их пропускаем, а имя берём из следующего слова — «Dr Nadya»
HONORIFICS = frozenset(('dr', 'mr', 'mrs', 'ms', 'prof', 'sir'))

# Порядок важен: сначала длинные сочетания
DIGRAPHS = (
    ('shch', 'щ'), ('sch', 'щ'), ('zh', 'ж'), ('kh', 'х'), ('ch', 'ч'),
    ('sh', 'ш'), ('ts', 'ц'), ('yu', 'ю'), ('ya', 'я'), ('yo', 'ё'),
    ('ye', 'е'), ('iy', 'ий'), ('ey', 'ей'), ('ay', 'ай'), ('oy', 'ой'),
    ('uy', 'уй'), ('ee', 'и'), ('ph', 'ф'), ('ck', 'к'), ('x', 'кс'),
    ('th', 'т'), ('chr', 'кр'), ('ja', 'я'), ('ju', 'ю'), ('jo', 'йо'),
    ('j', 'дж'),
)

SINGLE = {
    'a': 'а', 'b': 'б', 'c': 'к', 'd': 'д', 'e': 'е', 'f': 'ф', 'g': 'г',
    'h': 'х', 'i': 'и', 'j': 'й', 'k': 'к', 'l': 'л', 'm': 'м', 'n': 'н',
    'o': 'о', 'p': 'п', 'q': 'к', 'r': 'р', 's': 'с', 't': 'т', 'u': 'у',
    'v': 'в', 'w': 'в', 'y': 'и', 'z': 'з', '-': '-',
}


# Имена нужны, чтобы понять порядок слов: «Ковригина Юлия» — это Юлия, а
# «Екатерина Зеленская» — Екатерина. По окончаниям не выйдет: «Екатерина»
# и «Марина» кончаются так же, как фамилия «Ковригина».
FIRST_NAMES = frozenset('''
александр алексей анатолий андрей антон аркадий арсений артем артём артур
богдан борис вадим валентин валерий василий виктор виталий владимир владислав
вячеслав геннадий георгий герман глеб григорий давид даниил данил данила денис
дмитрий евгений егор иван игорь илья кирилл константин лев леонид максим марк
матвей михаил никита николай олег павел пётр петр роман руслан рустам семён
семен сергей станислав степан тимофей тимур фёдор федор филипп эдуард юрий яков
ярослав гевог геворг ильдар айрат ринат марат наиль рафаэль
алина алла анастасия ангелина анна антонина валентина валерия варвара вера
вероника виктория галина дарья диана дина екатерина елена елизавета жанна
зинаида инна ирина карина кристина ксения лариса лидия любовь людмила маргарита
марина мария надежда наталия наталья нина оксана ольга полина раиса регина
светлана снежана софия софья таисия тамара татьяна ульяна юлия яна алёна алена
эльвира гульнара диляра лилия камила аида арина евгения
ваня саша серёжа сережа дима вася коля петя женя миша гриша толя костя витя
слава юра рома лёша леша тоха тёма тема стас макс лена катя маша даша таня оля
наташа настя света ира люда галя вера зина тася юля алиса ася
'''.split())


def is_first_name(word):
    return (word or '').strip().lower().strip('.,') in FIRST_NAMES


# Женские имена — списком, потому что окончание не решает: Никита и Илья
# кончаются на «а» и «я», а Любовь и Нинель — на согласную.
FEMALE = frozenset('''
алина алла анастасия ангелина анна антонина валентина валерия варвара вера
вероника виктория галина дарья диана дина екатерина елена елизавета жанна
зинаида инна ирина карина кристина ксения лариса лидия любовь людмила маргарита
марина мария надежда наталия наталья нина оксана ольга полина раиса регина
светлана снежана софия софья таисия тамара татьяна ульяна юлия яна алёна алена
эльвира гульнара диляра лилия камила аида арина евгения нинель
лена катя маша даша таня оля наташа настя света ира люда галя зина тася юля
алиса ася поля соня женя
'''.split())

MALE_ENDING_IN_VOWEL = frozenset('''
никита илья лёша леша дима вася коля петя миша гриша толя костя витя
юра рома тёма тема данила серёжа сережа тоха
'''.split())

# Одинаково зовут и мужчин, и женщин — гадать не будем
AMBIGUOUS = frozenset('саша женя валя слава'.split())


def gender(name):
    """«ж», «м» или '' — если непонятно.

    Нужно для прошедшего времени: «ты передавал» / «ты передавала». В форме
    «вы» рода нет, так что там это не спрашивают.
    """
    word = (name or '').strip().lower().strip('.,').split()[:1]
    if not word:
        return ''
    word = word[0]
    if word in AMBIGUOUS:
        return ''
    if word in FEMALE:
        return 'ж'
    if word in MALE_ENDING_IN_VOWEL or word in FIRST_NAMES:
        return 'м'
    return ''


TITLE_SPLIT = re.compile(r'[\s\[\]()|/,·]+')


def in_title(title):
    """Имена, которые видно в подписи чата. Кириллицей, как в списках."""
    found = []
    for word in TITLE_SPLIT.split(title or ''):
        word = word.strip(TRIM).lower()
        if not word:
            continue
        cyrillic = to_cyrillic(word) or word
        if is_first_name(cyrillic):
            found.append(cyrillic)
    return found


def trusted(name, title):
    """Имя из переписки — если оно не спорит с подписью чата.

    Модель иногда берёт имя того, о ком в переписке речь: в чате «Anna» она
    вычитала «Дмитрий Южанин» и предложила писать «Дмитрий, добрый день!».
    Расхождение по роду — надёжная примета такой подмены: на 3135 контактах
    сработало 6 раз, и все шесть — настоящие ошибки. Сокращениям («Макс»
    при подписи «Максим») это не мешает: род у них один.
    """
    name = (name or '').strip()
    if not name:
        return ''
    theirs = gender(name)
    if not theirs:
        return name
    for candidate in in_title(title):
        if gender(candidate) and gender(candidate) != theirs:
            return candidate.capitalize()
    return name


def pick_name(words):
    """Из «Фамилия Имя» и «Имя Фамилия» достаёт имя.

    Порядок в Telegram какой угодно: кто-то подписан «Ковригина Юлия».
    Если знакомое имя стоит вторым, берём его.
    """
    clean = [w.strip(TRIM) for w in list(words)[:3] if w.strip()]
    if not clean:
        return ''
    for word in clean:
        if is_first_name(word):
            return word.capitalize()
    return clean[0]


def to_cyrillic(word):
    """Латинское имя кириллицей. Не имя — пустая строка."""
    cleaned = (word or '').strip().strip(TRIM)
    if not LATIN_WORD.match(cleaned):
        return ''

    lowered = cleaned.lower()
    if lowered in EXCEPTIONS:
        return EXCEPTIONS[lowered]
    if lowered in NOT_PEOPLE:
        return ''
    # аббревиатуры вроде «AD», «GGRN», «KSK» — не имена
    if cleaned.isupper() and len(cleaned) <= 4:
        return ''

    text = lowered
    for latin, cyrillic in ENDINGS:
        if text.endswith(latin) and len(text) > len(latin) + 1:
            text = text[:-len(latin)] + '\x00' + cyrillic
            break
    for latin, cyrillic in DIGRAPHS:
        text = text.replace(latin, cyrillic)
    text = text.replace('\x00', '')
    result = ''.join(SINGLE.get(ch, ch if not ch.isascii() else '') for ch in text)
    if not result or len(result) < 2:
        return ''
    return result[0].upper() + result[1:]
