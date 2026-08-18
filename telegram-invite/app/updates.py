# -*- coding: utf-8 -*-
"""Проверка и установка новой версии — в один клик, без терминала.

Хитрость здесь одна, и она решает всю задачу. macOS блокирует скачанные
программы не потому, что они «чужие», а потому, что браузер вешает на файл
метку карантина. Скачивание, сделанное самой программой, метки не ставит —
значит обновление ставится молча, без «не удалось проверить разработчика»
и без разрешений в системных настройках. Ровно поэтому обновляться нужно
изнутри, а не ссылкой на архив.

Порядок такой:

    1. читаем latest.json на сервере — там версия, ссылка и sha256;
    2. качаем архив во временную папку и сверяем контрольную сумму;
    3. распаковываем через ditto: он сохраняет симлинки и подпись,
       обычный zipfile их портит, и приложение перестаёт запускаться;
    4. проверяем подпись распакованного;
    5. запускаем отдельный процесс, который дождётся выхода программы,
       подменит папку и откроет новую версию.

Пятый шаг нужен потому, что своё же приложение нельзя заменить, пока оно
работает: файлы открыты, и половина замены прошла бы вхолостую.
"""
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__

SITE_URL = 'https://tgsoft.fi.leadget.ru/'
FEED_URL = SITE_URL + 'telegram-invite/latest.json'
TIMEOUT = 20
CHUNK = 64 * 1024

class UpdateError(Exception):
    """Обновиться не вышло — с человеческим объяснением."""


def as_numbers(version):
    """'2.4.1' -> (2, 4, 1). Мусор превращается в нули, а не в ошибку."""
    parts = []
    for chunk in str(version or '').split('.')[:4]:
        digits = ''.join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(there, here=None):
    return as_numbers(there) > as_numbers(here or __version__)


def _opener():
    """Свой opener: раздача открыта, логин не нужен."""
    return urllib.request.build_opener()


def check(url=FEED_URL):
    """Что лежит на сервере. None — если там не новее нашего.

    Ошибок наружу не отдаём: проверка идёт молча при запуске, и «нет сети»
    не повод показывать окно с ошибкой.
    """
    try:
        with _opener().open(url, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    if not isinstance(data, dict) or not data.get('url'):
        return None
    if not is_newer(data.get('version')):
        return None
    return data


def download(info, on_progress=None):
    """Качает архив во временную папку и сверяет sha256."""
    folder = Path(tempfile.mkdtemp(prefix='telegram-invite-update-'))
    target = folder / 'update.zip'
    digest = hashlib.sha256()
    try:
        with _opener().open(info['url'], timeout=TIMEOUT) as response:
            total = int(response.headers.get('Content-Length') or 0)
            done = 0
            with open(target, 'wb') as out:
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateError('Не удалось скачать обновление: {}'.format(e))

    expected = (info.get('sha256') or '').strip().lower()
    if expected and digest.hexdigest() != expected:
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateError('Файл скачался повреждённым — обновление отменено.')
    return target


def unpack(archive):
    """Распаковывает и возвращает путь к .app внутри.

    ditto, а не zipfile: внутри бандла есть симлинки и подпись, и штатный
    распаковщик Python их не сохраняет — приложение потом не запускается.
    """
    folder = archive.parent / 'unpacked'
    folder.mkdir(exist_ok=True)
    result = subprocess.run(['ditto', '-xk', str(archive), str(folder)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise UpdateError('Архив не распаковался: {}'.format(
            (result.stderr or '').strip()[:200]))
    apps = sorted(folder.glob('*.app'))
    if not apps:
        raise UpdateError('В архиве нет приложения.')
    return apps[0]


def verify(app_path):
    """Подпись цела и версия действительно новее нашей."""
    result = subprocess.run(['codesign', '--verify', '--strict', str(app_path)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise UpdateError('Подпись скачанного приложения повреждена — '
                          'обновление отменено.')
    plist = app_path / 'Contents' / 'Info.plist'
    try:
        with open(plist, 'rb') as f:
            version = plistlib.load(f).get('CFBundleShortVersionString', '')
    except (OSError, ValueError):
        raise UpdateError('В приложении нет номера версии.')
    if not is_newer(version):
        raise UpdateError('Скачалась версия {}, а у нас уже {}.'.format(
            version, __version__))
    return version


def installed_bundle():
    """Путь к .app, из которого мы запущены. None — если запуск из исходников."""
    if not getattr(sys, 'frozen', False):
        return None
    parts = Path(sys.executable).resolve().parts
    for index in range(len(parts) - 1, 0, -1):
        if parts[index].endswith('.app'):
            return Path(*parts[:index + 1])
    return None


# Подменяет папку и открывает новую версию. Ждём выхода по исчезновению
# процесса, а не по сигналу: программа закрывается сама сразу после запуска
# этого скрипта, и ловить там нечего.
SWAP = '''#!/bin/sh
# Ждём выхода программы: заменить работающий бандл нельзя, файлы открыты.
for i in $(seq 1 60); do
  kill -0 {pid} 2>/dev/null || break
  sleep 0.5
done
sleep 1

[ -d "{source}" ] || exit 1

# Сначала собираем новую копию рядом и только потом меняем местами. Если
# сделать наоборот — отодвинуть старую и копировать на её место, — сбой
# копирования оставит человека вообще без программы.
rm -rf "{target}.new" "{target}.old"
if ! ditto "{source}" "{target}.new"; then
  rm -rf "{target}.new"
  open "{target}"
  exit 1
fi

if [ -d "{target}" ] && ! mv "{target}" "{target}.old"; then
  rm -rf "{target}.new"
  open "{target}"
  exit 1
fi
if ! mv "{target}.new" "{target}"; then
  [ -d "{target}.old" ] && mv "{target}.old" "{target}"
  open "{target}"
  exit 1
fi

rm -rf "{target}.old"
xattr -dr com.apple.quarantine "{target}" 2>/dev/null
open -n "{target}"
rm -rf "{workdir}"
'''


def install(new_app, target=None):
    """Ставит скачанную версию. После вызова программу надо закрыть.

    Возвращает путь к скрипту подмены — он уже запущен и ждёт выхода.
    """
    target = target or installed_bundle()
    if target is None:
        raise UpdateError('Обновление работает только для собранного '
                          'приложения, а не для запуска из исходников.')
    if not os.access(str(Path(target).parent), os.W_OK):
        raise UpdateError('Нет прав на запись в {}. Перенесите приложение '
                          'в «Программы» внутри домашней папки.'.format(
                              Path(target).parent))
    script = new_app.parent.parent / 'swap.sh'
    script.write_text(SWAP.format(pid=os.getpid(), source=new_app,
                                  target=target,
                                  workdir=new_app.parent.parent),
                      encoding='utf-8')
    script.chmod(0o755)
    subprocess.Popen(['/bin/sh', str(script)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return script
