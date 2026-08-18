# -*- coding: utf-8 -*-
"""Подключение к Telegram и вход в аккаунт.

Вопросы пользователю задаются через объект prompts, поэтому один и тот же
код входа работает и в консоли, и в окне.
"""
import asyncio
import getpass

from telethon import TelegramClient
from telethon.errors.rpcerrorlist import (
    ApiIdInvalidError,
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

from . import __version__
from .paths import SESSION_PATH, ensure_dirs
from .ui import ask, error, info

# Постоянные значения: сессия, которая каждый раз представляется новым
# устройством, выглядит подозрительно и чаще требует переподтверждения.
DEVICE_MODEL = 'Desktop'
SYSTEM_VERSION = 'Windows 10'
APP_VERSION = 'telegram-invite {}'.format(__version__)


class LoginFailed(Exception):
    pass


class LoginCancelled(LoginFailed):
    """Пользователь закрыл окно входа."""


class ConsolePrompts(object):
    """Вопросы через терминал. Блокирующий input уводим в поток,
    чтобы не морозить цикл событий, пока Telethon держит соединение."""

    async def _in_thread(self, func, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func, *args)

    async def ask(self, label):
        return await self._in_thread(ask, label)

    async def ask_secret(self, label):
        return await self._in_thread(getpass.getpass, label + ': ')

    def notify(self, text):
        info(text)

    def failed(self, text):
        error(text)


def build_client(config) -> TelegramClient:
    ensure_dirs()
    try:
        api_id = int(str(config['api_id']).strip())
    except (KeyError, TypeError, ValueError):
        raise LoginFailed('api_id должен быть числом. Проверьте credentials.json.')

    api_hash = str(config.get('api_hash', '')).strip()
    if not api_hash:
        raise LoginFailed('Не заполнен api_hash. Проверьте credentials.json.')

    return TelegramClient(
        str(SESSION_PATH),
        api_id,
        api_hash,
        device_model=DEVICE_MODEL,
        system_version=SYSTEM_VERSION,
        app_version=APP_VERSION,
        lang_code='ru',
        system_lang_code='ru',
        # Прогон по большому списку идёт часами. С настройками по умолчанию
        # (5 попыток раз в секунду) короткого сна ноутбука хватает, чтобы
        # соединение умерло насовсем. Даём Telethon почти минуту на то,
        # чтобы подняться самому; если не выйдет — переподключается drafts.
        connection_retries=10,
        retry_delay=5,
        request_retries=5,
        timeout=20,
    )


async def ensure_authorized(client, prompts=None) -> None:
    """Подключается и, если нужно, проводит вход. Файл сессии не трогаем никогда."""
    prompts = prompts or ConsolePrompts()

    try:
        await client.connect()
    except OSError as e:
        raise LoginFailed('Нет соединения с Telegram: {}'.format(e))

    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            prompts.notify('Вход выполнен: {} {}'.format(
                (me.first_name or '').strip(),
                ('@' + me.username) if me.username else '',
            ).strip())
            return
    except (ApiIdInvalidError,) as e:
        raise LoginFailed('api_id и api_hash не подходят друг к другу: {}'.format(e))

    await _interactive_login(client, prompts)


async def _interactive_login(client, prompts) -> None:
    prompts.notify('')
    prompts.notify('Нужен вход в Telegram. Код придёт в само приложение Telegram,')
    prompts.notify('в чат «Telegram». Это разовая процедура — дальше программа помнит вход.')
    prompts.notify('')

    phone = await prompts.ask('Номер телефона в формате +79991234567')
    if not phone:
        raise LoginCancelled('Номер не введён')

    try:
        sent = await client.send_code_request(phone)
    except PhoneNumberInvalidError:
        raise LoginFailed('Telegram не принял этот номер')
    except ApiIdInvalidError:
        raise LoginFailed('api_id и api_hash не подходят друг к другу. Проверьте credentials.json.')
    except FloodWaitError as e:
        raise LoginFailed(
            'Слишком много попыток входа. Telegram просит подождать {} мин.'.format(
                max(1, e.seconds // 60))
        )

    for attempt in range(3):
        code = await prompts.ask('Код из Telegram')
        if not code:
            raise LoginCancelled('Код не введён')
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            break
        except PhoneCodeInvalidError:
            if attempt == 2:
                raise LoginFailed('Код неверный')
            prompts.failed('Код неверный. Осталось попыток: {}'.format(2 - attempt))
        except PhoneCodeExpiredError:
            raise LoginFailed('Код истёк. Запустите вход заново.')
        except SessionPasswordNeededError:
            await _ask_password(client, prompts)
            break
    else:
        raise LoginFailed('Не удалось подтвердить код')

    me = await client.get_me()
    if me is None:
        raise LoginFailed('Вход не завершился')
    prompts.notify('Готово, вошли как {}'.format((me.first_name or '').strip() or me.id))


async def _ask_password(client, prompts) -> None:
    prompts.notify('У аккаунта включён облачный пароль (двухфакторная защита).')
    for attempt in range(3):
        try:
            password = await prompts.ask_secret('Облачный пароль')
        except (EOFError, KeyboardInterrupt):
            raise LoginCancelled('Ввод пароля прерван')
        if not password:
            raise LoginCancelled('Пароль не введён')
        try:
            await client.sign_in(password=password)
            return
        except Exception as e:
            if attempt == 2:
                raise LoginFailed('Пароль не подошёл')
            prompts.failed('Пароль не подошёл ({}). Осталось попыток: {}'.format(
                type(e).__name__, 2 - attempt))
    raise LoginFailed('Не удалось войти с облачным паролем')


async def logout(client) -> bool:
    """Явный выход по просьбе пользователя — единственный случай, когда сессия стирается."""
    try:
        await client.log_out()
    except Exception:
        return False
    return True
