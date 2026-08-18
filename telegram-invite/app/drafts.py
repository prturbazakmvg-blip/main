# -*- coding: utf-8 -*-
"""Создание черновиков в Telegram с оглядкой на лимиты.

Программа ничего не отправляет: она только кладёт текст в поле ввода диалога
(messages.SaveDraft). Отправку делает человек руками. Рискованная часть здесь
не сама запись черновика, а contacts.ResolveUsername — обращение к Telegram за
незнакомым username. Поэтому между контактами держим случайную паузу, помним
уже обработанных и аккуратно переживаем FLOOD_WAIT.
"""
import asyncio
import random
from typing import Callable, List

from telethon import types
from telethon.errors.rpcerrorlist import (
    AuthKeyUnregisteredError,
    ChatWriteForbiddenError,
    FloodWaitError,
    InputUserDeactivatedError,
    PeerFloodError,
    PeerIdInvalidError,
    SessionRevokedError,
    UserDeactivatedBanError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
    UserPrivacyRestrictedError,
    YouBlockedUserError,
)
from telethon.tl.functions.messages import SaveDraftRequest

from . import progress, templates

# FLOOD_WAIT дольше этого порога означает, что аккаунт уже придержали.
# Продолжать в такой ситуации — прямой путь к спам-блоку.
MAX_FLOOD_WAIT_SECONDS = 300

# Ошибки, которые касаются конкретного контакта: пропускаем и идём дальше.
SKIPPABLE = (
    UsernameNotOccupiedError,
    UsernameInvalidError,
    ChatWriteForbiddenError,
    UserPrivacyRestrictedError,
    InputUserDeactivatedError,
    PeerIdInvalidError,
    YouBlockedUserError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
)

# Ошибки, после которых прогон надо останавливать немедленно.
FATAL = (
    PeerFloodError,
    UserDeactivatedBanError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
)


class RunAborted(Exception):
    """Прогон остановлен: аккаунт под ограничениями или сессия недействительна."""


# Прогон по большому списку идёт часами: ноутбук успевает уснуть, wi-fi —
# смениться. Telethon переподключается сам, но попытки у него не бесконечные,
# и после них соединение мертво навсегда: каждый следующий запрос падает с
# «Cannot send requests while disconnected». Список при этом не кончается, и
# программа молча портит остаток — поэтому связь проверяем сами.
RECONNECT_ATTEMPTS = 3
RECONNECT_PAUSE = 5

CONNECTION_LOST = (
    'Связь с Telegram оборвалась и не восстанавливается. Проверьте интернет '
    'и запустите прогон заново: созданные черновики уже отмечены, '
    'программа продолжит с того же места.'
)


async def _ensure_connected(client, on_event):
    """Возвращает True, если связь есть или её удалось восстановить."""
    if client.is_connected():
        return True
    for attempt in range(1, RECONNECT_ATTEMPTS + 1):
        on_event('reconnect', attempt=attempt, attempts=RECONNECT_ATTEMPTS)
        try:
            await client.connect()
        except Exception:
            pass
        if client.is_connected():
            on_event('reconnected')
            return True
        await asyncio.sleep(RECONNECT_PAUSE * attempt)
    return False


class Summary(object):
    def __init__(self):
        self.created = 0
        self.skipped = 0
        self.failed = 0
        self.aborted_reason = ''
        self.stopped = False
        self.problems = []

    @property
    def processed(self):
        return self.created + self.skipped + self.failed


async def _sleep_interruptibly(seconds, should_stop, on_tick=None):
    """Спит посекундно, чтобы кнопка «Стоп» срабатывала сразу.

    Возвращает False, если попросили остановиться.
    """
    remaining = int(seconds)
    while remaining > 0:
        if should_stop is not None and should_stop():
            return False
        await asyncio.sleep(1)
        remaining -= 1
        if on_tick is not None:
            on_tick(remaining)
    return should_stop is None or not should_stop()


