# -*- coding: utf-8 -*-
"""Шаблоны сообщений и подстановка переменных.

Файл шаблона может содержать несколько вариантов текста, разделённых строкой
из трёх и более дефисов. Для каждого контакта вариант выбирается случайно —
сотня абсолютно одинаковых сообщений это самый заметный признак рассылки.
"""
import random
import re
from typing import Dict, List

from .paths import DATA_DIR, TEMPLATES_DIR, bundled_dir, ensure_dirs

VARIANT_SEPARATOR = re.compile(r'^-{3,}\s*$', re.MULTILINE)

# Поддерживаются обе формы: {NAME} и голое NAME.
# \b не даёт NAME совпасть внутри USERNAME — у старой версии здесь был баг.
PLACEHOLDERS = ('NAME', 'USERNAME')


class TemplateError(Exception):
    pass


def available() -> List[str]:
    """Список доступных файлов шаблонов (абсолютные пути в виде строк)."""
    ensure_dirs()
    found = []
    legacy = DATA_DIR / 'template.txt'
    if legacy.exists():
        found.append(legacy)
    for path in sorted(TEMPLATES_DIR.glob('*.txt')):
        found.append(path)
    return [str(p) for p in found]


def load_variants(file_name: str) -> List[str]:
    try:
        with open(file_name, encoding='utf-8') as f:
            raw = f.read()
    except UnicodeDecodeError:
        with open(file_name, encoding='cp1251') as f:
            raw = f.read()
    except OSError as e:
        raise TemplateError('Не удалось прочитать шаблон {}: {}'.format(file_name, e))

    variants = [chunk.strip() for chunk in VARIANT_SEPARATOR.split(raw)]
    variants = [v for v in variants if v]
    if not variants:
        raise TemplateError('Шаблон {} пустой'.format(file_name))
    return variants


# Варианты прямо в тексте: {так|или так|или вот так}. Нужны, чтобы сотня
# сообщений не была слово в слово одинаковой — это самый заметный признак
# рассылки. {NAME} сюда не попадает: внутри нет вертикальной черты.
SPIN = re.compile(r'\{([^{}|]*\|[^{}]*)\}')


def spin(text: str, rng: random.Random) -> str:
    """Раскрывает {а|б|в}, выбирая по одному варианту."""
    def pick(match):
        options = [part.strip() for part in match.group(1).split('|')]
        options = [o for o in options if o] or ['']
        return rng.choice(options)

    return SPIN.sub(pick, text or '')


def spin_options(text: str) -> int:
    """Сколько разных сообщений даёт текст — для подсказки в окне."""
    total = 1
    for match in SPIN.finditer(text or ''):
        count = len([p for p in match.group(1).split('|') if p.strip()])
        total *= max(1, count)
    return total


def substitute(text: str, values: Dict[str, str]) -> str:
    result = text
    for key in PLACEHOLDERS:
        value = values.get(key, '')
        result = result.replace('{' + key + '}', value)
        result = re.sub(r'\b' + key + r'\b', lambda _m, v=value: v, result)
    return result


def render(variants: List[str], values: Dict[str, str], rng: random.Random) -> str:
    return substitute(spin(rng.choice(variants), rng), values)


def missing_placeholders(variants: List[str]) -> List[str]:
    """Варианты, в которых нет ни одного упоминания имени — вероятная опечатка."""
    broken = []
    for index, variant in enumerate(variants, start=1):
        has_name = '{NAME}' in variant or re.search(r'\bNAME\b', variant)
        if not has_name:
            broken.append('вариант {}'.format(index))
    return broken


DEFAULT_TEXT = """Привет, {NAME}! Мы тут проводим AI Growth Day (ex AGDAYs).

Единственная конфа с докладами про то как ИТ-компаниям и digital-агентствам
перестроиться на ИИ-рельсы.

Это будет 28-го августа, в последнюю пятницу лета в Екатеринбурге.

Буду рад видеть, если соберешься, то вышлю промокод на 11%.
"""


def ensure_default():
    """Кладёт текст по умолчанию, если у пользователя ещё ничего нет."""
    ensure_dirs()
    if available():
        return

    target = DATA_DIR / 'template.txt'
    bundled = bundled_dir() / 'template.txt'
    if bundled.exists() and bundled.resolve() != target.resolve():
        import shutil
        shutil.copyfile(bundled, target)
    else:
        with open(target, 'w', encoding='utf-8') as f:
            f.write(DEFAULT_TEXT)
