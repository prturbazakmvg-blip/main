# -*- coding: utf-8 -*-
"""Совместимость со старым запуском.

Логика переехала в пакет app/. Этот файл оставлен, чтобы прежние команды
не сломались. Новая точка входа — run.py.
"""
import sys

from app.main import main
from app.ui import setup_console, warn


def translate(argv):
    """Переводит старые флаги в новые."""
    translated = []
    skip_next = False

    for index, arg in enumerate(argv):
        if skip_next:
            skip_next = False
            continue

        if arg in ('--csv', '--template'):
            translated.append(arg)
            if index + 1 < len(argv):
                translated.append(argv[index + 1])
                skip_next = True
        elif arg.startswith('--csv=') or arg.startswith('--template='):
            translated.append(arg)
        elif arg == '--download' or arg == '--no-download':
            if arg == '--download':
                translated.append('--download')
        elif arg == '--batch':
            skip_next = True
            warn('--batch больше не нужен: программа сама помнит, кто уже обработан.')
            warn('Сколько взять за раз, задаётся флагом --count.')
        elif arg.startswith('--batch='):
            warn('--batch больше не нужен, используйте --count.')
        elif arg in ('--creds', '--csv-delimeter'):
            skip_next = True
            warn('{} больше не поддерживается и проигнорирован.'.format(arg))
        elif arg.startswith('--creds=') or arg.startswith('--csv-delimeter='):
            warn('{} больше не поддерживается и проигнорирован.'.format(arg.split('=')[0]))
        else:
            translated.append(arg)

    return translated


if __name__ == '__main__':
    setup_console()
    warn('create_messages.py устарел. Запускайте run.py (или собранный exe).')
    main(translate(sys.argv[1:]))