async def _resolve(client, username):
    """Отдельно, чтобы FLOOD_WAIT ловился именно на резолве username."""
    return await client.get_input_entity(username)


async def _call_with_flood_retry(action: Callable, on_event: Callable, attempts: int = 2):
    """Выполняет запрос, переживая короткие FLOOD_WAIT."""
    last_error = None
    for attempt in range(attempts):
        try:
            return await action()
        except FloodWaitError as e:
            last_error = e
            if e.seconds > MAX_FLOOD_WAIT_SECONDS:
                raise
            if attempt == attempts - 1:
                raise
            on_event('flood', seconds=e.seconds)
            await asyncio.sleep(e.seconds + 5)
    raise last_error


async def create_drafts(
    client,
    contacts: List,
    variants: List[str],
    contacts_file: str,
    delay_range,
    on_event: Callable,
    rng=None,
    dry_run: bool = False,
    should_stop=None,
    use_contact_message: bool = False,
    prepare=None,
    should_skip=None,
) -> Summary:
    """Создаёт черновики для списка контактов.

    contacts_file — куда писать отметку «обработано», если у контакта не
    задан свой source (режим «вся папка» проставляет source у каждого).
    on_event(kind, **details) — для вывода прогресса.
    """
    rng = rng or random.Random()
    low, high = delay_range
    summary = Summary()
    total = len(contacts)

    for index, contact in enumerate(contacts):
        if should_stop is not None and should_stop():
            summary.stopped = True
            on_event('stopped')
            break

        # до чтения переписки: без связи и доводка текста, и черновик всё
        # равно не получатся, а ошибок будет столько же, сколько контактов
        if client is not None and not await _ensure_connected(client, on_event):
            summary.aborted_reason = CONNECTION_LOST
            on_event('aborted', reason=summary.aborted_reason)
            raise RunAborted(summary.aborted_reason)

        if should_skip is not None:
            # например, этого человека уже звали на то же мероприятие
            try:
                reason = await should_skip(contact)
            except Exception:
                reason = ''
            if reason:
                summary.skipped += 1
                summary.problems.append('{}: {}'.format(contact.label, reason))
                on_event('skipped', contact=contact, reason=reason)
                progress.mark_done(contact.source or contacts_file, contact.key)
                continue

        values = {'NAME': contact.name, 'USERNAME': contact.username}
        if use_contact_message:
            # текст берём из колонки таблицы, а не из шаблона
            if not (contact.message or '').strip():
                summary.skipped += 1
                summary.problems.append('{}: в таблице нет текста'.format(contact.label))
                on_event('skipped', contact=contact, reason='в таблице пустая колонка с текстом')
                continue
            text = templates.substitute(contact.message, values)
        else:
            text = templates.render(variants, values, rng)

        if prepare is not None:
            # доводка текста под конкретного человека; при осечке остаётся
            # заготовка — сорвать весь прогон из-за этого нельзя
            try:
                prepared, note = await prepare(contact, text)
                if prepared:
                    text = prepared
                    if note:
                        on_event('prepared', contact=contact, note=note)
            except Exception as e:
                on_event('prepare_failed', contact=contact, error=e)

        on_event('start', index=index + 1, total=total, contact=contact, text=text)

        if dry_run:
            summary.created += 1
            continue

        try:
            if contact.peer is not None:
                # человек уже в диалогах: ResolveUsername не нужен
                entity = contact.peer
            else:
                entity = await _call_with_flood_retry(
                    lambda c=contact: _resolve(client, c.username), on_event
                )

            # Проверка нужна только когда мы искали по username: там легко
            # попасть в канал-однофамилец. Если peer пришёл из папки Telegram,
            # доверяем ему — черновик кладётся и в группу, и в канал.
            if contact.peer is None and not isinstance(entity, types.InputPeerUser):
                summary.skipped += 1
                summary.problems.append(
                    '{}: по этому username не личный аккаунт'.format(contact.label))
                on_event('skipped', contact=contact, reason='не личный аккаунт')
                progress.mark_done(contact.source or contacts_file, contact.key)
                continue

            await _call_with_flood_retry(
                lambda e=entity, t=text: client(
                    SaveDraftRequest(peer=e, message=t, no_webpage=False, entities=[])
                ),
                on_event,
            )

        except FATAL as e:
            summary.aborted_reason = _describe_fatal(e)
            on_event('aborted', reason=summary.aborted_reason)
            raise RunAborted(summary.aborted_reason)

        except FloodWaitError as e:
            summary.aborted_reason = (
                'Telegram просит подождать {} мин. Аккаунт упёрся в лимит — '
                'на сегодня стоит остановиться.'.format(max(1, e.seconds // 60))
            )
            on_event('aborted', reason=summary.aborted_reason)
            raise RunAborted(summary.aborted_reason)

        except ConnectionError as e:
            # оборвалось прямо на запросе: одна осечка — не повод бросать
            # список, но и молотить вхолостую по остатку нельзя
            if not await _ensure_connected(client, on_event):
                summary.aborted_reason = CONNECTION_LOST
                on_event('aborted', reason=summary.aborted_reason)
                raise RunAborted(summary.aborted_reason)
            summary.failed += 1
            summary.problems.append('{}: связь оборвалась, {}'.format(
                contact.label, e))
            on_event('failed', contact=contact, error=e)
            continue

        except SKIPPABLE as e:
            summary.skipped += 1
            reason = _describe_skip(e, contact.username)
            summary.problems.append('{}: {}'.format(contact.label, reason))
            on_event('skipped', contact=contact, reason=reason)
            # Помечаем обработанным: повторный резолв несуществующего
            # username при следующем запуске — лишний риск без пользы.
            progress.mark_done(contact.source or contacts_file, contact.key)
            continue

        except Exception as e:  # непредвиденное — не роняем весь прогон
            summary.failed += 1
            summary.problems.append('{}: неожиданная ошибка {}: {}'.format(
                contact.label, type(e).__name__, e))
            on_event('failed', contact=contact, error=e)
            continue

        summary.created += 1
        progress.mark_done(contact.source or contacts_file, contact.key)
        progress.bump_today(1)
        on_event('created', contact=contact)

        if index < total - 1:
            pause = rng.randint(low, high)
            on_event('pause', seconds=pause)
            keep_going = await _sleep_interruptibly(
                pause, should_stop, lambda left: on_event('tick', seconds=left)
            )
            if not keep_going:
                summary.stopped = True
                on_event('stopped')
                break

    return summary


def _describe_skip(error, username):
    if isinstance(error, UsernameNotOccupiedError):
        return 'такого username больше не существует'
    if isinstance(error, UsernameInvalidError):
        return 'username записан неверно'
    if isinstance(error, ChatWriteForbiddenError):
        return 'нельзя писать в этот чат'
    if isinstance(error, UserPrivacyRestrictedError):
        return 'настройки приватности запрещают писать'
    if isinstance(error, InputUserDeactivatedError):
        return 'аккаунт удалён'
    if isinstance(error, YouBlockedUserError):
        return 'вы заблокировали этого пользователя'
    if isinstance(error, ValueError):
        return 'не удалось найти пользователя'
    return '{}: {}'.format(type(error).__name__, error)


def _describe_fatal(error):
    if isinstance(error, PeerFloodError):
        return (
            'Telegram ответил PEER_FLOOD: аккаунт ограничен за массовые обращения '
            'к незнакомым людям. Прекратите рассылку минимум на несколько дней и '
            'напишите в @SpamBot.'
        )
    if isinstance(error, UserDeactivatedBanError):
        return 'Аккаунт заблокирован Telegram.'
    if isinstance(error, (AuthKeyUnregisteredError, SessionRevokedError)):
        return 'Сессия недействительна: вход завершён с другого устройства. Нужно войти заново.'
    return '{}: {}'.format(type(error).__name__, error)
