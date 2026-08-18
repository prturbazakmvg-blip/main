# -*- coding: utf-8 -*-
"""Запуск оконной версии. Она же собирается в .app для macOS.

Всё завёрнуто в перехват: у собранного приложения нет консоли, и любая
ошибка при старте выглядит для человека как «значок мигнул и погас». Поэтому
падение пишем в файл рядом с настройками и показываем окном — иначе
разбираться не с чем.
"""
import multiprocessing
import sys
import traceback


def whoami():
    """Версия, macOS и откуда запущено — три строки, которые всё объясняют.

    Без них разбор падения превращается в переписку: «а какая версия?»,
    «а не осталось ли второй копии?». На скриншоте окна это видно сразу.
    """
    import platform
    lines = []
    try:
        from app import __version__
        lines.append('Версия {}'.format(__version__))
    except Exception:
        lines.append('Версия неизвестна')
    try:
        lines.append('macOS {}'.format(platform.mac_ver()[0] or '?'))
    except Exception:
        pass
    try:
        path = sys.executable
        for part in ('.app/',):
            if part in path:
                path = path.split(part)[0] + '.app'
        lines.append(path)
    except Exception:
        pass
    return '\n'.join(lines)


def report(error):
    """Записать падение и показать его человеку."""
    text = ''.join(traceback.format_exception(type(error), error,
                                              error.__traceback__))
    head = whoami()
    where = ''
    try:
        from app.paths import STATE_DIR, ensure_dirs
        ensure_dirs()
        path = STATE_DIR / 'crash.txt'
        with open(path, 'a', encoding='utf-8') as f:
            from datetime import datetime
            f.write('\n=== {} ===\n{}\n{}'.format(
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'), head, text))
        where = '\n\nПодробности записаны в\n{}'.format(path)
    except Exception:
        pass

    sys.stderr.write(head + '\n' + text)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            'Программа не смогла запуститься',
            '{}\n\n{}: {}{}'.format(head, type(error).__name__, error, where))
        root.destroy()
    except Exception:
        pass


def main():
    multiprocessing.freeze_support()
    try:
        from app.gui import run
        run()
    except SystemExit:
        raise
    except BaseException as error:      # noqa: BLE001 — тут ловим всё
        report(error)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
