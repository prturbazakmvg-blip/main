# -*- coding: utf-8 -*-
"""Папки чатов из самого Telegram.

Отличие от списков в csv: адресаты из папки уже есть у вас в диалогах, поэтому
их не нужно искать через contacts.ResolveUsername — а именно этот вызов и
лимитируется жёстче всего. Имена берутся пачкой одним запросом.

Черновик кладётся в любой чат, включая группы и каналы, — Telegram это
разрешает. Поэтому фильтра «только личные» здесь нет, есть только пометка
вида чата, чтобы пользователь мог выбрать сам.
"""
from telethon import utils
from telethon.tl import types
from telethon.tl.functions.messages import GetDialogFiltersRequest

from .contacts import Contact

# Папки, собранные по признаку («все контакты», «все группы»), не хранят
# список чатов — там только флаги. Явно добавленные чаты мы видим, остальное нет.
DYNAMIC_FLAGS = ('contacts', 'non_contacts', 'groups', 'broadcasts', 'bots')

PERSON = 'человек'
GROUP = 'группа'
CHANNEL = 'канал'


def _title(raw):
    """В новых слоях заголовок — TextWithEntities, в старых просто строка."""
    return getattr(raw, 'text', raw) or 'Без названия'


def _is_dynamic(dialog_filter):
    return any(getattr(dialog_filter, flag, None) for flag in DYNAMIC_FLAGS)


def _peer_kind(peer):
    if isinstance(peer, types.InputPeerUser):
        return PERSON
    if isinstance(peer, types.InputPeerChat):
        return GROUP
    if isinstance(peer, types.InputPeerChannel):
        return CHANNEL
    return None


def _peer_id(peer, kind):
    if kind == PERSON:
        return peer.user_id
    if kind == GROUP:
        return peer.chat_id
    return peer.channel_id


async def list_folders(client):
    """Только список папок — один запрос, без чтения названий чатов.

    Названия стоят дорого: у аккаунта легко бывает два десятка папок и под
    тысячу чатов суммарно. Их читает load_contacts, и только для той папки,
    которую выбрали.
    """
    raw = await client(GetDialogFiltersRequest())
    filters = getattr(raw, 'filters', raw) or []

    folders = []
    for dialog_filter in filters:
        # «Все чаты» — не папка, а состояние по умолчанию
        if isinstance(dialog_filter, types.DialogFilterDefault):
            continue

        peers = list(getattr(dialog_filter, 'pinned_peers', None) or [])
        peers += list(getattr(dialog_filter, 'include_peers', None) or [])

        kept = []
        counts = {PERSON: 0, GROUP: 0, CHANNEL: 0}
        seen = set()
        for peer in peers:
            kind = _peer_kind(peer)
            if kind is None:
                continue
            key = (kind, _peer_id(peer, kind))
            if key in seen:
                continue
            seen.add(key)
            counts[kind] += 1
            kept.append((peer, kind))

        folders.append({
            'title': _title(dialog_filter.title),
            'peers': kept,                     # [(InputPeer, вид)]
            'contacts': None,                  # названия ещё не читали
            'dynamic': _is_dynamic(dialog_filter),
            'people': counts[PERSON],
            'groups': counts[GROUP],
            'channels': counts[CHANNEL],
            'total': len(kept),
        })

    return folders


async def load_contacts(client, folder):
    """Дочитывает названия для одной папки. Возвращает список Contact."""
    if folder['contacts'] is not None:
        return folder['contacts']
    folder['contacts'] = await _contacts_for(client, folder['peers'], folder['title'])
    return folder['contacts']


async def _contacts_for(client, peers, folder_title):
    if not peers:
        return []

    try:
        # Telethon сам разложит запрос на users.GetUsers / messages.GetChats /
        # channels.GetChannels — смешанный список ему не мешает
        entities = await client.get_entity([peer for peer, _kind in peers])
    except Exception:
        return []

    if not isinstance(entities, list):
        entities = [entities]

    source = 'telegram-папка {}'.format(folder_title)
    contacts = []
    for entity, (_peer, kind) in zip(entities, peers):
        if getattr(entity, 'bot', False) or getattr(entity, 'deleted', False):
            continue
        if getattr(entity, 'is_self', False):
            continue
        # ни имени, ни username — так выглядит удалённый аккаунт
        if not (utils.get_display_name(entity) or '').strip() \
                and not (getattr(entity, 'username', '') or '').strip():
            continue
        # В канал без права писать черновик всё равно не ляжет —
        # такие Telegram отсеет сам, а мы аккуратно пропустим по ошибке
        if kind == CHANNEL and getattr(entity, 'broadcast', False) \
                and not getattr(entity, 'creator', False) \
                and not getattr(entity, 'admin_rights', None):
            continue

        if kind == PERSON:
            name = (entity.first_name or '').strip() or (entity.last_name or '').strip()
            username = entity.username or ''
            key = username.lower() or 'id{}'.format(entity.id)
        else:
            name = (utils.get_display_name(entity) or '').strip()
            username = getattr(entity, 'username', '') or ''
            key = '{}{}'.format('chat' if kind == GROUP else 'ch', entity.id)

        contacts.append(Contact(
            username=username,
            name=name,
            row_num=0,
            source=source,
            # peer уже известен — резолвить по username не придётся
            peer=utils.get_input_peer(entity),
            key=key,
            kind=kind,
        ))

    return contacts
