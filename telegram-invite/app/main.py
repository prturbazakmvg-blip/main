# -*- coding: utf-8 -*-
"""Точка входа: консольный мастер."""
import argparse
import asyncio
import random
import sys
from pathlib import Path

from . import __version__, config as config_module, contacts as contacts_module
from . import drafts, progress, sheets, templates
from .paths import (
    CONTACTS_DIR,
    DATA_DIR,
    SESSION_PATH,
    TEMPLATES_DIR,
    ensure_dirs,
    migrate_from_legacy,
)
from .session import LoginFailed, build_client, ensure_authorized, logout
from .ui import (
    ask,
    ask_int,
    choose,
    confirm,
    error,
    info,
    ok,
    pause_exit,
    rule,
    setup_console,
    show_problems,
    title,
    warn,
    write,
)

DEFAULT_TEMPLATE = """{NAME}, привет. Как дела?

Хотел лично пригласить, мы 31.10-2.11 проводим agday.ru. Ежегодная конфа про рост
IT-компаний. 300 участников. Обсудим как найти новые добавочные ценности для
клиента. Препати, афтерпати, диджитал-баня и тур по офисам. В Екатеринбурге

В этом году планируешь?
"""


# --------------------------------------------------------------------------
# Подготовка
# --------------------------------------------------------------------------

def ensure_template() -> None:
    templates.ensure_default()


def setup_credentials(cfg: dict) -> dict:
    title('Первый запуск: нужны ключи Telegram')
    write('')
    write('  1. Откройте https://my.telegram.org и войдите по своему номеру.')
    write('  2. Раздел "API development tools" -> создайте приложение.')
    write('  3. Скопируйте оттуда App api_id и App api_hash.')
    write('')
    write('Ключи сохранятся в вашей пользовательской папке приложения.')
    write('Никому их не передавайте.')
    write('')

    while True:
        api_id = ask('api_id (только цифры)')
        if api_id.isdigit():
            break
        error('api_id — это число, например 1234567')

    while True:
        api_hash = ask('api_hash (32 символа)')
        if len(api_hash) >= 16:
            break
        error('Похоже на обрезанное значение, скопируйте целиком')

    cfg['api_id'] = api_id
    cfg['api_hash'] = api_hash

    write('')
    if confirm('Будете выгружать контакты из Google-таблицы?', default=True):
        cfg['google_file'] = ask('Ссылка на таблицу')

    config_module.save(cfg)
    ok('Настройки сохранены в {}'.format(DATA_DIR / 'credentials.json'))
    return cfg


# --------------------------------------------------------------------------
# Выгрузка из Google
# --------------------------------------------------------------------------

def action_refresh(cfg: dict) -> None:
    title('Обновление списков из Google-таблицы')

    url = cfg.get('google_file', '')
    if url:
        write('')
        write('Текущая ссылка: {}'.format(url[:80]))
        if not confirm('Использовать её?', default=True):
            url = ''

    if not url:
        url = ask('Ссылка на Google-таблицу')
        if not url:
            return
        cfg['google_file'] = url
        config_module.save(cfg)

    write('')
    info('Скачиваю...')
    try:
        created, grouped, skipped, explained = sheets.refresh(url, cfg['columns'])
    except sheets.SheetError as e:
        error(str(e))
        return

    write('')
    write('Какие колонки взяты:')
    labels = {'name': 'имя', 'telegram': 'telegram', 'group': 'ответственный', 'done': 'уже позвали'}
    for key in ('name', 'telegram', 'group', 'done'):
        index, header, how = explained[key]
        write('  {:<14} колонка {:>2} «{}» ({})'.format(labels[key] + ':', index + 1, header, how))
    write('')
    warn('Если колонки определились неверно — поправьте их в Настройках.')

    write('')
    ok('Готово. Файлы в папке {}'.format(CONTACTS_DIR))
    for path, group, count in created:
        write('  {:<36} {:>5} контакт(ов)   [{}]'.format(Path(path).name, count, group))

    total = sum(len(rows) for rows in grouped.values())
    write('')
    write('Годных контактов: {}'.format(total))
    write('Пропущено строк:  {}'.format(skipped.total))
    show_problems(skipped.as_lines(), limit=10)


