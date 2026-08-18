# -*- coding: utf-8 -*-
"""Простой консольный интерфейс: вывод и вопросы."""
import sys
from typing import List, Optional, Sequence


def setup_console() -> None:
    """Windows-консоль по умолчанию не в utf-8 — иначе кириллица падает."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            try:
                reconfigure(encoding='utf-8', errors='replace')
            except (ValueError, OSError):
                pass


def write(text: str = '') -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', 'replace').decode('ascii'))
    sys.stdout.flush()


def info(text: str = '') -> None:
    write(text)


def warn(text: str) -> None:
    write('  ! ' + text)


def error(text: str) -> None:
    write('  x ' + text)


def ok(text: str) -> None:
    write('  + ' + text)


def title(text: str) -> None:
    write('')
    write('=' * 64)
    write('  ' + text)
    write('=' * 64)


def rule() -> None:
    write('-' * 64)


def ask(prompt: str, default: str = '') -> str:
    suffix = ' [{}]'.format(default) if default else ''
    try:
        answer = input('{}{}: '.format(prompt, suffix)).strip()
    except EOFError:
        return default
    return answer or default


def ask_int(prompt: str, default: int, minimum: int = 0, maximum: Optional[int] = None) -> int:
    while True:
        raw = ask(prompt, str(default))
        try:
            value = int(raw)
        except ValueError:
            error('Нужно число')
            continue
        if value < minimum:
            error('Минимум {}'.format(minimum))
            continue
        if maximum is not None and value > maximum:
            error('Максимум {}'.format(maximum))
            continue
        return value


def confirm(prompt: str, default: bool = False) -> bool:
    hint = 'Д/н' if default else 'д/Н'
    while True:
        raw = ask('{} ({})'.format(prompt, hint))
        if not raw:
            return default
        first = raw[:1].lower()
        if first in ('д', 'y', '1'):
            return True
        if first in ('н', 'n', '0'):
            return False
        error('Ответьте «д» или «н»')


def choose(prompt: str, options: Sequence[str], allow_cancel: bool = True) -> Optional[int]:
    """Показывает нумерованный список, возвращает индекс или None при отмене."""
    if not options:
        return None
    for index, option in enumerate(options, start=1):
        write('  {:>2}) {}'.format(index, option))
    if allow_cancel:
        write('   0) назад')
    write('')

    while True:
        raw = ask(prompt)
        if not raw:
            continue
        if raw == '0' and allow_cancel:
            return None
        try:
            index = int(raw)
        except ValueError:
            error('Введите номер из списка')
            continue
        if 1 <= index <= len(options):
            return index - 1
        error('Введите номер из списка')


def show_problems(problems: List[str], limit: int = 15) -> None:
    if not problems:
        return
    write('')
    write('Замечания ({}):'.format(len(problems)))
    for problem in problems[:limit]:
        write('  - ' + problem)
    if len(problems) > limit:
        write('  ... и ещё {}'.format(len(problems) - limit))


def pause_exit(code: int = 0) -> None:
    """Иначе при запуске двойным кликом окно закроется мгновенно."""
    write('')
    try:
        input('Нажмите Enter, чтобы закрыть окно...')
    except (EOFError, KeyboardInterrupt):
        pass
    sys.exit(code)