# --------------------------------------------------------------------------
# Черновики
# --------------------------------------------------------------------------

def pick_contacts_file():
    files = contacts_module.available_files()
    if not files:
        error('Не нашёл ни одного csv. Сначала выгрузите контакты из таблицы (пункт 2)')
        return None

    write('')
    write('Какой список берём?')
    write('')
    labels = []
    for path in files:
        done = len(progress.load_done(path))
        try:
            items, _ = contacts_module.read(path)
            total = len(items)
        except RuntimeError:
            total = 0
        left = max(0, total - done)
        labels.append('{:<34} всего {:>4}, осталось {:>4}'.format(Path(path).name, total, left))

    index = choose('Номер списка', labels)
    return files[index] if index is not None else None


def pick_template():
    files = templates.available()
    if not files:
        ensure_template()
        files = templates.available()
    if len(files) == 1:
        return files[0]

    write('')
    write('Какой шаблон берём?')
    write('')
    labels = []
    for path in files:
        try:
            variants = templates.load_variants(path)
            labels.append('{:<34} вариантов текста: {}'.format(Path(path).name, len(variants)))
        except templates.TemplateError:
            labels.append('{:<34} (не читается)'.format(Path(path).name))

    index = choose('Номер шаблона', labels)
    return files[index] if index is not None else None


def prepare_run(cfg: dict, dry_run: bool):
    """Собирает всё нужное для прогона. Возвращает dict или None."""
    contacts_file = pick_contacts_file()
    if not contacts_file:
        return None

    template_file = pick_template()
    if not template_file:
        return None

    try:
        variants = templates.load_variants(template_file)
    except templates.TemplateError as e:
        error(str(e))
        return None

    broken = templates.missing_placeholders(variants)
    if broken:
        warn('В шаблоне нет NAME: {}. Имя подставляться не будет.'.format(', '.join(broken)))

    try:
        all_contacts, warnings = contacts_module.read(contacts_file)
    except RuntimeError as e:
        error(str(e))
        return None

    show_problems(warnings, limit=5)

    done = progress.load_done(contacts_file)
    pending = [c for c in all_contacts if c.key not in done]

    write('')
    write('Список:   {}'.format(Path(contacts_file).name))
    write('Шаблон:   {} ({} вариант(ов) текста)'.format(Path(template_file).name, len(variants)))
    write('Всего:    {}'.format(len(all_contacts)))
    write('Обработано ранее: {}'.format(len(done)))
    write('Осталось: {}'.format(len(pending)))

    if not pending:
        write('')
        ok('Этот список уже полностью обработан.')
        if confirm('Сбросить отметки и пройти его заново?', default=False):
            progress.reset_done(contacts_file)
            pending = all_contacts
        else:
            return None

    limit = config_module.daily_limit(cfg)
    today = progress.today_count()
    write('Сегодня уже создано черновиков: {} (мягкий лимит {})'.format(today, limit))

    suggested = max(1, min(len(pending), limit - today)) if not dry_run else min(len(pending), 5)
    write('')
    count = ask_int(
        'Сколько контактов обработать за этот заход',
        default=suggested,
        minimum=1,
        maximum=len(pending),
    )

    if not dry_run and today + count > limit:
        write('')
        warn('Получится {} черновиков за сегодня при мягком лимите {}.'.format(today + count, limit))
        warn('Чем больше однотипных обращений к незнакомым людям, тем выше шанс')
        warn('получить ограничение аккаунта. Лимит можно поменять в настройках.')
        if not confirm('Всё равно продолжить?', default=False):
            return None

    low, high = config_module.delay_range(cfg)
    batch = pending[:count]

    write('')
    rule()
    write('Пример текста для первого контакта (@{}):'.format(batch[0].username))
    rule()
    preview = templates.render(
        variants,
        {'NAME': batch[0].name, 'USERNAME': batch[0].username},
        random.Random(0),
    )
    write(preview)
    rule()
    write('')
    if not dry_run:
        estimate = (count - 1) * (low + high) // 2
        write('Пауза между контактами: {}-{} с. Примерное время: ~{} мин.'.format(
            low, high, max(1, estimate // 60)))
        write('')
        if not confirm('Создать {} черновик(ов)?'.format(count), default=True):
            return None

    return {
        'contacts_file': contacts_file,
        'batch': batch,
        'variants': variants,
        'delay': (low, high),
    }


def make_reporter(dry_run: bool):
    def on_event(kind, **data):
        if kind == 'start':
            contact = data['contact']
            write('')
            write('[{}/{}] @{} ({})'.format(
                data['index'], data['total'], contact.username, contact.name or 'без имени'))
            if dry_run:
                rule()
                write(data['text'])
                rule()
        elif kind == 'created':
            ok('черновик создан')
        elif kind == 'skipped':
            warn('пропуск — {}'.format(data['reason']))
        elif kind == 'failed':
            error('ошибка — {}'.format(data['error']))
        elif kind == 'pause':
            write('    пауза {} с (Ctrl+C — остановиться)'.format(data['seconds']))
        elif kind == 'reconnect':
            warn('связь пропала — подключаюсь заново ({} из {})...'.format(
                data['attempt'], data['attempts']))
        elif kind == 'reconnected':
            ok('связь восстановлена')
        elif kind == 'flood':
            warn('Telegram просит подождать {} с, жду...'.format(data['seconds']))
        elif kind == 'aborted':
            write('')
            error(data['reason'])

    return on_event


async def action_drafts(cfg: dict, client_holder: dict, dry_run: bool = False) -> None:
    title('Пробный прогон без Telegram' if dry_run else 'Создание черновиков')

    plan = prepare_run(cfg, dry_run)
    if plan is None:
        return

    client = None
    if not dry_run:
        client = await get_client(cfg, client_holder)
        if client is None:
            return

    reporter = make_reporter(dry_run)
    summary = None
    try:
        summary = await drafts.create_drafts(
            client=client,
            contacts=plan['batch'],
            variants=plan['variants'],
            contacts_file=plan['contacts_file'],
            delay_range=plan['delay'],
            on_event=reporter,
            dry_run=dry_run,
        )
    except drafts.RunAborted:
        write('')
        error('Прогон остановлен. Уже созданные черновики на месте.')
    except (KeyboardInterrupt, asyncio.CancelledError):
        write('')
        warn('Остановлено вручную. Прогресс сохранён, можно продолжить позже.')

    write('')
    rule()
    if dry_run:
        write('Проверено текстов: {}'.format(summary.created if summary else 0))
    elif summary is not None:
        write('Создано: {}   пропущено: {}   ошибок: {}'.format(
            summary.created, summary.skipped, summary.failed))
        write('Всего за сегодня: {}'.format(progress.today_count()))
        show_problems(summary.problems)
        write('')
        write('Черновики лежат в диалогах Telegram. Проверьте текст и отправьте руками.')
    rule()


# --------------------------------------------------------------------------
# Настройки
# --------------------------------------------------------------------------

def action_settings(cfg: dict) -> None:
    while True:
        low, high = config_module.delay_range(cfg)
        title('Настройки')
        write('')
        options = [
            'Пауза между контактами: {}-{} с'.format(low, high),
            'Мягкий дневной лимит: {}'.format(config_module.daily_limit(cfg)),
            'Ссылка на Google-таблицу: {}'.format((cfg.get('google_file') or 'не задана')[:50]),
            'Ключи Telegram (api_id / api_hash)',
            'Сбросить отметки «обработано» по списку',
            'Показать, где лежат файлы',
        ]
        index = choose('Что меняем', options)
        if index is None:
            return

        if index == 0:
            write('')
            write('Меньше 15 секунд — заметный риск словить FLOOD_WAIT.')
            new_low = ask_int('Минимальная пауза, с', default=low, minimum=5, maximum=3600)
            new_high = ask_int('Максимальная пауза, с', default=max(high, new_low + 1),
                               minimum=new_low + 1, maximum=7200)
            cfg['min_delay_seconds'] = new_low
            cfg['max_delay_seconds'] = new_high
            config_module.save(cfg)
            ok('Сохранено')

        elif index == 1:
            write('')
            write('Сколько черновиков в день считать безопасным потолком.')
            write('Для аккаунта без истории массовых рассылок разумно 20-30.')
            cfg['daily_limit'] = ask_int('Дневной лимит', default=config_module.daily_limit(cfg),
                                         minimum=1, maximum=500)
            config_module.save(cfg)
            ok('Сохранено')

        elif index == 2:
            cfg['google_file'] = ask('Ссылка на таблицу', default=cfg.get('google_file', ''))
            config_module.save(cfg)
            ok('Сохранено')

        elif index == 3:
            setup_credentials(cfg)
            warn('Ключи изменены. Перезапустите программу.')

        elif index == 4:
            path = pick_contacts_file()
            if path and confirm('Точно сбросить прогресс по {}?'.format(Path(path).name)):
                if progress.reset_done(path):
                    ok('Сброшено')
                else:
                    info('По этому списку отметок и не было')

        elif index == 5:
            write('')
            write('Рабочая папка:   {}'.format(DATA_DIR))
            write('Контакты:        {}'.format(CONTACTS_DIR))
            write('Шаблоны:         {} и template.txt рядом с программой'.format(TEMPLATES_DIR))
            write('Сессия и прогресс: {}'.format(SESSION_PATH.parent))


# --------------------------------------------------------------------------
# Меню
# --------------------------------------------------------------------------

async def get_client(cfg: dict, holder: dict):
    if holder.get('client') is not None:
        return holder['client']

    try:
        client = build_client(cfg)
    except LoginFailed as e:
        error(str(e))
        return None

    try:
        await ensure_authorized(client)
    except LoginFailed as e:
        error(str(e))
        try:
            await client.disconnect()
        except Exception:
            pass
        return None
    except (KeyboardInterrupt, asyncio.CancelledError):
        warn('Вход прерван')
        try:
            await client.disconnect()
        except Exception:
            pass
        return None

    holder['client'] = client
    return client


async def menu(cfg: dict) -> None:
    holder = {'client': None}

    try:
        while True:
            title('Черновики приглашений в Telegram  v{}'.format(__version__))
            write('')
            write('Программа только раскладывает черновики по диалогам.')
            write('Ничего не отправляется — отправляете вы сами, руками.')
            write('')

            options = [
                'Создать черновики',
                'Обновить списки контактов из Google-таблицы',
                'Проверить тексты, не подключаясь к Telegram',
                'Настройки',
                'Выйти из аккаунта Telegram',
            ]
            index = _menu_choice(options)
            if index is None:
                return

            if index == 0:
                await action_drafts(cfg, holder, dry_run=False)
            elif index == 1:
                action_refresh(cfg)
            elif index == 2:
                await action_drafts(cfg, holder, dry_run=True)
            elif index == 3:
                action_settings(cfg)
            elif index == 4:
                await action_logout(cfg, holder)

            write('')
            ask('Enter — вернуться в меню')
    finally:
        client = holder.get('client')
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass


def _menu_choice(options):
    for index, option in enumerate(options, start=1):
        write('  {:>2}) {}'.format(index, option))
    write('   0) выход')
    write('')
    while True:
        raw = ask('Номер пункта')
        if raw == '0':
            return None
        try:
            index = int(raw)
        except ValueError:
            error('Введите номер пункта')
            continue
        if 1 <= index <= len(options):
            return index - 1
        error('Введите номер пункта')


async def action_logout(cfg: dict, holder: dict) -> None:
    title('Выход из аккаунта')
    write('')
    write('После выхода при следующем запуске снова понадобится код из Telegram.')
    write('Частые повторные входы Telegram не любит — делайте это только если')
    write('нужно сменить аккаунт.')
    write('')
    if not confirm('Точно выйти?', default=False):
        return

    client = await get_client(cfg, holder)
    if client is None:
        return

    if await logout(client):
        holder['client'] = None
        ok('Вышли из аккаунта')
    else:
        error('Не получилось выйти')


# --------------------------------------------------------------------------
# Неинтерактивный режим (для тех, кто запускает из терминала)
# --------------------------------------------------------------------------

async def run_cli(cfg: dict, args) -> int:
    if args.download:
        url = args.sheet or cfg.get('google_file', '')
        if not url:
            error('Не задана ссылка на таблицу')
            return 1
        try:
            created, _grouped, skipped, _explained = sheets.refresh(url, cfg['columns'])
        except sheets.SheetError as e:
            error(str(e))
            return 1
        for path, group, count in created:
            write('{}  ({} контактов, {})'.format(path, count, group))
        write('Пропущено строк: {}'.format(skipped.total))
        show_problems(skipped.as_lines(), limit=10)
        return 0

    contacts_file = args.csv
    if not contacts_file:
        files = contacts_module.available_files()
        if len(files) != 1:
            error('Укажите --csv: списков найдено {}'.format(len(files)))
            return 1
        contacts_file = files[0]

    template_file = args.template
    if not template_file:
        ensure_template()
        available = templates.available()
        if not available:
            error('Не найден шаблон')
            return 1
        template_file = available[0]

    variants = templates.load_variants(template_file)
    all_contacts, warnings = contacts_module.read(contacts_file)
    show_problems(warnings, limit=5)

    done = progress.load_done(contacts_file)
    pending = [c for c in all_contacts if c.key not in done]
    batch = pending[:args.count]

    if not batch:
        info('Нечего обрабатывать')
        return 0

    holder = {'client': None}
    client = None
    if not args.dry_run:
        client = await get_client(cfg, holder)
        if client is None:
            return 1

    try:
        summary = await drafts.create_drafts(
            client=client,
            contacts=batch,
            variants=variants,
            contacts_file=contacts_file,
            delay_range=config_module.delay_range(cfg),
            on_event=make_reporter(args.dry_run),
            dry_run=args.dry_run,
        )
        write('')
        write('Создано: {}  пропущено: {}  ошибок: {}'.format(
            summary.created, summary.skipped, summary.failed))
        show_problems(summary.problems)
        return 0
    except drafts.RunAborted:
        return 1
    finally:
        if holder.get('client') is not None:
            await holder['client'].disconnect()


# --------------------------------------------------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Черновики приглашений в Telegram',
        epilog='Без аргументов запускается пошаговый мастер.',
    )
    parser.add_argument('--download', action='store_true', help='Выгрузить контакты из Google-таблицы и выйти')
    parser.add_argument('--sheet', help='Ссылка на таблицу (по умолчанию из credentials.json)')
    parser.add_argument('--csv', help='Файл со списком контактов')
    parser.add_argument('--template', help='Файл шаблона')
    parser.add_argument('--count', type=int, default=10, help='Сколько контактов обработать (по умолчанию 10)')
    parser.add_argument('--dry-run', action='store_true', help='Только показать тексты, не подключаясь к Telegram')
    parser.add_argument('--no-pause', action='store_true', help='Не ждать Enter перед закрытием окна')
    parser.add_argument('--version', action='version', version=__version__)
    return parser.parse_args(argv)


async def async_main(argv) -> int:
    args = parse_args(argv)
    interactive = not (args.download or args.csv or args.dry_run)

    ensure_dirs()
    for what in migrate_from_legacy():
        ok('Перенёс из прежней папки: {}'.format(what))
    ensure_template()

    try:
        cfg = config_module.load()
    except RuntimeError as e:
        error(str(e))
        return 1

    if config_module.migrate_legacy_session():
        ok('Перенёс вход из прежней версии — заново логиниться не нужно')

    if not config_module.is_complete(cfg):
        if not interactive:
            error('Не заполнены api_id/api_hash в credentials.json')
            return 1
        cfg = setup_credentials(cfg)

    if interactive:
        await menu(cfg)
        return 0

    return await run_cli(cfg, args)


def main(argv=None) -> None:
    setup_console()
    argv = sys.argv[1:] if argv is None else argv
    no_pause = '--no-pause' in argv

    code = 0
    try:
        code = asyncio.run(async_main(argv))
    except KeyboardInterrupt:
        write('')
        write('Прервано.')
        code = 130
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 0
    except Exception as e:
        write('')
        error('Непредвиденная ошибка: {}: {}'.format(type(e).__name__, e))
        import traceback
        write('')
        write(traceback.format_exc())
        write('Файл сессии не тронут — повторный вход не потребуется.')
        code = 1

    if no_pause:
        sys.exit(code)
    pause_exit(code)


if __name__ == '__main__':
    main()
