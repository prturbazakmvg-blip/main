# -*- coding: utf-8 -*-
"""Окно приложения (tkinter).

Экран построен по порядку работы: источник → текст → пробный прогон →
рассылка партиями. Всё на одной вкладке, чтобы не прыгать туда-сюда.

Устройство: tkinter живёт в главном потоке, asyncio с Telethon — в отдельном.
Общаются они только через очередь: фоновый поток кладёт в неё сообщения,
главный забирает их по таймеру. Прямых вызовов виджетов из фонового потока
нет, потому что tkinter этого не переживает.
"""
import asyncio
import json
import queue
import subprocess
import sys
import threading
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from . import __version__, config as config_module, contacts as contacts_module
from . import drafts, names, onboarding, openrouter, priority, progress
from . import reactions
from . import sheets, softeners, updates
from . import templates
from . import tgcontacts, tgfolders
from .paths import (DATA_DIR, LOG_PATH, SESSION_PATH, STATE_DIR, ensure_dirs,
                    migrate_from_legacy)
from .session import LoginCancelled, LoginFailed, build_client, ensure_authorized, logout

PAD = 12
MUTED = '#6e7781'

# Пары «что предложила программа — что отправили вы». Самый полезный
# материал для правил: разница между ними и есть то, чего коду не хватает.
EDITS_PATH = STATE_DIR / 'edits.jsonl'


# tk.Text — не ttk: цвета он у темы не берёт. На тёмной теме macOS текст
# получается почти того же цвета, что фон. Системные имена цветов
# («systemTextColor») Tk понимает, но перерисовывает их с задержкой — поле
# моргает и подтормаживает. Поэтому один раз смотрим, тёмная тема или
# светлая, и дальше держим обычные цвета.
DARK_TEXT = {'background': '#1f1f1f', 'foreground': '#f2f2f2',
             'insertbackground': '#f2f2f2', 'selectbackground': '#2f5c9e',
             'selectforeground': '#ffffff'}
LIGHT_TEXT = {'background': '#ffffff', 'foreground': '#101418',
              'insertbackground': '#101418', 'selectbackground': '#b7d5ff',
              'selectforeground': '#101418'}


def theme_colors(widget):
    """Тёмная тема или светлая — по фону окна."""
    try:
        red, green, blue = widget.winfo_rgb('systemWindowBackgroundColor')
    except tk.TclError:
        try:
            red, green, blue = widget.winfo_rgb(widget.cget('background'))
        except tk.TclError:
            return LIGHT_TEXT
    return DARK_TEXT if (red + green + blue) / 3 < 32768 else LIGHT_TEXT


def _reverse_date(text):
    """Ключ сортировки «свежие раньше» для строки вида 2026-08-14."""
    return tuple(-ord(ch) for ch in (text or ''))


def wheel_lines(event):
    """Сколько строк прокрутить. На macOS delta — уже строки."""
    step = getattr(event, 'delta', 0)
    if not step:
        return 0
    if sys.platform == 'darwin':
        return -int(step) or (-1 if step > 0 else 1)
    return -int(step / 120) or (-1 if step > 0 else 1)


def bind_wheel(widget):
    """Колесо внутри Notebook на macOS уходит не тому виджету — крутим сами.

    Когда виджет докручен до края, событие не перехватываем: дальше его
    поймает страница и прокрутится она. Иначе курсор над списком «залипает»
    и до нижних шагов не добраться.
    """
    def wheel(event):
        lines = wheel_lines(event)
        if not lines:
            return None
        try:
            first, last = widget.yview()
        except (tk.TclError, ValueError):
            first, last = 0.0, 1.0
        if (lines < 0 and first <= 0.0) or (lines > 0 and last >= 1.0):
            return None
        widget.yview_scroll(lines, 'units')
        # macOS откладывает перерисовку до следующего события: без этого текст
        # при прокрутке «заедает» и догоняет рывком
        widget.update_idletasks()
        return 'break'

    def button(direction):
        def handler(_event):
            widget.yview_scroll(direction * 3, 'units')
            widget.update_idletasks()
            return 'break'
        return handler

    widget.bind('<MouseWheel>', wheel, add='+')
    widget.bind('<Button-4>', button(-1), add='+')
    widget.bind('<Button-5>', button(1), add='+')


def scroll_area(parent):
    """Страница целиком в прокручиваемой области.

    На невысоком экране нижний шаг просто уезжал за край окна: grid обрезал
    то, что не поместилось, и до кнопок было не добраться. Теперь всё, что не
    влезло, доступно прокруткой.

    Возвращает (canvas, inner) — содержимое кладут в inner.
    """
    background = ttk.Style().lookup('TFrame', 'background') or '#ececec'
    canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0,
                       takefocus=0, background=background)
    bar = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
    canvas.configure(yscrollcommand=bar.set)
    canvas.pack(side='left', fill='both', expand=True)

    inner = ttk.Frame(canvas)
    window = canvas.create_window((0, 0), window=inner, anchor='nw')
    state = {'width': 0, 'height': 0, 'bar': False}

    def refresh(_event=None):
        width = canvas.winfo_width()
        wanted = inner.winfo_reqheight()
        # если содержимое короче окна — растягиваем на всю высоту, иначе шаги
        # прижимаются к верху и внизу остаётся пустая полоса
        height = max(wanted, canvas.winfo_height())
        if (width, height) != (state['width'], state['height']):
            state['width'], state['height'] = width, height
            canvas.itemconfigure(window, width=width, height=height)
        canvas.configure(scrollregion=(0, 0, width, height))
        need = wanted > canvas.winfo_height()
        if need != state['bar']:
            state['bar'] = need
            if need:
                bar.pack(side='right', fill='y')
            else:
                bar.pack_forget()

    canvas.bind('<Configure>', refresh)
    inner.bind('<Configure>', refresh)
    return canvas, inner


def paint_text(widget):
    """Красит tk.Text и вешает прокрутку колесом.

    Колесо приходится обрабатывать самим: внутри вкладок Notebook на macOS
    Tk отдаёт событие не тому виджету, и прокрутка «залипает».
    """
    for option, value in theme_colors(widget).items():
        try:
            widget.configure(**{option: value})
        except tk.TclError:
            pass

    bind_wheel(widget)


def _days_ago(when):
    """Сколько дней прошло. None — если дата неизвестна."""
    if when is None:
        return None
    try:
        now = datetime.now(when.tzinfo) if when.tzinfo else datetime.now()
        return max(0, (now - when).days)
    except (TypeError, ValueError):
        return None

# Метка сегмента. Красим не строку, а сам тег: ttk.Treeview умеет задавать
# цвет только всей строке, и на тёмной теме подложка съедает текст. Цветной
# квадратик в ячейке читается одинаково при любой теме.
SEGMENT_MARKS = {
    'ит и digital': '🟦',
    'клиент': '🟩',
    'подрядчик': '🟧',
    'коллеги': '🟪',
    'соискатель': '🟫',
    'близкие': '🟨',
    'спам': '🟥',
    'другое': '⬜',
    'можно': '🟩',
    'осторожно': '🟨',
    'нельзя': '🟥',
}


# --------------------------------------------------------------------------
# Фоновый поток с циклом asyncio
# --------------------------------------------------------------------------

class Tip(object):
    """Подсказка по наведению. Пояснения под каждым полем превращали окно
    в инструкцию — теперь они спрятаны и не мешают тем, кто уже разобрался."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.window = None
        widget.bind('<Enter>', self.show, add='+')
        widget.bind('<Leave>', self.hide, add='+')

    def show(self, _event=None):
        if self.window is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry('+{}+{}'.format(x, y))
        frame = tk.Frame(self.window, background='#333333')
        frame.pack()
        tk.Label(frame, text=self.text, justify='left', background='#333333',
                 foreground='white', padx=10, pady=7,
                 font=('Helvetica', 12)).pack()

    def hide(self, _event=None):
        if self.window is not None:
            self.window.destroy()
            self.window = None


class Backend(object):
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def shutdown(self):
        def stop():
            for task in asyncio.all_tasks(self.loop):
                task.cancel()
            self.loop.stop()

        self.loop.call_soon_threadsafe(stop)


class GuiPrompts(object):
    """Вопросы при входе в Telegram — модальными окошками."""

    def __init__(self, app):
        self.app = app

    async def ask(self, label):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.app.ask_dialog, label, False)

    async def ask_secret(self, label):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.app.ask_dialog, label, True)

    def notify(self, text):
        if text.strip():
            self.app.post('log', text=text)

    def failed(self, text):
        self.app.post('log', text=text, level='err')


# --------------------------------------------------------------------------

class App(object):
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.backend = Backend()
        self.client = None
        self.stop_flag = threading.Event()
        # счётчик, а не флаг: операций может идти несколько сразу (например,
        # восстановление входа и чтение таблицы), и первая закончившаяся
        # не должна разблокировать интерфейс
        self._busy_count = 0
        self.busy = False

        # что уже прочитано из источников
        self.sheet_groups = {}      # {название группы: [Contact]}
        self.tg_folders = []        # [{'title', 'peers', 'contacts', ...}]
        self._stoppable = False
        self._text_dirty = False
        self._save_after_id = None
        self._status_after_id = None
        self.excluded = set()      # кому не писать; заполнится из настроек
        self._counts_cache = None
        self._by_username = None
        self._drain_after_id = None

        ensure_dirs()
        self._migrated = migrate_from_legacy()
        templates.ensure_default()
        try:
            self.cfg = config_module.load()
        except RuntimeError as e:
            messagebox.showerror('Ошибка', str(e))
            self.cfg = dict(config_module.DEFAULTS)
        # снятые галочки живут между запусками
        self.excluded = set(self.cfg.get('excluded') or [])


        config_module.migrate_legacy_session()

        root.title('Черновики приглашений в Telegram {}'.format(__version__))
        # Окно не должно быть выше экрана: на 13" ноутбуке нижний шаг с
        # кнопками оказывался за краем, и до него было не добраться.
        root.geometry('{}x{}'.format(*self._fits(980, 980)))
        root.minsize(*self._fits(880, 560))

        self._build()
        self._drain_after_id = self.root.after(50, self._drain)
        self.load_messages()
        self.restore_selection()
        self.refresh_ui()

        for what in self._migrated:
            self.log('Перенёс из прежней папки: {}'.format(what), 'ok')

        # Откуда взялся ключ для «Определить типы» и «Дорабатывать под
        # каждого». Без этой строки непонятно, работает ли вшитый в сборку
        # ключ: «нет ключа» видно только когда уже нажал кнопку.
        if (self.cfg.get('openrouter_key') or '').strip():
            self.log('Ключ OpenRouter: свой, из «Настроек».', 'muted')
        elif config_module.bundled_key():
            self.log('Ключ OpenRouter: вшит в программу, вводить свой не нужно.',
                     'muted')
        else:
            self.log('Ключа OpenRouter нет — разбор контактов и доводка текста '
                     'работать не будут. Ключ вводится в «Настройках».', 'warn')

        if not config_module.is_complete(self.cfg):
            self.root.after(200, self.show_onboarding)
        elif SESSION_PATH.with_suffix('.session').exists():
            # Сессия уже может быть сохранена с прошлого раза — подключаемся
            # молча. Ход показываем в строке шага 1, а не в логе: панель лога
            # должна открываться, только когда есть что читать.
            self.status_var.set('Проверяю сохранённый вход…')
            self.set_busy(True)
            self.backend.submit(self._connect(silent=True))

        # Обновления смотрим молча в фоне: «нет сети» — не повод для окна.
        threading.Thread(target=self._look_for_update, daemon=True).start()

        if float(tk.TkVersion) < 9.0:
            self.log('Внимание: сборка с Tk {} — эмодзи в тексте могут '
                     'отображаться неверно.'.format(tk.TkVersion), 'warn')

    # ---------------------------------------------------------------- вёрстка

    def _fit_window(self, window, width, height):
        """Размер дочернего окна, который поместится на экране."""
        window.geometry('{}x{}'.format(*self._fits(width, height)))
        window.minsize(*self._fits(min(width, 560), min(height, 420)))

    def _fits(self, width, height):
        """Размер, который точно поместится на экране (с полем под меню и док)."""
        return (min(width, max(640, self.root.winfo_screenwidth() - 80)),
                min(height, max(480, self.root.winfo_screenheight() - 140)))

    def _build(self):
        style = ttk.Style()
        # Заголовки шагов должны читаться как заголовки, а не как подписи.
        # padding — отступ самой надписи: слева от рамки и снизу от содержимого.
        style.configure('Step.TLabelframe.Label', font=('', 14, 'bold'),
                        anchor='w', padding=(10, 0, 10, 10))
        style.configure('Step.TLabelframe', borderwidth=1)

        self._build_header(self.root)

        book = ttk.Notebook(self.root, padding=(PAD, 0, PAD, 0))
        book.pack(fill='both', expand=True)
        self.book = book

        page_send = ttk.Frame(book, padding=(0, PAD, 0, 0))
        page_contacts = ttk.Frame(book, padding=(0, PAD, 0, 0))
        page_groups = ttk.Frame(book, padding=(0, PAD, 0, 0))
        page_channels = ttk.Frame(book, padding=(0, PAD, 0, 0))
        book.add(page_send, text='   Рассылка   ')
        book.add(page_contacts, text='   Контакты   ')
        book.add(page_groups, text='   Группы   ')
        book.add(page_channels, text='   Каналы   ')

        main = ttk.Frame(page_send)
        main.pack(fill='both', expand=True)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=0, minsize=0)
        main.rowconfigure(0, weight=1)
        self.main = main

        # Прокручивается только колонка с шагами. Панель лога держим снаружи:
        # внутри прокрутки она растягивалась на всю высоту содержимого, нижняя
        # половина уезжала за край окна, и чтение лога превращалось в возню.
        column = ttk.Frame(main)
        column.grid(row=0, column=0, sticky='nsew')
        self.page_canvas, page_body = scroll_area(column)
        # колесо над свободным местом страницы: у ttk-рамок своих обработчиков
        # нет, событие доходит до окна, и здесь мы его и ловим
        self.root.bind('<MouseWheel>', self._page_wheel, add='+')

        steps = ttk.Frame(page_body)
        steps.pack(fill='both', expand=True)
        steps.columnconfigure(0, weight=1)
        steps.rowconfigure(1, weight=1, minsize=240)   # поле текста не сжимаем

        self.step_frames = {}
        self._build_source(steps, row=0)
        self._build_text(steps, row=1)
        self._build_run(steps, row=2)
        self._build_log(main)
        self.pages = {}
        self._build_contacts_page(page_contacts)
        self._build_groups_page(page_groups)
        self._build_channels_page(page_channels)
        self.contacts_store = tgcontacts.load_store()
        self.contacts_list = []
        self.root.after(100, self.load_contacts_from_store)

    def _page_wheel(self, event):
        """Прокрутка страницы «Рассылка». Виджеты со своей прокруткой событие
        забирают себе и сюда его не пускают — кроме тех случаев, когда они уже
        докручены до края."""
        canvas = getattr(self, 'page_canvas', None)
        if canvas is None or not canvas.winfo_ismapped():
            return None
        lines = wheel_lines(event)
        if lines:
            canvas.yview_scroll(lines, 'units')
            canvas.update_idletasks()
        return None

    def _step(self, parent, row, number, title, weighted=False):
        """Шаг с номером в заголовке. Заголовок потом показывает состояние."""
        frame = ttk.LabelFrame(parent, text='  {}. {}  '.format(number, title),
                               style='Step.TLabelframe', padding=(16, 10, 16, 10))
        frame.grid(row=row, column=0, sticky='nsew' if weighted else 'ew',
                   pady=(0, 12))
        self.step_frames[number] = {'frame': frame, 'title': title, 'number': number}
        return frame

    def _mark_step(self, number, done, summary=''):
        """Галочка и короткий итог прямо в заголовке шага."""
        info = self.step_frames.get(number)
        if not info:
            return
        mark = '  ✓' if done else ''
        text = '  {}. {}{}  '.format(number, info['title'], mark)
        if summary:
            text = '  {}. {}{} — {}  '.format(number, info['title'], mark, summary)
        info['frame'].configure(text=text)

    def _label(self, parent, text, row):
        """Одинаковая колонка подписей во всех шагах — иначе поля пляшут."""
        widget = ttk.Label(parent, text=text, width=13, anchor='w')
        widget.grid(row=row, column=0, sticky='w', pady=3)
        return widget

    def _hint(self, parent, text, row, columnspan=3):
        widget = ttk.Label(parent, text='ⓘ  как это работает', foreground=MUTED,
                           cursor='question_arrow')
        widget.grid(row=row, column=0, columnspan=columnspan, sticky='w', pady=(8, 0))
        Tip(widget, text)
        return widget

    # шапка -----------------------------------------------------------------

    def _build_header(self, parent):
        """Вход и настройки — над вкладками: это не шаг рассылки, а состояние
        программы, и нужно оно один раз."""
        bar = ttk.Frame(parent, padding=(PAD + 2, PAD, PAD + 2, PAD))
        bar.pack(fill='x')
        bar.columnconfigure(1, weight=1)

        self.account_var = tk.StringVar(value='Telegram: вход не выполнен')
        ttk.Label(bar, textvariable=self.account_var,
                  font=('', 13, 'bold')).grid(row=0, column=0, sticky='w')
        # Состояние — по центру верхней строки: и вход, и ход рассылки. Внизу
        # под ним была своя строка с полосой, а шаги должны влезать в экран.
        middle = ttk.Frame(bar)
        middle.grid(row=0, column=1, sticky='ew', padx=12)
        middle.columnconfigure(0, weight=1)
        center = ttk.Frame(middle)
        center.grid(row=0, column=0)           # без sticky — встаёт по центру

        self.status_var = tk.StringVar(value='')
        ttk.Label(center, textvariable=self.status_var,
                  foreground=MUTED).pack(side='left')
        self.progress = ttk.Progressbar(center, mode='determinate', length=170)
        self.progress_var = tk.StringVar(value='')
        self.progress_label = ttk.Label(center, textvariable=self.progress_var,
                                        foreground=MUTED)
        self.progress_label.pack(side='left', padx=(10, 0))

        buttons = ttk.Frame(bar)
        buttons.grid(row=0, column=2, sticky='e')
        self.login_button = ttk.Button(buttons, text='Войти', command=self.on_login)
        self.login_button.pack(side='left')
        ttk.Button(buttons, text='Настройки', command=self.open_settings).pack(
            side='left', padx=(8, 0))
        ttk.Separator(parent, orient='horizontal').pack(fill='x')

    # шаг 2 -----------------------------------------------------------------

    def _build_source(self, parent, row):
        frame = self._step(parent, row, 1, 'Кому пишем')
        frame.columnconfigure(1, weight=1)

        self.source_mode = tk.StringVar(value='sheet')
        modes = ttk.Frame(frame)
        modes.grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 8))
        ttk.Radiobutton(modes, text='Google-таблица', value='sheet',
                        variable=self.source_mode,
                        command=self.on_source_changed).pack(side='left')
        ttk.Radiobutton(modes, text='Папка чатов в Telegram', value='tgfolder',
                        variable=self.source_mode,
                        command=self.on_source_changed).pack(side='left', padx=24)
        ttk.Radiobutton(modes, text='Сегмент контактов', value='segment',
                        variable=self.source_mode,
                        command=self.on_source_changed).pack(side='left')

        self.sheet_row = ttk.Frame(frame)
        self.sheet_row.grid(row=1, column=0, columnspan=3, sticky='ew')
        self.sheet_row.columnconfigure(1, weight=1)

        self._label(self.sheet_row, 'Ссылка', 0)
        self.sheet_var = tk.StringVar(value=self.cfg.get('google_file', ''))
        self.sheet_entry = ttk.Entry(self.sheet_row, textvariable=self.sheet_var)
        self.sheet_entry.grid(row=0, column=1, sticky='ew', padx=8, pady=3)
        self.sheet_button = ttk.Button(self.sheet_row, text='Прочитать',
                                       command=self.on_read_sheet, width=16)
        self.sheet_button.grid(row=0, column=2, pady=3)

        self._label(self.sheet_row, 'Кто пишет', 1)
        self.group_box = ttk.Combobox(self.sheet_row, state='readonly')
        self.group_box.grid(row=1, column=1, sticky='ew', padx=8, pady=3)
        self.group_box.bind('<<ComboboxSelected>>', lambda _e: self.on_source_changed())
        self.sheet_hint = self._hint(
            self.sheet_row,
            'Таблица читается заново при каждом нажатии — на диск ничего не сохраняется.', 2)

        self.tg_row = ttk.Frame(frame)
        self.tg_row.grid(row=2, column=0, columnspan=3, sticky='ew')
        self.tg_row.columnconfigure(1, weight=1)

        self._label(self.tg_row, 'Папка', 0)
        self.tg_box = ttk.Combobox(self.tg_row, state='readonly')
        self.tg_box.grid(row=0, column=1, sticky='ew', padx=8, pady=3)
        self.tg_box.bind('<<ComboboxSelected>>',
                         lambda _e: (self.remember_selection(), self.on_pick_tg_folder()))
        self.tg_button = ttk.Button(self.tg_row, text='Загрузить папки',
                                    command=self.on_load_tg_folders, width=16)
        self.tg_button.grid(row=0, column=2, pady=3)
        self.tg_hint = self._hint(
            self.tg_row,
            'Эти чаты уже есть у вас в диалогах — искать по username не придётся.\n'
            'Черновик кладётся и в личку, и в группу, и в канал, где вы можете писать.', 1)

        self.segment_row = ttk.Frame(frame)
        self.segment_row.grid(row=3, column=0, columnspan=3, sticky='ew')
        self.segment_row.columnconfigure(1, weight=1)

        self._label(self.segment_row, 'Сегмент', 0)
        self.segment_box = ttk.Combobox(self.segment_row, state='readonly')
        self.segment_box.grid(row=0, column=1, sticky='ew', padx=8, pady=3)
        self.segment_box.bind('<<ComboboxSelected>>',
                              lambda _e: (self.remember_selection(),
                                          self.on_pick_segment()))
        self.segment_hint = self._hint(
            self.segment_row,
            'Кому писать — по типу контакта с вкладки «Контакты».\n'
            'Типы проставляются там же кнопкой «Определить типы (ИИ)» и правятся вручную.', 1)
        self.segment_contacts = None
        self.segment_label = ''

        # кому именно уйдут черновики — видно сразу, галочкой можно исключить
        self.who_box = ttk.Frame(frame)
        self.who_box.grid(row=4, column=0, columnspan=3, sticky='ew', pady=(10, 0))
        self.who_box.columnconfigure(0, weight=1)
        head = ttk.Frame(self.who_box)
        head.grid(row=0, column=0, sticky='ew')
        self.who_title = tk.StringVar(value='')
        self._who_tip = None
        ttk.Label(head, textvariable=self.who_title).pack(side='left')
        ttk.Button(head, text='Снять все', width=11,
                   command=lambda: self.pick_all(False)).pack(side='right')
        self.reset_button = ttk.Button(head, text='Забыть «уже писали»', width=21,
                                       command=self.on_reset_done)
        self.reset_button.pack(side='right', padx=(0, 16))
        ttk.Button(head, text='Выбрать все', width=13,
                   command=lambda: self.pick_all(True)).pack(side='right', padx=6)

        holder = ttk.Frame(self.who_box)
        holder.grid(row=1, column=0, sticky='ew', pady=(4, 0))
        holder.columnconfigure(0, weight=1)
        self.who_table = ttk.Treeview(
            holder, columns=('pick', 'rank', 'who', 'where', 'note', 'edit'),
            show='headings', height=6, selectmode='none')
        for key, title, width in (('pick', '✓', 34), ('rank', 'Шанс', 52),
                                  ('who', 'Кому', 230),
                                  ('where', 'Компания, должность', 210),
                                  ('note', 'Уже писали / тип', 170),
                                  ('edit', 'Текст', 52)):
            self.who_table.heading(key, text=title)
            self.who_table.column(key, width=width, anchor='w',
                                  stretch=(key == 'who'))
        scroll = ttk.Scrollbar(holder, command=self.who_table.yview)
        self.who_table.configure(yscrollcommand=scroll.set)
        self.who_table.grid(row=0, column=0, sticky='ew')
        scroll.grid(row=0, column=1, sticky='ns')
        self.who_table.bind('<Button-1>', self.on_who_click)
        Tip(self.who_table,
            'Клик по галочке слева — писать этому человеку или нет.\n'
            'Клик по имени или компании — открыть человека в Telegram.\n'
            'Клик по «✎» — посмотреть и поправить письмо этому человеку.\n'
            'Клик по «уже писали» переключает отметку в обе стороны:\n'
            'снять — чтобы написать заново, поставить — чтобы пропустить.\n\n'
            'Список отсортирован по «шансу»: свежесть переписки, её\n'
            'плотность, частота и неформальность. Сверху — те, кому\n'
            'приглашение уместнее всего.')
        self.who_rows = []

    # шаг 3 -----------------------------------------------------------------

    def _build_text(self, parent, row):
        frame = self._step(parent, row, 2, 'Что пишем', weighted=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky='ew')

        self.text_mode = tk.StringVar(value='template')
        ttk.Radiobutton(top, text='Свой текст', value='template', variable=self.text_mode,
                        command=self.refresh_ui).pack(side='left')
        self.column_radio = ttk.Radiobutton(
            top, text='Текст из колонки таблицы', value='column',
            variable=self.text_mode, command=self.refresh_ui)
        self.column_radio.pack(side='left', padx=24)



        self.personalize_var = tk.BooleanVar(
            value=bool(self.cfg.get('personalize', False)))
        self.personalize_check = ttk.Checkbutton(
            top, text='Дорабатывать под каждого',
            variable=self.personalize_var, command=self.on_toggle_personalize)
        self.personalize_check.pack(side='left', padx=(24, 0))

        # Подсказка — на той же строке, что переключатели: отдельной строкой
        # она съедала высоту, а шаги должны помещаться в один экран
        self.text_hint = ttk.Label(top, text='ⓘ  как это работает',
                                   foreground=MUTED, cursor='question_arrow')
        self.text_hint.pack(side='right')
        self._text_tip = Tip(self.text_hint, '')

        self.tabs = ttk.Notebook(frame)
        self.tabs.grid(row=2, column=0, sticky='nsew', pady=(8, 0))
        self.tabs.bind('<<NotebookTabChanged>>', self._on_tab_changed)
        self.editors = []          # [(frame, Text)] по порядку вкладок

        # Кнопки вкладок — под самими вкладками, иначе строка с переключателями
        # получается шире окна
        bottom = ttk.Frame(frame)
        bottom.grid(row=3, column=0, sticky='ew', pady=(8, 0))
        bottom.columnconfigure(0, weight=1)

        self.text_status = tk.StringVar(value='')
        ttk.Label(bottom, textvariable=self.text_status, foreground=MUTED).grid(
            row=0, column=0, sticky='w')

        self.tab_buttons = ttk.Frame(bottom)
        self.tab_buttons.grid(row=0, column=1, sticky='e')
        self.variant_button = ttk.Button(self.tab_buttons, text='Добавить вариант',
                                         command=self.on_add_variant)
        self.variant_button.pack(side='left')
        self.rename_button = ttk.Button(self.tab_buttons, text='Переименовать',
                                        command=self.on_rename_variant)
        self.rename_button.pack(side='left', padx=8)
        self.del_button = ttk.Button(self.tab_buttons, text='Удалить',
                                     command=self.on_delete_variant)
        self.del_button.pack(side='left')

    # шаг 4 -----------------------------------------------------------------

    def _build_run(self, parent, row):
        frame = self._step(parent, row, 3, 'Проверить и разослать')
        frame.columnconfigure(0, weight=1)

        # Весь шаг — одна строка: количество слева, кнопки справа. Полоса хода
        # и рамка «Действие» уехали в шапку и в высоту больше не растут.
        line = ttk.Frame(frame)
        line.grid(row=0, column=0, sticky='ew')
        line.columnconfigure(1, weight=1)

        count = ttk.Frame(line)
        count.grid(row=0, column=0, sticky='w')
        ttk.Label(count, text='По сколько за раз').pack(side='left')
        self.count_var = tk.StringVar(value=str(self.cfg.get('batch_size', 10) or 10))
        ttk.Spinbox(count, from_=1, to=500, width=6,
                    textvariable=self.count_var).pack(side='left', padx=8)

        self.run_hint = ttk.Label(line, text='ⓘ  что произойдёт',
                                  foreground=MUTED, cursor='question_arrow')
        self.run_hint.grid(row=0, column=1, sticky='w', padx=16)

        Tip(self.run_hint,
            'Пробный прогон только показывает тексты: к Telegram не обращается\n'
            'и никого не помечает.\n\n'
            'Программа ничего не отправляет — она кладёт черновик в поле ввода,\n'
            'отправляете вы руками.')

        actions = ttk.Frame(line)
        actions.grid(row=0, column=2, sticky='e')

        self.preview_button = ttk.Button(actions, text='Пробный прогон',
                                         command=self.on_preview, width=15)
        self.preview_button.pack(side='left', padx=(0, 8))
        hotkey = '⌘⏎' if sys.platform == 'darwin' else 'Ctrl+⏎'
        self.create_button = ttk.Button(actions,
                                        text='Создать черновики {}'.format(hotkey),
                                        command=self.on_create, width=21)
        self.create_button.pack(side='left')
        self.stop_button = ttk.Button(actions, text='Стоп', command=self.on_stop,
                                      state='disabled', width=6)
        self.stop_button.pack(side='left', padx=(8, 0))

        # Только с модификатором: на простой Enter черновики создавались прямо
        # во время правки текста — перевод строки запускал рассылку.
        self.root.bind('<Command-Return>', self._on_return)
        self.root.bind('<Control-Return>', self._on_return)

    def _on_return(self, _event=None):
        if str(self.create_button['state']) == 'normal':
            self.on_create()
        return 'break'

    def _build_log(self, parent):
        frame = ttk.LabelFrame(parent, text='  Что происходит  ',
                               style='Step.TLabelframe', padding=8)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        self.log_frame = frame
        self.log_visible = False

        ttk.Button(frame, text='Скрыть', width=8, command=self.hide_log).grid(
            row=0, column=0, columnspan=2, sticky='e', pady=(0, 6))

        self.log_widget = tk.Text(frame, width=42, wrap='word', relief='flat',
                                  font=('Menlo', 11), highlightthickness=0)
        paint_text(self.log_widget)
        bar = ttk.Scrollbar(frame, command=self.log_widget.yview)
        self.log_widget.configure(yscrollcommand=bar.set, state='disabled')
        self.log_widget.grid(row=1, column=0, sticky='nsew')
        bar.grid(row=1, column=1, sticky='ns')

        self.log_widget.tag_configure('ok', foreground='#1a7f37')
        self.log_widget.tag_configure('warn', foreground='#9a6700')
        self.log_widget.tag_configure('err', foreground='#c0392b')
        self.log_widget.tag_configure('muted', foreground=MUTED)
        self.log_widget.tag_configure('head', font=('Menlo', 11, 'bold'))

    def show_log(self):
        """Панель появляется только когда есть что показать."""
        if self.log_visible:
            return
        self.log_visible = True
        self.log_frame.grid(row=0, column=1, sticky='nsew', padx=(PAD, 0))
        self.main.columnconfigure(1, weight=0, minsize=360)
        # окно расширяем, чтобы панель не съела место у шагов, но не за край
        # экрана: иначе нижний шаг с кнопками оказывается под краем стола
        self.root.update_idletasks()
        if self.root.winfo_width() < 1240:
            self.root.geometry('{}x{}'.format(
                *self._fits(1320, max(980, self.root.winfo_height()))))

    def hide_log(self):
        self.log_visible = False
        self.log_frame.grid_remove()
        self.main.columnconfigure(1, weight=0, minsize=0)

    # вкладки со списками чатов -------------------------------------------

    # какие колонки показывать для каждого вида
    TABLE_COLUMNS = {
        tgcontacts.PERSON: [
            ('pick', '', 34), ('title', 'Имя', 180), ('username', 'Username', 120),
            ('kind', 'Тип', 150), ('company', 'Компания', 150),
            ('role', 'Должность', 120), ('extra', 'Обращение', 130),
            ('about', 'О себе', 190), ('why', 'Почему так', 200)],
        tgcontacts.GROUP: [
            ('pick', '', 34), ('title', 'Группа', 240), ('username', 'Username', 140),
            ('kind', 'Уместность', 140), ('extra', 'Тема, тон', 190),
            ('about', 'Описание', 260), ('why', 'Почему так', 220)],
        tgcontacts.CHANNEL: [
            ('pick', '', 34), ('title', 'Канал', 320), ('username', 'Username', 180),
            ('about', 'Описание', 320), ('date', 'Последнее', 120)],
    }

    def _build_list_page(self, parent, kind, with_ai, hint):
        """Одинаковый каркас для контактов, групп и каналов."""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)
        page = self.pages[kind] = {'kind': kind, 'sort': ('title', False)}

        bar = ttk.Frame(parent)
        bar.grid(row=0, column=0, sticky='ew')
        page['download'] = ttk.Button(bar, text='Обновить из Telegram',
                                      command=self.on_download_contacts)
        page['download'].pack(side='left')
        if with_ai:
            page['classify'] = ttk.Button(
                bar, text='Определить типы (ИИ)',
                command=lambda k=kind: self.on_classify(k))
            page['classify'].pack(side='left', padx=8)
            page['warm'] = ttk.Button(
                bar, text='Измерить теплоту',
                command=self.on_measure_warmth)
            page['warm'].pack(side='left', padx=(0, 8))
            Tip(page['warm'],
                'Читает переписку и считает, насколько плотно и неформально\n'
                'вы общались. По этому список адресатов сортируется: кому\n'
                'приглашение уместнее, тот выше. Модель не участвует —\n'
                'только чтение диалогов, денег не стоит.')
            page['stop'] = ttk.Button(bar, text='Стоп', width=8,
                                      command=self.on_stop, state='disabled')
            page['stop'].pack(side='left')
        ttk.Button(bar, text='Выгрузить в CSV',
                   command=lambda k=kind: self.on_export(k)).pack(side='right')

        filters = ttk.Frame(parent)
        filters.grid(row=1, column=0, sticky='ew', pady=(10, 4))
        if with_ai:
            ttk.Label(filters, text='Показывать').pack(side='left')
            values = (['все'] + list(openrouter.KINDS) + ['без типа']
                      if kind == tgcontacts.PERSON
                      else ['все'] + list(openrouter.FITS) + ['без типа'])
            page['filter'] = ttk.Combobox(filters, state='readonly', width=16,
                                          values=values)
            page['filter'].current(0)
            page['filter'].pack(side='left', padx=8)
            page['filter'].bind('<<ComboboxSelected>>',
                                lambda _e, k=kind: self.fill_table(k))
        page['summary'] = tk.StringVar(value='')
        ttk.Label(filters, textvariable=page['summary'],
                  foreground=MUTED).pack(side='left', padx=8)

        mark = ttk.Label(parent, text='ⓘ  как это работает', foreground=MUTED,
                         cursor='question_arrow')
        mark.grid(row=2, column=0, sticky='w', pady=(0, 10))
        Tip(mark, hint)

        holder = ttk.Frame(parent)
        holder.grid(row=3, column=0, sticky='nsew')
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        columns = self.TABLE_COLUMNS[kind]
        table = ttk.Treeview(holder, columns=[c[0] for c in columns],
                             show='headings', selectmode='browse')
        page['table'] = table
        page['titles'] = {key: title for key, title, _w in columns}
        for key, title, width in columns:
            if key == 'pick':
                table.heading(key, text=title)
            else:
                table.heading(key, text=title,
                              command=lambda k=kind, c=key: self.sort_table(k, c))
            table.column(key, width=width, anchor='w',
                         stretch=(key not in ('pick', 'kind')))
        scroll = ttk.Scrollbar(holder, command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        table.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')

        table.bind('<Button-1>', lambda e, k=kind: self.on_table_click(e, k))
        table.bind('<Double-1>', lambda _e, k=kind: self.open_chat(k))
        # правой кнопкой (и Ctrl+клик на маке) — сменить сегмент вручную
        table.bind('<Button-3>', lambda e, k=kind: self.segment_menu(e, k))
        table.bind('<Control-Button-1>', lambda e, k=kind: self.segment_menu(e, k))
        page['rows'] = []

    def _build_contacts_page(self, parent):
        self.pages = getattr(self, 'pages', {})
        self._build_list_page(
            parent, tgcontacts.PERSON, True,
            'Двойной клик открывает чат в Telegram, правая кнопка — сменить тип '
            'вручную. Тип определяется по переписке и описанию профиля;\n'
            'уже разобранные повторно не отправляются. Удалённые, заблокированные '
            'и пустые чаты в список не попадают.')

    def _build_groups_page(self, parent):
        self._build_list_page(
            parent, tgcontacts.GROUP, True,
            'Для групп оценивается, уместно ли там объявление: тематика и '
            'неформальность общения.\nДвойной клик открывает чат, правая кнопка — '
            'сменить оценку вручную.')

    def _build_channels_page(self, parent):
        self._build_list_page(
            parent, tgcontacts.CHANNEL, False,
            'Подписки и вещательные каналы. Они не участвуют в разборе типов '
            'и в рассылке — список нужен, чтобы видеть, что в аккаунте есть.')

    # --- общие операции над таблицами

    def items_of(self, kind):
        return [c for c in self.contacts_list if c.get('kind') == kind]

    def on_table_click(self, event, kind):
        """Первая колонка — галочка «писать / не писать»."""
        table = self.pages[kind]['table']
        if table.identify_region(event.x, event.y) != 'cell':
            return
        if table.identify_column(event.x) != '#1':
            return
        row = table.identify_row(event.y)
        if not row:
            return
        index = table.index(row)
        rows = self.pages[kind]['rows']
        if index >= len(rows):
            return
        key = tgcontacts.item_key(rows[index])
        self.set_excluded(key, key not in self.excluded)
        self.fill_table(kind)
        self.fill_who()
        self.refresh_ui()
        return 'break'

    def sort_table(self, kind, column):
        page = self.pages[kind]
        current, reverse = page['sort']
        page['sort'] = (column, not reverse if column == current else False)
        self.fill_table(kind)

    def _sort_value(self, item, column):
        if column == 'kind':
            return item.get('type', '') or 'яяя'
        if column == 'username':
            return (item.get('username', '') or 'яяя').lower()
        if column == 'extra':
            return (item.get('address', '') or item.get('topic', '') or '').lower()
        if column in ('company', 'role'):
            return (item.get(column, '') or 'яяя').lower()
        return (item.get(column, '') or '').lower()

    def fill_table(self, kind):
        page = self.pages.get(kind)
        if not page:
            return
        table = page['table']
        column, reverse = page['sort']
        for key, title in page['titles'].items():
            mark = ('  ↓' if reverse else '  ↑') if key == column else ''
            table.heading(key, text=title + mark)

        items = sorted(self.items_of(kind),
                       key=lambda c: self._sort_value(c, column), reverse=reverse)

        wanted = page['filter'].get() if page.get('filter') else 'все'
        table.delete(*table.get_children())
        page['rows'] = []
        for item in items:
            value = item.get('type', '')
            if wanted == 'без типа' and value:
                continue
            if wanted not in ('все', 'без типа') and value != wanted:
                continue

            # тёзок без username иначе не отличить: «без имени» таких сотни
            handle = ('@' + item['username'] if item.get('username')
                      else 'id {}'.format(item.get('id', '')))
            if kind == tgcontacts.PERSON:
                # как начнётся сообщение: «Иван · ты»
                extra = ' · '.join(part for part in
                                   (tgcontacts.greeting_name(item),
                                    item.get('address', '')) if part)
            elif kind == tgcontacts.GROUP:
                extra = ' · '.join(x for x in (item.get('topic', ''),
                                               item.get('tone', '')) if x)
            else:
                extra = None

            mark = '☐' if tgcontacts.item_key(item) in self.excluded else '☑'
            if kind == tgcontacts.CHANNEL:
                values = (mark, item.get('title', ''), handle,
                          item.get('about', ''), item.get('date', ''))
            elif kind == tgcontacts.PERSON:
                tag = '{} {}'.format(SEGMENT_MARKS.get(value, '·'), value) \
                    if value else '—'
                values = (mark, item.get('title', ''), handle, tag,
                          item.get('company', ''), item.get('role', ''), extra,
                          item.get('about', ''), item.get('why', ''))
            else:
                tag = '{} {}'.format(SEGMENT_MARKS.get(value, '·'), value) \
                    if value else '—'
                values = (mark, item.get('title', ''), handle, tag, extra,
                          item.get('about', ''), item.get('why', ''))
            table.insert('', 'end', values=values)
            page['rows'].append(item)

        total = len(items)
        typed = sum(1 for c in items if c.get('type'))
        by_kind = {}
        for c in items:
            if c.get('type'):
                by_kind[c['type']] = by_kind.get(c['type'], 0) + 1
        details = ', '.join('{} {}'.format(k, v) for k, v in sorted(by_kind.items()))
        if kind == tgcontacts.CHANNEL:
            page['summary'].set('всего {}'.format(total))
        else:
            page['summary'].set('всего {}, разобрано {}{}   ·   показано {}'.format(
                total, typed, ' ({})'.format(details) if details else '',
                len(page['rows'])))

    def fill_all_tables(self):
        for kind in self.pages:
            self.fill_table(kind)

    def segment_menu(self, event, kind):
        """Тип можно поправить руками: модель ошибается, человек знает точнее."""
        page = self.pages[kind]
        table = page['table']
        row = table.identify_row(event.y)
        if not row:
            return
        table.selection_set(row)
        index = table.index(row)
        if index >= len(page['rows']):
            return
        item = page['rows'][index]

        values = openrouter.KINDS if kind == tgcontacts.PERSON else openrouter.FITS
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=item.get('title', '')[:40], state='disabled')
        menu.add_separator()
        for value in values:
            mark = SEGMENT_MARKS.get(value, '·')
            menu.add_command(
                label='{} {} {}'.format('✓' if item.get('type') == value else '  ',
                                        mark, value),
                command=lambda v=value, i=item, k=kind: self.set_segment(i, v, k))
        menu.add_separator()
        menu.add_command(label='убрать тип',
                         command=lambda i=item, k=kind: self.set_segment(i, '', k))
        menu.add_command(label='открыть чат',
                         command=lambda k=kind: self.open_chat(k))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def manual_examples(self, kind=tgcontacts.PERSON):
        """Что владелец разметил руками — эталон для модели.

        Свежие правки важнее старых, поэтому берём с конца списка.
        """
        pairs = []
        for item in self.contacts_list:
            if item.get('kind') != kind or not item.get('type'):
                continue
            if item.get('by') == 'человек':
                pairs.append((item.get('title', ''), item['type']))
        return pairs[-12:]

    def set_segment(self, item, value, kind):
        # что отвечала модель — пригодится, чтобы понять, на чём она врёт
        if item.get('type') and item.get('by') != 'человек':
            item['model_type'] = item['type']
            item['model_why'] = item.get('why', '')
        item['by'] = 'человек' if value else ''
        item['type'] = value
        item['why'] = 'выставлено вручную' if value else ''
        self.contacts_store[item['id']] = item
        tgcontacts.save_store(self.contacts_store)
        self.fill_table(kind)

    def open_chat(self, kind):
        page = self.pages[kind]
        selection = page['table'].selection()
        if not selection:
            return
        index = page['table'].index(selection[0])
        if index >= len(page['rows']):
            return
        link = tgcontacts.tg_link(page['rows'][index])
        if not link:
            messagebox.showinfo('Нет ссылки', 'У этого чата нет ни username, ни id.')
            return
        subprocess.Popen(['open', link])

    def on_export(self, kind):
        items = self.items_of(kind)
        if not items:
            messagebox.showinfo('Пусто', 'Сначала обновите список из Telegram.')
            return
        import csv as csv_module
        names = {tgcontacts.PERSON: 'contacts', tgcontacts.GROUP: 'groups',
                 tgcontacts.CHANNEL: 'channels'}
        path = DATA_DIR / '{}.csv'.format(names[kind])
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv_module.writer(f)
            writer.writerow(['username', 'title', 'type', 'company', 'role',
                             'address', 'topic', 'tone', 'about', 'why', 'link'])
            for c in items:
                writer.writerow([c.get('username', ''), c.get('title', ''),
                                 c.get('type', ''), c.get('company', ''),
                                 c.get('role', ''), c.get('address', ''),
                                 c.get('topic', ''), c.get('tone', ''),
                                 c.get('about', ''), c.get('why', ''),
                                 tgcontacts.tg_link(c)])
        self.log('Выгружено в {}'.format(path), 'ok')
        subprocess.Popen(['open', '-R', str(path)])

    def load_contacts_from_store(self):
        saved = [v for v in self.contacts_store.values() if isinstance(v, dict)]
        if saved:
            self.contacts_list = sorted(saved, key=lambda c: c.get('title', ''))
            self.fill_all_tables()
            if self.source_mode.get() == 'segment':
                self._fill_segments()
                self.refresh_ui()
                self._autoload_segment()

    def on_download_contacts(self):
        if self.client is None:
            messagebox.showinfo('Нужен вход', 'Сначала войдите в Telegram.')
            return
        self.stop_flag.clear()
        self.set_busy(True, stoppable=True)
        self.log('Читаю список диалогов...', 'muted')
        self.backend.submit(self._download_contacts())

    def on_classify(self, kind):
        if self.client is None:
            messagebox.showinfo('Нужен вход', 'Сначала войдите в Telegram.')
            return
        key = config_module.openrouter_key(self.cfg)
        if not key:
            messagebox.showinfo('Нужен ключ', 'Вставьте ключ OpenRouter в «Настройках».')
            return

        todo = [c for c in self.items_of(kind)
                if not c.get('type') and not c.get('no_data')]
        empty = sum(1 for c in self.items_of(kind)
                    if not c.get('type') and c.get('no_data'))
        if not todo:
            messagebox.showinfo(
                'Всё разобрано',
                'Здесь у всех уже проставлен тип.' if not empty else
                'Осталось {} чатов, где читать нечего — ни переписки, ни '
                'описания профиля. Тип для них можно проставить руками.'.format(empty))
            return
        if not messagebox.askyesno(
                'Определение типов',
                'Разобрать {} чатов?\n\n'
                'Для каждого читаются последние сообщения и описание профиля, '
                'затем они уходят в модель {}.\n'
                'Ориентировочно: {:.2f} $ и около {} мин.\n\n'
                'Прервать можно кнопкой «Стоп», сделанное сохранится.'.format(
                    len(todo),
                    config_module.openrouter_model(self.cfg),
                    len(todo) * 0.0001, max(1, len(todo) // 90))):
            return

        self.stop_flag.clear()
        self.set_busy(True, stoppable=True)
        self.backend.submit(self._classify_contacts(todo, key))

    # ------------------------------------------------------------- сообщения

    def post(self, kind, **data):
        """Вызывается из любого потока."""
        self.queue.put((kind, data))

    def _drain(self):
        try:
            while True:
                kind, data = self.queue.get_nowait()
                self._handle(kind, data)
        except queue.Empty:
            pass
        except tk.TclError:
            return                      # окно уже закрывают
        self._drain_after_id = self.root.after(50, self._drain)

    def _handle(self, kind, data):
        if kind == 'update':
            self.offer_update(data)
            return
        if kind == 'quit_for_update':
            self.save_messages()
            self.remember_selection()
            self.stop_flag.set()
            self.backend.shutdown()
            self.root.destroy()
            return
        if kind == 'draft':
            DraftWindow(self, data['contact'], data['known'],
                        data['made'], data['note'])
            return
        if kind == 'log':
            self.log(data['text'], data.get('level', ''))
        elif kind == 'status':
            if data.get('logged_in'):
                self.account_var.set('Telegram: {}'.format(data['text']))
                self.status_var.set('')
            else:
                self.account_var.set('Telegram: вход не выполнен')
                self.status_var.set(data['text'])
            self.login_button.configure(
                text='Выйти' if data.get('logged_in') else 'Войти')
            if data.get('logged_in'):
                # вход поднялся — можно достроить сегмент прошлого запуска
                self._autoload_segment()
        elif kind == 'progress':
            self.progress.configure(maximum=max(1, data['total']), value=data['done'])
            self.progress_var.set(data['text'])
        elif kind == 'busy':
            self.set_busy(data['value'])
        elif kind == 'sheet_groups':
            self.invalidate_counts()
            self._fill_groups()
            self.fill_who()
        elif kind == 'tg_folders':
            self._fill_tg_folders()
            self.fill_who()
        elif kind == 'contacts':
            self.fill_all_tables()
            if self.source_mode.get() == 'segment':
                self._fill_segments()
        elif kind == 'status_line':
            self.invalidate_counts()
            self.fill_who()
            self.refresh_ui()
        elif kind == 'dialog':
            self._show_dialog(data)
        elif kind == 'error':
            messagebox.showerror('Ошибка', data['text'])

    def _show_dialog(self, data):
        try:
            value = simpledialog.askstring(
                'Вход в Telegram', data['label'],
                show='*' if data['secret'] else None, parent=self.root)
        finally:
            data['box']['value'] = value
            data['event'].set()

    def ask_dialog(self, label, secret):
        """Вызывается из фонового потока, ждёт ответа из главного."""
        event = threading.Event()
        box = {}
        self.post('dialog', label=label, secret=secret, box=box, event=event)
        event.wait()
        return box.get('value') or ''

    def log(self, text, level=''):
        self.show_log()
        self.log_widget.configure(state='normal')
        self.log_widget.insert('end', text + '\n', level)
        self.log_widget.see('end')
        self.log_widget.configure(state='disabled')
        self._log_to_file(text, level)

    def _log_to_file(self, text, level=''):
        """Тот же лог на диск: после закрытия окна иначе не узнать, чем
        закончился долгий прогон."""
        if not text.strip():
            return
        try:
            with open(LOG_PATH, 'a', encoding='utf-8') as f:
                f.write('{} {}{}\n'.format(
                    datetime.now().strftime('%d.%m %H:%M:%S'),
                    '[{}] '.format(level) if level else '', text))
        except OSError:
            pass

    def set_busy(self, value, stoppable=False):
        if value:
            self._busy_count += 1
            if stoppable:
                self._stoppable = True
        else:
            self._busy_count = max(0, self._busy_count - 1)
            if self._busy_count == 0:
                self._stoppable = False
        self.busy = self._busy_count > 0
        # полоса в шапке — только пока идёт работа, иначе она просто занимает
        # место посреди строки состояния
        if self.busy:
            if not self.progress.winfo_ismapped():
                self.progress.pack(side='left', padx=(10, 0),
                                   before=self.progress_label)
        else:
            self.progress.pack_forget()
            self.progress_var.set('')
        self.refresh_ui()

    # ------------------------------------------------------------- источники

    def _set_enabled(self, container, enabled):
        state = 'normal' if enabled else 'disabled'
        for child in container.winfo_children():
            try:
                if isinstance(child, ttk.Combobox):
                    child.configure(state='readonly' if enabled else 'disabled')
                else:
                    child.configure(state=state)
            except tk.TclError:
                pass

    def refresh_ui(self):
        """Одно место, где решается что доступно и что показано в заголовках.

        Вызывается после любого изменения состояния — так шаги не разъезжаются.
        """
        sheet = self.source_mode.get() == 'sheet'
        busy = self.busy

        # шапка
        logged_in = self.client is not None
        self.login_button.configure(text='Выйти' if logged_in else 'Войти',
                                    state='disabled' if busy else 'normal')

        # шаг 1: ненужный источник просто прячем, а не глушим серым
        mode = self.source_mode.get()
        rows = {'sheet': self.sheet_row, 'tgfolder': self.tg_row,
                'segment': self.segment_row}
        for name, widget in rows.items():
            if name == mode:
                widget.grid()
                self._set_enabled(widget, not busy)
            else:
                widget.grid_remove()
        if mode == 'tgfolder' and not logged_in:
            self.tg_button.configure(state='disabled')

        items, label, total, left = self._counts()
        if items is None:
            self.who_box.grid_remove()
            self._mark_step(1, False)
        else:
            self._mark_step(1, bool(left), '{}: осталось {} из {}'.format(
                label, left, total))

        # шаг 2
        from_column = self.text_mode.get() == 'column'
        self.column_radio.configure(state='normal' if (sheet and not busy) else 'disabled')
        if from_column and not sheet:
            self.text_mode.set('template')
            from_column = False

        if from_column:
            self.tabs.grid_remove()
            self.tab_buttons.grid_remove()
        else:
            self.tabs.grid()
            self.tab_buttons.grid()
            for widget in (self.variant_button, self.rename_button, self.del_button):
                widget.configure(state='disabled' if busy else 'normal')
            for _holder, editor in self.editors:
                editor.configure(state='disabled' if busy else 'normal')

        variants = [] if from_column else self.editor_variants()
        if from_column:
            self._text_tip.text = (
                'Для каждого человека берётся его собственный текст из колонки\n'
                'таблицы. Строки с пустой колонкой будут пропущены.')
            self._mark_step(2, True, 'текст из таблицы')
        else:
            self._text_tip.text = (
                'Рассылается текст выбранной вкладки, остальные просто хранятся.\n'
                'Имя подставляется вместо {NAME}.\n\n'
                'Чтобы сообщения не были одинаковыми, пишите варианты прямо\n'
                'в тексте: {Мы тут проводим|Проводим} — программа выберет\n'
                'один случайно.')
            self._mark_step(2, bool(variants), 'вариантов: {}'.format(len(variants))
                            if variants else 'текст пустой')
        self._update_text_status()

        # шаг 4
        ready = items is not None and bool(items) and (from_column or bool(variants))
        for page in getattr(self, 'pages', {}).values():
            page['download'].configure(state='disabled' if busy else 'normal')
            if page.get('classify'):
                page['classify'].configure(state='disabled' if busy else 'normal')
            if page.get('stop'):
                page['stop'].configure(
                    state='normal' if (busy and self._stoppable) else 'disabled')

        self.preview_button.configure(state='normal' if (ready and not busy) else 'disabled')
        self.create_button.configure(
            state='normal' if (ready and logged_in and not busy) else 'disabled')
        self.stop_button.configure(state='normal' if (busy and self._stoppable) else 'disabled')

        today = progress.today_count()
        limit = config_module.daily_limit(self.cfg)
        if not logged_in:
            note = 'нужен вход в Telegram'
        elif items is None:
            note = 'выберите, кому пишем'
        elif not ready:
            note = 'напишите текст'
        else:
            note = 'сегодня создано {} из {}'.format(today, limit)
        self._mark_step(3, False, note)

    def on_source_changed(self):
        self.invalidate_counts()
        if self.source_mode.get() == 'segment':
            self._fill_segments()
        self.remember_selection()
        self.fill_who()
        self.refresh_ui()

    def _segment_counts(self):
        """Сколько людей в каждом сегменте. Считается один раз на изменение."""
        counts = {}
        for item in self.contacts_list:
            if item.get('kind') != tgcontacts.PERSON or not item.get('type'):
                continue
            counts[item['type']] = counts.get(item['type'], 0) + 1
        return counts

    def _fill_segments(self):
        counts = self._segment_counts()
        self._segments = [kind for kind in openrouter.KINDS if counts.get(kind)]
        values = ['{} {} — {}'.format(SEGMENT_MARKS.get(kind, '·'), kind, counts[kind])
                  for kind in self._segments]
        if self.segment_box['values'] == tuple(values):
            return
        self.segment_box.configure(values=values)
        if not values:
            self.segment_box.set('')
            self.segment_contacts = None
            return
        wanted = self.cfg.get('last_segment') or ''
        self.segment_box.current(self._segments.index(wanted)
                                 if wanted in self._segments else 0)

    def _autoload_segment(self):
        """Сегмент из прошлого запуска собираем сами.

        Раньше восстанавливалось только название в списке: таблица оставалась
        пустой, пока тот же сегмент не выберут заново руками.
        """
        if self.source_mode.get() != 'segment' or self.client is None:
            return
        if self.segment_contacts is not None or self.selected_segment() is None:
            return
        if self.busy:                       # вход или загрузка ещё идут
            self.root.after(400, self._autoload_segment)
            return
        self.on_pick_segment()

    def selected_segment(self):
        index = self.segment_box.current()
        segments = getattr(self, '_segments', [])
        if index < 0 or index >= len(segments):
            return None
        return segments[index]

    def on_pick_segment(self):
        segment = self.selected_segment()
        if segment is None:
            return
        if self.client is None:
            messagebox.showinfo('Нужен вход', 'Сначала войдите в Telegram.')
            return
        if self.segment_label == segment and self.segment_contacts is not None:
            self.invalidate_counts()
            self.refresh_ui()
            return
        items = [c for c in self.contacts_list
                 if c.get('kind') == tgcontacts.PERSON and c.get('type') == segment]
        self.set_busy(True)
        self.log('Собираю сегмент «{}»: {} чат(ов)...'.format(segment, len(items)), 'muted')
        self.backend.submit(self._load_segment(segment, items))

    async def _load_segment(self, segment, items):
        try:
            contacts, missed = await tgcontacts.contacts_for(self.client, items, segment)
        except Exception as e:
            self.post('error', text='{}: {}'.format(type(e).__name__, e))
            self.post('busy', value=False)
            return
        self.segment_contacts = contacts
        self.segment_label = segment
        self.post('log', text='Сегмент «{}»: {} адресат(ов){}'.format(
            segment, len(contacts),
            ', пропущено {}'.format(missed) if missed else ''), level='ok')
        self.post('status_line')
        self.post('busy', value=False)

    def remember_selection(self):
        """Что было выбрано — чтобы окно открылось там же, где закрылось."""
        cfg = self.cfg
        before = (cfg.get('last_source'), cfg.get('last_group'), cfg.get('last_folder'),
                  cfg.get('last_segment'), cfg.get('batch_size'))
        cfg['last_source'] = self.source_mode.get()
        try:
            cfg['batch_size'] = max(1, int(self.count_var.get()))
        except ValueError:
            pass
        if self.group_box.current() > 0 and getattr(self, '_group_titles', None):
            cfg['last_group'] = self._group_titles[self.group_box.current() - 1]
        elif self.group_box.current() == 0:
            cfg['last_group'] = ''
        folder = self.selected_tg_folder()
        if folder is not None:
            cfg['last_folder'] = folder['title']
        segment = self.selected_segment()
        if segment is not None:
            cfg['last_segment'] = segment
        after = (cfg.get('last_source'), cfg.get('last_group'), cfg.get('last_folder'),
                 cfg.get('last_segment'), cfg.get('batch_size'))
        if before != after:
            config_module.save(cfg)

    def restore_selection(self):
        mode = self.cfg.get('last_source') or 'sheet'
        if mode in ('sheet', 'tgfolder', 'segment'):
            self.source_mode.set(mode)

    def update_status(self):
        self.invalidate_counts()
        self.fill_who()
        self.refresh_ui()

    def on_read_sheet(self):
        url = self.sheet_var.get().strip()
        if not url:
            messagebox.showinfo('Нет ссылки', 'Вставьте ссылку на Google-таблицу.')
            return
        self.cfg['google_file'] = url
        config_module.save(self.cfg)
        self.set_busy(True)
        self.log('Читаю таблицу...', 'muted')
        self.backend.submit(self._read_sheet(url))

    def _fill_groups(self):
        titles = sorted(self.sheet_groups, key=lambda g: -len(self.sheet_groups[g]))
        labels = ['ВСЕ — {} чел.'.format(sum(len(v) for v in self.sheet_groups.values()))]
        labels += ['{} — {} чел.'.format(t, len(self.sheet_groups[t])) for t in titles]
        self._group_titles = titles
        self.group_box.configure(values=labels)
        wanted = self.cfg.get('last_group') or ''
        self.group_box.current(titles.index(wanted) + 1 if wanted in titles else 0)
        self.invalidate_counts()
        self.refresh_ui()

    def on_load_tg_folders(self):
        if self.client is None:
            messagebox.showinfo('Нужен вход',
                                'Папки чатов лежат в вашем аккаунте — сначала войдите в Telegram.')
            return
        self.set_busy(True)
        self.log('Читаю список папок...', 'muted')
        self.backend.submit(self._load_tg_folders())

    def _folder_label(self, folder):
        """Показываем состав: где люди, где группы, где каналы."""
        bits = []
        if folder.get('people'):
            bits.append('людей {}'.format(folder['people']))
        if folder.get('groups'):
            bits.append('групп {}'.format(folder['groups']))
        if folder.get('channels'):
            bits.append('каналов {}'.format(folder['channels']))
        return '{} — {} чат(ов): {}'.format(
            folder['title'], folder.get('total', 0), ', '.join(bits) or 'пусто')

    def _fill_tg_folders(self):
        self.tg_box.configure(values=[self._folder_label(f) for f in self.tg_folders])
        self.invalidate_counts()
        if self.tg_folders:
            wanted = self.cfg.get('last_folder') or ''
            titles = [f['title'] for f in self.tg_folders]
            self.tg_box.current(titles.index(wanted) if wanted in titles else 0)
            self.on_pick_tg_folder()
        else:
            self.refresh_ui()

    def on_pick_tg_folder(self):
        folder = self.selected_tg_folder()
        if folder is None:
            return
        if folder['contacts'] is None:
            # людей в папке читаем только когда её выбрали: у аккаунта могут
            # быть десятки папок, и тянуть имена сразу для всех очень долго
            self.set_busy(True)
            self.log('Читаю участников папки «{}»...'.format(folder['title']), 'muted')
            self.backend.submit(self._load_folder_contacts(folder))
        else:
            self.invalidate_counts()
            self.refresh_ui()

    def selected_tg_folder(self):
        index = self.tg_box.current()
        if index < 0 or index >= len(self.tg_folders):
            return None
        return self.tg_folders[index]

    def current_contacts(self):
        """Контакты выбранного источника или None."""
        if self.source_mode.get() == 'sheet':
            if not self.sheet_groups:
                return None, ''
            index = self.group_box.current()
            if index <= 0:
                items = []
                for title in getattr(self, '_group_titles', []):
                    items.extend(self.sheet_groups[title])
                return self.rank(items), 'вся таблица'
            title = self._group_titles[index - 1]
            return (self.rank(self.sheet_groups[title]),
                    'таблица · {}'.format(title))

        if self.source_mode.get() == 'segment':
            if self.segment_contacts is None:
                return None, ''
            return (self.rank(self.segment_contacts),
                    'сегмент «{}»'.format(self.segment_label))

        folder = self.selected_tg_folder()
        if folder is None or folder['contacts'] is None:
            return None, ''
        return (self.rank(folder['contacts']),
                'папка «{}»'.format(folder['title']))

    # ------------------------------------------------------------- галочки

    def set_excluded(self, key, excluded):
        if not key:
            return
        if excluded:
            self.excluded.add(key)
        else:
            self.excluded.discard(key)
        self.cfg['excluded'] = sorted(self.excluded)
        config_module.save(self.cfg)
        self.invalidate_counts()

    def known_of(self, contact):
        """Запись из хранилища для этого адресата. Пусто — не нашли."""
        index = self._by_username
        if index is None:
            index = self._by_username = {
                value['username'].lower(): value
                for value in self.contacts_store.values()
                if isinstance(value, dict) and value.get('username')}
        known = index.get((contact.username or '').lower())
        if known is None and contact.key.startswith('id'):
            known = self.contacts_store.get(contact.key[2:])
        return known or {}

    def rank(self, items):
        """Сначала те, кого уместнее всего позвать.

        Порядок важнее, чем кажется: список на полторы тысячи человек
        уходит партиями, и разослать сперва тёплым и свежим — не то же
        самое, что пройти его в алфавитном порядке.
        """
        scored = []
        for contact in items:
            known = self.known_of(contact)
            points = priority.score(known)
            # дата — вторым ключом: пока теплота не измерена, баллы у
            # многих совпадают, и без неё порядок был бы алфавитным
            scored.append((-points, known.get('date') or '',
                           (known.get('title') or contact.label).lower(),
                           contact))
        scored.sort(key=lambda row: (row[0], _reverse_date(row[1]), row[2]))
        return [row[3] for row in scored]

    def fill_who(self):
        """Список адресатов выбранного источника — с галочками."""
        items, label = self.current_contacts()
        if items is None:
            self.who_box.grid_remove()
            self.who_rows = []
            return
        self.who_box.grid()

        done_cache = {}
        rows = []
        for contact in items:
            source = contact.source
            if source not in done_cache:
                done_cache[source] = progress.load_done(source)
            rows.append((contact, contact.key in done_cache[source]))

        self.who_table.delete(*self.who_table.get_children())
        self.who_rows = []
        picked = 0
        by_username = {}
        for value in self.contacts_store.values():
            if value.get('username'):
                by_username[value['username'].lower()] = value

        for contact, done in rows:
            off = contact.key in self.excluded
            picked += 0 if (off or done) else 1

            known = by_username.get((contact.username or '').lower())
            if known is None and contact.key.startswith('id'):
                known = self.contacts_store.get(contact.key[2:])
            known = known or {}

            # полное имя из диалога: там фамилия, в contact.name — только имя
            who = known.get('title') or contact.name or contact.label
            if contact.username:
                who = '{}  @{}'.format(who, contact.username)
            where = '  ·  '.join(part for part in (known.get('company', ''),
                                                   known.get('role', '')) if part)
            note = 'уже писали' if done else (known.get('type') or '')

            points = priority.score(known)
            mark = '✎ есть' if (known.get('draft') or '').strip() else '✎'
            self.who_table.insert('', 'end', values=(
                '☐' if off else '☑', priority.label(points), who.strip(),
                where, note, mark))
            self.who_rows.append(contact)
        self.who_title.set('{} · получателей {}, из них новых {}'.format(
            label, len(rows), picked))

    def on_who_click(self, event):
        if self.who_table.identify_region(event.x, event.y) != 'cell':
            return
        row = self.who_table.identify_row(event.y)
        if not row:
            return
        index = self.who_table.index(row)
        if index >= len(self.who_rows):
            return
        contact = self.who_rows[index]

        column = self.who_table.identify_column(event.x)
        if column == '#6':
            self.show_draft(contact)
            return
        if column in ('#3', '#4'):
            # по имени и по компании открываем человека в Telegram: снимать
            # галочку кликом по всей строке было слишком легко случайно
            self.open_contact(contact)
            return
        if column == '#5':
            # клик по «уже писали» переключает отметку в обе стороны
            if contact.key in progress.load_done(contact.source):
                progress.unmark_done(contact.source, contact.key)
                self.log('{} — снова в очереди'.format(contact.label), 'muted')
            else:
                progress.mark_done(contact.source, contact.key)
                self.log('{} — помечен как обработанный'.format(contact.label),
                         'muted')
        else:
            self.set_excluded(contact.key, contact.key not in self.excluded)
        self.invalidate_counts()
        self.fill_who()
        self.refresh_ui()

    def show_draft(self, contact):
        """Показать письмо этому человеку и дать его поправить.

        Правка сохраняется и уходит в черновик вместо собранного текста.
        Заодно пишем, что программа предложила и что вы из этого сделали:
        по этим парам и видно, как надо писать, — сравнение собранных и
        отправленных писем оказалось самым говорящим источником правил.
        """
        variants = self.editor_variants()
        if not variants:
            messagebox.showinfo('Нет текста', 'Сначала напишите текст письма.')
            return
        known = self.known_of(contact)
        if self.client is None or not known.get('id'):
            # без входа переписку не прочитать — показываем что есть
            made, note = self.draft_for(contact, known, variants[0], '')
            DraftWindow(self, contact, known, made, note)
            return
        self.set_busy(True)
        self.backend.submit(self._prepare_draft(contact, known, variants[0]))

    async def _prepare_draft(self, contact, known, base_text):
        """Собрать письмо ровно так, как его соберёт рассылка.

        Переписку читаем: без неё не будет ни памяти о прошлом ответе, ни
        извинения за повтор — и в окне оказался бы не тот текст, который
        уйдёт в черновик.
        """
        rows = await tgcontacts.history(self.client, known['id'], limit=50)
        history = '' if rows is None else tgcontacts.as_transcript(rows)
        made, note = self.draft_for(contact, known, base_text, history)
        if rows is None:
            note = '{}; переписку прочитать не вышло'.format(note)
        self.post('draft', contact=contact, known=known, made=made, note=note)
        self.post('busy', value=False)

    def draft_for(self, contact, known, base_text, history=''):
        """Что уйдёт этому человеку — с правкой, если она есть."""
        saved = (known.get('draft') or '').strip()
        if saved:
            return saved, 'правлено руками'
        name = names.trusted((known.get('name') or '').strip(),
                             known.get('title') or contact.name or '')
        if not name:
            name = tgcontacts.greeting_name(
                {'title': contact.name or '', 'name': ''})
        text = templates.substitute(base_text, {'NAME': name,
                                                'USERNAME': contact.username})
        talk = tgcontacts.load_event_talk()
        return softeners.build(
            text, name=name, address=known.get('address') or 'вы',
            transcript=history, talk=talk.get(str(known.get('id', ''))) or (),
            kind=known.get('type') or '', last_seen=known.get('date') or '',
            recall=known.get('recall') or '',
            sender=self.cfg.get('sender_gender') or 'м',
            intro=self.cfg.get('sender_intro') or '',
            sender_name=self.cfg.get('sender_name') or '',
            title=known.get('title') or '')

    def save_draft(self, contact, known, made, edited):
        """Запомнить правку и записать пару «предложено — стало»."""
        edited = (edited or '').strip()
        if edited == (made or '').strip():
            edited = ''            # ничего не меняли — не храним копию
        if edited:
            known['draft'] = edited
        else:
            known.pop('draft', None)
        if known.get('id'):
            self.contacts_store[str(known['id'])] = known
            tgcontacts.save_store(self.contacts_store)
        if edited:
            self._log_edit(contact, made, edited)
        self.fill_who()

    def _log_edit(self, contact, made, edited):
        """Правки копятся в файл — на них потом смотрим и выводим правила."""
        from datetime import datetime
        try:
            with open(EDITS_PATH, 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    'when': datetime.now().strftime('%Y-%m-%d %H:%M'),
                    'who': contact.label,
                    'made': made,
                    'edited': edited,
                }, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def open_contact(self, contact):
        """Открыть человека в настольном Telegram."""
        item = {'username': contact.username or '', 'kind': contact.kind}
        if not item['username'] and contact.key.startswith('id'):
            item['id'] = contact.key[2:]
        link = tgcontacts.tg_link(item)
        if not link:
            messagebox.showinfo('Нет ссылки',
                                'У «{}» нет ни username, ни id — открыть нечего.'
                                .format(contact.label))
            return
        subprocess.Popen(['open', link])

    def on_reset_done(self):
        """Забыть, кому уже делали черновики в этом источнике."""
        items, label = self.current_contacts()
        if not items:
            return
        sources = sorted({contact.source for contact in items})
        done = sum(len(progress.load_done(source)) for source in sources)
        if not done:
            messagebox.showinfo('Нечего забывать',
                                'В источнике «{}» отметок пока нет.'.format(label))
            return
        if not messagebox.askyesno(
                'Забыть отметки',
                'Снять отметку «уже писали» со всех в источнике «{}»?\n\n'
                'Отметок сейчас: {}. Сами черновики в Telegram останутся —\n'
                'программа просто перестанет считать этих людей обработанными.'.format(
                    label, done)):
            return
        for source in sources:
            progress.reset_done(source)
        self.log('Отметки «уже писали» сняты: {} ({})'.format(done, label), 'ok')
        self.invalidate_counts()
        self.fill_who()
        self.refresh_ui()

    def pick_all(self, on):
        for contact in self.who_rows:
            if on:
                self.excluded.discard(contact.key)
            else:
                self.excluded.add(contact.key)
        self.cfg['excluded'] = sorted(self.excluded)
        config_module.save(self.cfg)
        self.invalidate_counts()
        self.fill_who()
        self.refresh_ui()

    def invalidate_counts(self):
        self._counts_cache = None

    def _counts(self):
        """Сколько людей в источнике и сколько осталось.

        Считается лениво и кэшируется: в источнике бывает под тысячу человек,
        а refresh_ui дёргается на каждое действие в окне.
        """
        if self._counts_cache is None:
            items, label = self.current_contacts()
            if items is None:
                self._counts_cache = (None, '', 0, 0)
            else:
                self._counts_cache = (items, label, len(items), len(self._pending(items)))
        return self._counts_cache

    def _pending(self, items):
        """Отсеивает тех, кому черновик уже сделан или снята галочка."""
        cache = {}
        result = []
        for contact in items:
            if contact.key in self.excluded:
                continue
            if contact.source not in cache:
                cache[contact.source] = progress.load_done(contact.source)
            if contact.key not in cache[contact.source]:
                result.append(contact)
        return result

    # ---------------------------------------------------------------- текст

    def _add_tab(self, title, text):
        holder = ttk.Frame(self.tabs)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1, minsize=220)

        editor = tk.Text(holder, wrap='word', height=10, undo=True,
                         font=('Helvetica', 13), relief='solid', borderwidth=1,
                         highlightthickness=0, padx=8, pady=6,
                         autoseparators=True, maxundo=200)
        paint_text(editor)
        bar = ttk.Scrollbar(holder, command=editor.yview)
        editor.configure(yscrollcommand=bar.set)
        editor.grid(row=0, column=0, sticky='nsew')
        bar.grid(row=0, column=1, sticky='ns')

        editor.insert('1.0', text)
        editor.edit_reset()
        editor.edit_modified(False)
        # <<Modified>> приходится каждый раз сбрасывать вручную, и Tk на
        # macOS успевает перерисовать виджет — текст заметно мигает.
        for event in ('<KeyRelease>', '<<Paste>>', '<<Cut>>', '<<Undo>>',
                      '<<Redo>>'):
            editor.bind(event, self._on_edited, add='+')

        self.tabs.add(holder, text='  {}  '.format(title))
        self.editors.append((holder, editor))
        return editor

    def load_messages(self):
        """Вкладки берутся из общего хранилища и туда же сохраняются."""
        items = config_module.messages(self.cfg)
        if not items:
            items = [{'title': 'Вариант 1', 'text': templates.DEFAULT_TEXT}]

        for holder, _editor in self.editors:
            holder.destroy()
        self.editors = []
        for item in items:
            self._add_tab(item['title'], item['text'])

        index = config_module.active_index(self.cfg, len(self.editors))
        self.tabs.select(index)
        self._text_dirty = False
        self.save_messages(force=True)

    def _tab_titles(self):
        return [self.tabs.tab(i, 'text').strip() for i in range(len(self.editors))]

    def active_editor(self):
        if not self.editors:
            return None
        index = self.tabs.index(self.tabs.select()) if self.tabs.tabs() else 0
        index = min(max(0, index), len(self.editors) - 1)
        return self.editors[index][1]

    def editor_variants(self):
        """Рассылается ровно то, что на выбранной вкладке."""
        editor = self.active_editor()
        if editor is None:
            return []
        text = editor.get('1.0', 'end-1c').strip()
        return [text] if text else []

    def _on_tab_changed(self, _event=None):
        self.save_messages()
        self.refresh_ui()

    def _on_edited(self, event=None):
        """Только лёгкая работа: пересчитывать тысячу контактов на каждую
        нажатую клавишу нельзя — окно начинает подвисать."""
        self._text_dirty = True
        if self._status_after_id is not None:
            self.root.after_cancel(self._status_after_id)
        self._status_after_id = self.root.after(400, self._update_text_status)

        if self._save_after_id is not None:
            self.root.after_cancel(self._save_after_id)
        self._save_after_id = self.root.after(1200, self.save_messages)

    def _update_text_status(self):
        if self.text_mode.get() == 'column':
            self.text_status.set('текст берётся из колонки таблицы для каждого человека')
            return
        variants = self.editor_variants()
        if not variants:
            parts = ['текст пустой']
        else:
            parts = ['вкладок: {}'.format(len(self.editors))]
            spins = templates.spin_options(variants[0])
            if spins > 1:
                parts.append('вариантов текста: {}'.format(spins))
            broken = templates.missing_placeholders(variants)
            if broken:
                parts.append('в этой вкладке нет {NAME}')
        parts.append('сохраняется…' if self._text_dirty else 'сохранено')
        self.text_status.set('   ·   '.join(parts))

    def save_messages(self, force=False):
        self._save_after_id = None
        if not self.editors:
            return
        items = [
            {'title': title, 'text': editor.get('1.0', 'end-1c')}
            for title, (_h, editor) in zip(self._tab_titles(), self.editors)
        ]
        active = self.tabs.index(self.tabs.select()) if self.tabs.tabs() else 0
        changed = (items != self.cfg.get('messages')
                   or active != self.cfg.get('active_message'))
        if changed or force:
            self.cfg['messages'] = items
            self.cfg['active_message'] = active
            self.cfg.pop('message', None)     # старое одиночное поле больше не нужно
            config_module.save(self.cfg)
        self._text_dirty = False
        self._update_text_status()

    def on_toggle_personalize(self):
        on = self.personalize_var.get()
        self.cfg['personalize'] = on
        config_module.save(self.cfg)
        if on:
            self.log('Начало письма будет своим: «Имя, привет!», форма «ты»/«вы» '
                     'из вкладки «Контакты», извинение — если вы не ответили\n'
                     'на последнее сообщение или уже звали, и оговорка, если '
                     'человек уже отвечал про эту конференцию.\n'
                     'Тело письма уходит дословно. Проверьте пробным прогоном.',
                     'muted')
        self.refresh_ui()

    def on_add_variant(self):
        title = 'Вариант {}'.format(len(self.editors) + 1)
        self._add_tab(title, 'Привет, {NAME}!\n')
        self.tabs.select(len(self.editors) - 1)
        self.save_messages(force=True)
        self.refresh_ui()

    def on_rename_variant(self):
        if not self.editors:
            return
        index = self.tabs.index(self.tabs.select())
        current = self._tab_titles()[index]
        name = simpledialog.askstring('Название вкладки', 'Как назвать этот текст?',
                                      initialvalue=current, parent=self.root)
        if not name:
            return
        self.tabs.tab(index, text='  {}  '.format(name.strip()[:24]))
        self.save_messages(force=True)

    def on_delete_variant(self):
        if len(self.editors) <= 1:
            messagebox.showinfo('Нельзя удалить', 'Должен остаться хотя бы один текст.')
            return
        index = self.tabs.index(self.tabs.select())
        title = self._tab_titles()[index]
        if not messagebox.askyesno('Удаление', 'Удалить вкладку «{}»?'.format(title)):
            return
        holder, _editor = self.editors.pop(index)
        holder.destroy()
        self.tabs.select(min(index, len(self.editors) - 1))
        self.save_messages(force=True)
        self.refresh_ui()

    # ------------------------------------------------------------- прогон

    def on_preview(self):
        self._start_run(dry_run=True)

    def on_create(self):
        self._start_run(dry_run=False)

    def on_stop(self):
        self.stop_flag.set()
        self.log('Останавливаюсь после текущего контакта...', 'warn')

    def _start_run(self, dry_run):
        items, label = self.current_contacts()
        if items is None:
            messagebox.showinfo(
                'Нет контактов',
                {'sheet': 'Сначала прочитайте таблицу.',
                 'tgfolder': 'Сначала загрузите папки и выберите одну.',
                 'segment': 'Выберите сегмент. Если список пуст — определите типы '
                            'на вкладке «Контакты».'}[self.source_mode.get()])
            return
        if not items:
            messagebox.showinfo('Пусто', 'В этом источнике нет ни одного человека.')
            return

        from_column = self.text_mode.get() == 'column'
        variants = []
        if from_column:
            if self.source_mode.get() != 'sheet':
                messagebox.showinfo('Только для таблицы',
                                    'Текст из колонки берётся только при работе с таблицей.')
                return
            without = [c.label for c in items if not (c.message or '').strip()]
            if len(without) == len(items):
                messagebox.showinfo(
                    'Нет колонки с текстом',
                    'В таблице не нашлось колонки с текстом сообщения.\n\n'
                    'Назовите её «Сообщение» или «Текст» — программа подхватит её сама.')
                return
            if without:
                self.log('У {} человек в таблице пустой текст — их пропущу.'.format(
                    len(without)), 'warn')
        else:
            variants = self.editor_variants()
            if not variants:
                messagebox.showinfo('Пусто', 'Напишите текст сообщения.')
                return
            broken = templates.missing_placeholders(variants)
            if broken:
                self.log('В тексте нет {NAME}: %s. Имя подставляться не будет.'
                         % ', '.join(broken), 'warn')

        try:
            count = int(self.count_var.get())
        except ValueError:
            messagebox.showerror('Ошибка', 'Размер партии должен быть числом.')
            return

        pending = self._pending(items)
        if not pending:
            messagebox.showinfo('Всё пройдено',
                                'Здесь черновики уже сделаны всем.\n'
                                'Сбросить отметки можно в настройках.')
            return

        batch = pending[:count]

        if not dry_run:
            if self.client is None:
                messagebox.showinfo('Нужен вход', 'Сначала войдите в Telegram.')
                return
            limit = config_module.daily_limit(self.cfg)
            today = progress.today_count()
            if today + len(batch) > limit:
                if not messagebox.askyesno(
                        'Превышение лимита',
                        'Получится {} черновиков за сегодня при мягком лимите {}.\n\n'
                        'Чем больше однотипных обращений к незнакомым людям, тем выше '
                        'шанс получить ограничение аккаунта.\n\nВсё равно продолжить?'.format(
                            today + len(batch), limit)):
                    return

        self.save_messages()
        self.remember_selection()
        self.stop_flag.clear()
        self.set_busy(True, stoppable=True)
        self.log('')
        self.log('{}: {} из {} оставшихся · {}'.format(
            'Пробный прогон' if dry_run else 'Создание черновиков',
            len(batch), len(pending), label), 'head')

        self.backend.submit(self._run_drafts(batch, variants, dry_run, from_column,
                                             self.personalize_var.get()))

    # ------------------------------------------------------------- корутины

    async def _read_sheet(self, url):
        loop = asyncio.get_event_loop()
        try:
            groups, skipped, explained = await loop.run_in_executor(
                None, contacts_module.from_sheet, url, self.cfg['columns'])
        except sheets.SheetError as e:
            self.post('error', text=str(e))
            self.post('busy', value=False)
            return
        except Exception as e:
            self.post('error', text='{}: {}'.format(type(e).__name__, e))
            self.post('busy', value=False)
            return

        self.sheet_groups = groups
        total = sum(len(v) for v in groups.values())

        labels = {'name': 'имя', 'telegram': 'telegram', 'group': 'кто пишет',
                  'done': 'уже позвали', 'message': 'текст сообщения'}
        self.post('log', text='Колонки:', level='head')
        for key in ('name', 'telegram', 'group', 'done', 'message'):
            if key not in explained:
                continue
            index, header, how = explained[key]
            self.post('log', text='  {:<16} колонка {} «{}» ({})'.format(
                labels[key] + ':', index + 1, header, how), level='muted')
        if 'message' not in explained:
            self.post('log', text='  колонки с текстом сообщения в таблице нет',
                      level='muted')

        self.post('log', text='Прочитано: {} человек в {} группах, пропущено строк {}'.format(
            total, len(groups), skipped.total), level='ok')
        for line in skipped.as_lines()[:6]:
            self.post('log', text='  ' + line, level='muted')

        self.post('sheet_groups')
        self.post('busy', value=False)

    async def _load_tg_folders(self):
        try:
            folders = await tgfolders.list_folders(self.client)
        except Exception as e:
            self.post('error', text='Не удалось прочитать папки: {}: {}'.format(
                type(e).__name__, e))
            self.post('busy', value=False)
            return

        self.tg_folders = folders
        if not folders:
            self.post('log', text='В аккаунте нет ни одной папки чатов.', level='warn')
        else:
            usable = [f for f in folders if f['total']]
            self.post('log', text='Папок: {}, из них непустых: {}'.format(
                len(folders), len(usable)), level='ok')
            for f in sorted(folders, key=lambda f: -f['total']):
                self.post('log', text='  {:<22} {:>4} чат(ов): людей {}, групп {}, '
                                      'каналов {}'.format(
                                          f['title'][:22], f['total'], f['people'],
                                          f['groups'], f['channels']),
                          level='muted' if f['total'] else 'warn')
        self.post('tg_folders')
        self.post('busy', value=False)

    async def _load_folder_contacts(self, folder):
        try:
            items = await tgfolders.load_contacts(self.client, folder)
        except Exception as e:
            self.post('error', text='{}: {}'.format(type(e).__name__, e))
            self.post('busy', value=False)
            return

        if not items:
            self.post('log', text='В папке «{}» не нашлось чатов, куда можно положить '
                                  'черновик.'.format(folder['title']), level='warn')
        else:
            note = ''
            if folder['dynamic']:
                note = (' Папка частично собрана по условию — взяты только чаты, '
                        'добавленные вручную.')
            self.post('log', text='«{}»: готово {} адресатов (людей {}, групп {}, '
                                  'каналов {}).{}'.format(
                                      folder['title'], len(items), folder['people'],
                                      folder['groups'], folder['channels'], note),
                      level='ok')
        self.post('status_line')
        self.post('busy', value=False)

    async def _run_drafts(self, batch, variants, dry_run, from_column, personalize=False):
        total = len(batch)
        state = {'done': 0}

        def on_event(kind, **data):
            if kind == 'start':
                state['done'] = data['index'] - 1
                self.post('progress', total=total, done=state['done'],
                          text='{} из {} · {}'.format(data['index'], total,
                                                      data['contact'].label))
                if dry_run:
                    self.post('log', text='')
                    self.post('log', text='{} ({})'.format(
                        data['contact'].label,
                        data['contact'].name or 'без имени'), level='head')
                    self.post('log', text=data['text'])
            elif kind == 'created':
                state['done'] += 1
                self.post('progress', total=total, done=state['done'],
                          text='{} из {} готово'.format(state['done'], total))
                self.post('log', text='{} — черновик создан'.format(data['contact'].label),
                          level='ok')
            elif kind == 'skipped':
                state['done'] += 1
                self.post('log', text='{} — пропуск: {}'.format(
                    data['contact'].label, data['reason']), level='warn')
            elif kind == 'failed':
                state['done'] += 1
                self.post('log', text='{} — ошибка: {}'.format(
                    data['contact'].label, data['error']), level='err')
            elif kind == 'tick':
                self.post('progress', total=total, done=state['done'],
                          text='{} из {} готово · следующий через {} с'.format(
                              state['done'], total, data['seconds']))
            elif kind == 'prepared':
                self.post('log', text='    доработано: {}'.format(data['note']),
                          level='muted')
            elif kind == 'prepare_failed':
                self.post('log', text='    доработать не вышло ({}), беру заготовку'.format(
                    data['error']), level='warn')
            elif kind == 'reconnect':
                self.post('log', level='warn',
                          text='Связь с Telegram пропала — подключаюсь заново '
                               '({} из {})...'.format(data['attempt'],
                                                      data['attempts']))
            elif kind == 'reconnected':
                self.post('log', text='Связь восстановлена, продолжаю.', level='ok')
            elif kind == 'flood':
                self.post('log', text='Telegram просит подождать {} с — жду.'.format(
                    data['seconds']), level='warn')
            elif kind == 'stopped':
                self.post('log', text='Остановлено.', level='warn')
            elif kind == 'aborted':
                self.post('log', text=data['reason'], level='err')

        # текст-образец нужен, чтобы узнать, кого уже звали на это же
        reference = (variants or [''])[0]
        if personalize:
            await self._refresh_event_talk(reference)
        prepare, should_skip = self._make_preparer(reference)
        if not personalize:
            prepare = None

        summary = None
        try:
            summary = await drafts.create_drafts(
                client=self.client,
                contacts=batch,
                variants=variants,
                contacts_file='',
                delay_range=config_module.delay_range(self.cfg),
                on_event=on_event,
                dry_run=dry_run,
                should_stop=self.stop_flag.is_set,
                use_contact_message=from_column,
                prepare=prepare,
                should_skip=should_skip,
            )
        except drafts.RunAborted as e:
            self.post('error', text=str(e))
        except Exception as e:
            self.post('error', text='{}: {}'.format(type(e).__name__, e))

        # «о чём говорили» спрашивается по ходу прогона — сохраняем
        if getattr(self, '_store_dirty', False):
            tgcontacts.save_store(self.contacts_store)
            self._store_dirty = False

        if summary is not None:
            self.post('log', text='')
            if dry_run:
                self.post('log', text='Показано текстов: {}. Никто не помечен как '
                                      'обработанный.'.format(summary.created), level='head')
            else:
                self.post('log', text='Создано: {} · пропущено: {} · ошибок: {}'.format(
                    summary.created, summary.skipped, summary.failed), level='head')
                for problem in summary.problems[:10]:
                    self.post('log', text='  ' + problem, level='muted')
                if summary.created:
                    self.post('log', text='Черновики лежат в диалогах Telegram — '
                                          'проверьте текст и отправьте руками.', level='muted')

        if summary is not None and summary.stopped:
            done_text = 'Остановлено на {} из {}'.format(summary.processed, total)
        elif summary is not None:
            done_text = 'Готово: {} из {}'.format(summary.processed, total)
        else:
            done_text = 'Прервано'
        self.post('progress', total=total, done=total, text=done_text)
        self.post('status_line')
        self.post('busy', value=False)

    async def _refresh_event_talk(self, reference_text=''):
        """Что говорили про конференцию в группах — раз в неделю.

        Глобальный поиск стоит несколько запросов, а даёт то, чего в личной
        переписке нет: человек мог обсуждать конфу в общем чате.
        """
        age = tgcontacts.event_talk_age()
        if age is not None and age < 7:
            return
        words = set(reactions.EVENT_WORDS)
        words.update(w for w in openrouter.invite_keys(reference_text)
                     if len(w) >= 5 and w != 'name')
        self.post('log', text='Смотрю, кто что писал про конференцию...',
                  level='muted')
        try:
            talk = await tgcontacts.search_event_talk(
                self.client, sorted(words),
                guard=tgcontacts.FloodGuard(
                    on_wait=lambda seconds: self.post(
                        'log', level='warn',
                        text='Telegram просит подождать {} с — жду.'.format(seconds))))
        except Exception as e:
            self.post('log', level='warn',
                      text='  не вышло ({}) — пишу без этой памяти'.format(
                          type(e).__name__))
            return
        # Оставляем только знакомых людей: в поиск попадают боты, служебные
        # чаты и рассылки, а их «реакции» — это анонсы и ответы автоматики.
        store = self.contacts_store
        talk = {uid: texts for uid, texts in talk.items()
                if (store.get(uid) or {}).get('kind') == tgcontacts.PERSON}
        tgcontacts.save_event_talk(talk)
        self.post('log', level='muted',
                  text='  нашёл высказавшихся: {}'.format(len(talk)))

    def _make_preparer(self, reference_text=''):
        """Готовит текст под каждого: имя, «ты»/«вы», извинение за пропуск.

        Модель здесь больше не участвует. Вариантов начала всего три, и
        выбираются они по переписке — правилами. Так текст предсказуем, а
        тело письма уходит дословно.
        """
        store = self.contacts_store
        # переписка нужна и для приветствия, и для проверки «уже звали» —
        # читаем один раз за прогон
        cache = {}

        def find(contact):
            for value in store.values():
                if value.get('username') and contact.username and \
                        value['username'].lower() == contact.username.lower():
                    return value
            if contact.key.startswith('id'):
                return store.get(contact.key[2:], {})
            return {}

        async def history_of(contact):
            known = find(contact)
            # только по известному id: искать чат по username — это
            # ResolveUsername, самый лимитируемый запрос
            dialog_id = known.get('id')
            if not dialog_id:
                return known, []
            if dialog_id not in cache:
                rows = await tgcontacts.history(self.client, dialog_id, limit=50)
                if rows is None:
                    # прочитать не вышло — в кэш не кладём, иначе одна осечка
                    # оставит человека без доводки до конца прогона
                    return known, None
                cache[dialog_id] = rows
            return known, cache[dialog_id]

        talk = tgcontacts.load_event_talk()
        self._store_dirty = False

        async def recall_of(contact, known, history):
            """О чём говорили в прошлый раз — спрашиваем один раз на человека.

            Только для тех, кому пойдёт напоминание: формальный регистр и
            долгое молчание. Переписка уже прочитана, лишних запросов в
            Telegram нет — только один вопрос модели.
            """
            if 'recall' in known:
                return known['recall'] or ''
            # Работаем только по двум сегментам: остальным конференция не
            # адресована, и платить за чтение их переписки незачем.
            if known.get('type') not in softeners.WORK_SEGMENTS:
                return ''
            # Тема прошлого разговора нужна ровно там, где она попадёт в
            # текст: после долгого молчания и только если разговор был.
            if not softeners.had_real_talk(history):
                return ''
            tone = softeners.register(known.get('address') or 'вы',
                                      known.get('type') or '')
            months = softeners.silent_months(known.get('date') or '')
            if months is None:
                return ''
            if months < softeners.LONG_SILENCE_MONTHS and \
                    not (tone == 'формальный' and months >= softeners.SILENT_MONTHS):
                return ''
            key = config_module.openrouter_key(self.cfg)
            if not key:
                return ''
            loop = asyncio.get_running_loop()
            phrase = await loop.run_in_executor(
                None, openrouter.recall_topic, key,
                config_module.openrouter_model(self.cfg), history,
                known.get('about') or '')
            known['recall'] = phrase
            self._store_dirty = True
            return phrase

        async def prepare(contact, base_text):
            known, rows = await history_of(contact)
            if rows is None:
                # None — переписка не прочиталась (не то же самое, что пустой
                # чат). Сочинять приветствие вслепую нельзя: извинение за
                # пропущенное сообщение зависит как раз от неё.
                raise RuntimeError('переписка не прочиталась')
            history = tgcontacts.as_transcript(rows)
            title = known.get('title') or contact.name or ''
            name = names.trusted((known.get('name') or '').strip(), title)
            if not name:
                name = tgcontacts.greeting_name(
                    {'title': contact.name or '', 'name': ''})
            return softeners.build(base_text, name=name,
                                   address=known.get('address') or 'вы',
                                   transcript=history,
                                   talk=talk.get(str(known.get('id', ''))) or (),
                                   kind=known.get('type') or '',
                                   last_seen=known.get('date') or '',
                                   recall=await recall_of(contact, known,
                                                          history),
                                   sender=self.cfg.get('sender_gender') or 'м',
                                   intro=self.cfg.get('sender_intro') or '',
                                   sender_name=self.cfg.get('sender_name') or '',
                                   title=title)

        keys = openrouter.invite_keys(reference_text)
        window = config_module.invite_window_days(self.cfg)

        async def should_skip(contact):
            _known, rows = await history_of(contact)
            if not rows or not keys:
                return ''
            for out, when, text in rows:
                if not out or not openrouter.mentions_invite(text, keys):
                    continue
                days = _days_ago(when)
                if days is None or days <= window:
                    return 'уже звали на это мероприятие{}'.format(
                        '' if days is None else ', {} дн. назад'.format(days))
                # звали давно — это была прошлая конференция, зовём заново
                break
            return ''

        return prepare, should_skip

    async def _download_contacts(self):
        try:
            items, dropped = await tgcontacts.download(
                self.client,
                on_progress=lambda n: self.post('log', text='  прочитано диалогов: {}'.format(n),
                                                level='muted'),
                should_stop=self.stop_flag.is_set,
                guard=tgcontacts.FloodGuard(
                    on_wait=lambda seconds: self.post(
                        'log', level='warn',
                        text='Telegram просит подождать {} с — жду.'.format(seconds))))
        except Exception as e:
            self.post('error', text='{}: {}'.format(type(e).__name__, e))
            self.post('busy', value=False)
            return

        # Разбор не теряем, а вот исчезнувшие чаты (боты, переехавшие группы,
        # удалённые диалоги) из хранилища убираем — иначе они висят вечно.
        old = self.contacts_store
        fresh = {}
        for item in items:
            saved = old.get(item['id'], {})
            for field in ('type', 'why', 'address', 'name', 'topic', 'tone',
                          'recall', 'warm', 'draft'):
                if saved.get(field):
                    item[field] = saved[field]
            fresh[item['id']] = item

        if self.stop_flag.is_set():
            # выгрузка неполная — чистить нельзя, просто дополняем
            old.update(fresh)
            store = old
        else:
            removed = len(old) - len(set(old) & set(fresh))
            store = fresh
            if removed:
                self.post('log', text='Убрано из списка (боты, переехавшие группы, '
                                      'удалённые чаты): {}'.format(removed), level='muted')
        self.contacts_store = store
        self._by_username = None
        tgcontacts.save_store(store)

        self.contacts_list = sorted(items, key=lambda c: c.get('title', ''))
        kinds = {}
        for c in items:
            kinds[c['kind']] = kinds.get(c['kind'], 0) + 1
        self.post('log', text='Живых чатов {}: {}'.format(
            len(items), ', '.join('{} {}'.format(k, v) for k, v in sorted(kinds.items()))),
            level='ok')
        if dropped:
            self.post('log', text='Не показываю: {}'.format(
                ', '.join('{} {}'.format(k, v) for k, v in sorted(dropped.items()))),
                level='muted')
        self.post('contacts')
        self.post('busy', value=False)

    def on_measure_warmth(self):
        """Замерить теплоту у тех, по кому её ещё не считали."""
        if self.client is None:
            messagebox.showinfo('Нужен вход', 'Сначала войдите в Telegram.')
            return
        todo = [item for item in self.contacts_store.values()
                if isinstance(item, dict)
                and item.get('kind') == tgcontacts.PERSON
                and item.get('type') in softeners.WORK_SEGMENTS
                and not item.get('warm')]
        if not todo:
            messagebox.showinfo(
                'Уже измерено',
                'По всем контактам из сегментов «ит и digital» и «клиент»\n'
                'теплота уже посчитана.')
            return
        if not messagebox.askyesno(
                'Измерить теплоту',
                'Прочитаю переписку у {} человек и посчитаю, насколько плотно\n'
                'и неформально вы общались. Модель не участвует — это только\n'
                'чтение диалогов.\n\nЗаймёт несколько минут. Начинаем?'.format(
                    len(todo))):
            return
        todo.sort(key=lambda item: item.get('date') or '', reverse=True)
        self.set_busy(True, stoppable=True)
        self.log('Измеряю теплоту: {} чат(ов)...'.format(len(todo)), 'muted')
        self.backend.submit(self._measure_warmth(todo))

    async def _measure_warmth(self, todo):
        """Плотность, частота и неформальность — по прочитанной переписке."""
        guard = tgcontacts.FloodGuard(
            on_wait=lambda seconds: self.post(
                'log', level='warn',
                text='Telegram просит подождать {} с — жду.'.format(seconds)))
        limit = asyncio.Semaphore(3)
        state = {'done': 0, 'failed': 0}

        async def handle(item):
            if self.stop_flag.is_set() or guard.stopped:
                return
            async with limit:
                if self.stop_flag.is_set() or guard.stopped:
                    return
                rows = await tgcontacts.history(self.client, item['id'],
                                                limit=30, guard=guard)
                if rows is None:
                    state['failed'] += 1
                    return
                item['warm'] = priority.stats(rows)
                state['done'] += 1
                if state['done'] % 50 == 0:
                    self.post('log', level='muted',
                              text='  измерено: {} из {}'.format(
                                  state['done'], len(todo)))
                    tgcontacts.save_store(self.contacts_store)

        await asyncio.gather(*(handle(item) for item in todo))
        tgcontacts.save_store(self.contacts_store)
        self.post('log', level='ok', text='Теплота измерена у {} человек{}.'.format(
            state['done'],
            ', не прочиталось {}'.format(state['failed']) if state['failed'] else ''))
        if guard.stopped:
            self.post('log', text=guard.stopped, level='warn')
        self.post('contacts')
        self.post('busy', value=False)

    async def _classify_contacts(self, todo, key):
        """Несколько контактов разом: по одному 5000 чатов заняли бы часы."""
        loop = asyncio.get_event_loop()
        model = self.cfg.get('openrouter_model') or openrouter.DEFAULT_MODEL
        about = self.cfg.get('openrouter_about', '')
        company = self.cfg.get('openrouter_company', '')
        examples = self.manual_examples()
        total = len(todo)
        state = {'done': 0, 'failed': 0, 'empty': 0, 'unread': 0, 'fatal': ''}
        limit = asyncio.Semaphore(3)     # три читателя: лимиты у аккаунта общие
        guard = tgcontacts.FloodGuard(
            on_wait=lambda seconds: self.post(
                'log', level='warn',
                text='Telegram просит подождать {} с — жду и продолжаю.'.format(
                    seconds)))
        warmed = {'done': False}

        async def warm_up():
            """Один проход по диалогам, когда Telegram не отдаёт переписку.

            Так чинится «Could not find the input entity»: клиенту нужен
            access_hash собеседника, а он живёт в кэше сессии и теряется.
            """
            if warmed['done']:
                return
            warmed['done'] = True
            self.post('log', text='Telegram не отдал переписку — обновляю список '
                                  'диалогов, это разовая операция...', level='warn')
            try:
                seen = await tgcontacts.warm_entities(
                    self.client, guard=guard,
                    on_progress=lambda n: self.post(
                        'log', text='  просмотрено диалогов: {}'.format(n),
                        level='muted'))
                self.post('log', text='Готово, диалогов: {}'.format(seen), level='ok')
            except Exception as e:
                self.post('log', text='Не вышло обновить диалоги: {}'.format(e),
                          level='err')

        async def handle(item):
            if self.stop_flag.is_set() or state['fatal']:
                return
            async with limit:
                if guard.stopped and not state['fatal']:
                    state['fatal'] = guard.stopped
                if self.stop_flag.is_set() or state['fatal']:
                    return
                rows = await tgcontacts.history(self.client, item['id'],
                                                limit=30, guard=guard)
                if rows is None:
                    # переписку не отдали — сначала чиним кэш, потом ещё раз
                    await warm_up()
                    rows = await tgcontacts.history(self.client, item['id'],
                                                    limit=30, guard=guard)
                text = None if rows is None else tgcontacts.as_transcript(rows)
                if rows is not None:
                    # плотность, частота и неформальность — из того же
                    # запроса, отдельно за ними ходить незачем
                    item['warm'] = priority.stats(rows)
                if text is None:
                    state['unread'] += 1
                    item.pop('no_data', None)     # это не пустой чат, а осечка
                    return
                person = item.get('kind') == tgcontacts.PERSON
                if not item.get('about'):
                    item['about'] = await tgcontacts.about(
                        self.client, item['id'], item.get('kind'), guard=guard)
                if openrouter.nothing_to_read(text, item['about']):
                    # ни переписки, ни описания — гадать по имени незачем.
                    # Помечаем, чтобы в следующий заход не читать заново;
                    # пометка слетает при обновлении списка из Telegram.
                    item['no_data'] = True
                    self.contacts_store[item['id']] = item
                    state['empty'] += 1
                    return
                try:
                    if person:
                        result = await loop.run_in_executor(
                            None, lambda: openrouter.classify_person(
                                key, model, item.get('title', ''), text,
                                about=about, company=company,
                                examples=examples, bio=item['about']))
                    else:
                        if item['about']:
                            text = 'Описание чата: {}\n\n{}'.format(item['about'], text)
                        result = await loop.run_in_executor(
                            None, lambda: openrouter.classify_group(
                                key, model, item.get('title', ''), text, about=about))
                except openrouter.OpenRouterError as e:
                    state['failed'] += 1
                    # ключ или деньги — дальше идти бессмысленно
                    if any(w in str(e) for w in ('401', '402', 'не задан')):
                        state['fatal'] = str(e)
                    elif state['failed'] <= 5:
                        self.post('log', text='{} — {}'.format(item.get('title', ''), e),
                                  level='err')
                    return
                except Exception:
                    state['failed'] += 1      # сеть моргнула — просто пропускаем
                    return

                if person:
                    item['type'] = result['type']
                    item['address'] = result['address']
                    item['company'] = result['company']
                    item['role'] = result['role']
                    # у «другое» имя обычно выдумано из служебного текста
                    item['name'] = result['name'] if result['type'] != 'другое' else ''
                else:
                    item['type'] = result['fit']
                    item['topic'] = result['topic']
                    item['tone'] = result['tone']
                item['why'] = result['why']
                self.contacts_store[item['id']] = item
                state['done'] += 1
                if state['done'] % 10 == 0:
                    tgcontacts.save_store(self.contacts_store)
                    self.post('contacts')
                self.post('progress', total=total, done=state['done'],
                          text='{} из {} · {}'.format(state['done'], total,
                                                      item.get('title', '')[:30]))

        # порциями, чтобы «Стоп» срабатывал быстро и память не пухла
        for start in range(0, total, 40):
            if self.stop_flag.is_set() or state['fatal']:
                break
            await asyncio.gather(*(handle(item) for item in todo[start:start + 40]))
            tgcontacts.save_store(self.contacts_store)   # после каждой порции

        tgcontacts.save_store(self.contacts_store)
        if state['fatal']:
            self.post('log', text='Остановлено: {}'.format(state['fatal']), level='err')
        elif self.stop_flag.is_set():
            self.post('log', text='Остановлено вручную.', level='warn')
        if guard.waits:
            self.post('log', level='muted',
                      text='Пауз по просьбе Telegram: {} (всего {} с)'.format(
                          guard.waits, guard.seconds))
        left = sum(1 for c in self.contacts_store.values()
                   if c.get('kind') == todo[0].get('kind') and not c.get('type')
                   and not c.get('no_data')) if todo else 0
        self.post('log', text='Определено типов: {}{}{}{}{}'.format(
            state['done'],
            ', ошибок {}'.format(state['failed']) if state['failed'] else '',
            ', пропущено пустых {}'.format(state['empty']) if state['empty'] else '',
            ', не удалось прочитать {}'.format(state['unread']) if state['unread'] else '',
            '. Осталось неразобранных: {}'.format(left) if left else
            '. Неразобранных не осталось'),
            level='ok')
        self.post('progress', total=max(1, total), done=total, text='')
        self.post('contacts')
        self.post('busy', value=False)

    # ---------------------------------------------------------------- вход

    def on_login(self):
        if self.client is not None:
            self.on_logout()
            return
        self.set_busy(True)
        self.backend.submit(self._connect())

    def on_logout(self):
        if not messagebox.askyesno(
                'Выход',
                'После выхода понадобится снова вводить код из Telegram.\n'
                'Частые повторные входы Telegram не любит. Точно выйти?'):
            return
        self.set_busy(True)
        self.backend.submit(self._logout())

    async def _connect(self, silent=False):
        """silent=True — поднять сохранённый вход без единого вопроса.

        Так запуск программы не выглядит как «вход слетел»: сессия обычно
        жива, и подключение проходит молча.
        """
        try:
            client = build_client(self.cfg)
        except LoginFailed as e:
            if not silent:
                self.post('error', text=str(e))
            self.post('busy', value=False)
            return

        try:
            if silent:
                await client.connect()
                if not await client.is_user_authorized():
                    raise LoginFailed('сохранённый вход недействителен')
            else:
                await ensure_authorized(client, GuiPrompts(self))
        except LoginCancelled as e:
            self.post('log', text='Вход отменён: {}'.format(e), level='warn')
            await self._safe_disconnect(client)
            self.post('busy', value=False)
            return
        except Exception as e:
            if silent:
                self.post('status', text='Вход не выполнен — нажмите «Войти»',
                          logged_in=False)
            else:
                self.post('error', text=str(e) if isinstance(e, LoginFailed)
                          else '{}: {}'.format(type(e).__name__, e))
            await self._safe_disconnect(client)
            self.post('busy', value=False)
            return

        self.client = client
        me = await client.get_me()
        label = (me.first_name or '').strip()
        if me.username:
            label += ' @' + me.username
        # Род отправителя: «пропустил» или «пропустила». Определяем один раз,
        # по имени из профиля; если в настройках уже выбрано — не трогаем.
        changed = False
        if not self.cfg.get('sender_gender'):
            guess = names.gender(me.first_name or '')
            if guess:
                self.cfg['sender_gender'] = guess
                changed = True
        # как представиться тому, кто мог забыть: «Меня Иван зовут, я
        # организатор конференции и гендиректор Alto»
        if not self.cfg.get('sender_name') and (me.first_name or '').strip():
            self.cfg['sender_name'] = me.first_name.strip()
            changed = True
        if not self.cfg.get('sender_intro'):
            company = (self.cfg.get('openrouter_company') or '').split(',')[0]
            if company.strip():
                self.cfg['sender_intro'] = 'организатор конференции и ' \
                    'гендиректор {}'.format(company.strip())
                changed = True
        if changed:
            config_module.save(self.cfg)

        self.post('status', text=label.strip() or 'вход выполнен', logged_in=True)
        if not silent:
            self.post('log', text='Вход выполнен: {}'.format(label.strip()), level='ok')
        self.post('busy', value=False)

        # список папок стоит один запрос — тянем сразу, чтобы не пришлось жать кнопку
        try:
            folders = await tgfolders.list_folders(client)
        except Exception:
            return
        self.tg_folders = folders
        self.post('tg_folders')

    async def _logout(self):
        if self.client is not None:
            await logout(self.client)
            await self._safe_disconnect(self.client)
            self.client = None
        self.tg_folders = []
        self.post('status', text='Вход не выполнен', logged_in=False)
        self.post('log', text='Вышли из аккаунта.', level='warn')
        self.post('tg_folders')
        self.post('busy', value=False)

    async def _safe_disconnect(self, client):
        try:
            await client.disconnect()
        except Exception:
            pass

    # ------------------------------------------------------------ настройки

    def show_onboarding(self):
        result = onboarding.show(
            self.root, str(self.cfg.get('api_id', '')), str(self.cfg.get('api_hash', '')))
        if result is None:
            self.log('Без api_id и api_hash программа не сможет войти в Telegram.', 'warn')
            self.log('Заполните их в «Настройках» или откройте инструкцию оттуда.', 'muted')
            return
        api_id, api_hash = result
        self.cfg['api_id'], self.cfg['api_hash'] = api_id, api_hash
        config_module.save(self.cfg)
        self.log('Ключи сохранены. Теперь нажмите «Войти в Telegram».', 'ok')

    def open_settings(self):
        SettingsWindow(self)

    # ---------------------------------------------------------------- выход

    # ------------------------------------------------------------ обновления

    def _look_for_update(self, loud=False):
        """Есть ли на сервере версия новее. Работает в отдельном потоке."""
        info = updates.check()
        if info is None:
            if loud:
                self.queue.put(('log', {
                    'text': 'Обновлений нет: у вас версия {}.'.format(__version__),
                    'level': 'ok'}))
            return
        self.queue.put(('update', info))

    def check_updates(self):
        self.log('Смотрю, нет ли новой версии...', 'muted')
        threading.Thread(target=self._look_for_update, args=(True,),
                         daemon=True).start()

    def offer_update(self, info):
        notes = (info.get('notes') or '').strip()
        if not messagebox.askyesno(
                'Есть новая версия',
                'Вышла версия {}, у вас {}.\n\n{}\n'
                'Обновлю сам: скачаю, заменю программу и открою заново.\n'
                'Ничего подтверждать в системе не придётся.\n\n'
                'Обновиться сейчас?'.format(
                    info.get('version', '?'), __version__,
                    (notes + '\n') if notes else '')):
            return
        if self.busy and not messagebox.askyesno(
                'Идёт работа',
                'Сейчас выполняется прогон, и программа закроется.\n'
                'Всё равно обновиться?'):
            return
        self.set_busy(True)
        threading.Thread(target=self._install_update, args=(info,),
                         daemon=True).start()

    def _install_update(self, info):
        def say(text, level='muted'):
            self.queue.put(('log', {'text': text, 'level': level}))

        try:
            say('Качаю версию {}...'.format(info.get('version', '')))
            last = {'shown': -1}

            def progress(done, total):
                if not total:
                    return
                percent = int(100 * done / total)
                if percent >= last['shown'] + 20:
                    last['shown'] = percent
                    say('  скачано {}%'.format(percent))

            archive = updates.download(info, progress)
            say('Проверяю и распаковываю...')
            new_app = updates.unpack(archive)
            version = updates.verify(new_app)
            say('Ставлю версию {} и перезапускаюсь.'.format(version), 'ok')
            updates.install(new_app)
        except updates.UpdateError as e:
            self.queue.put(('error', {'text': str(e)}))
            self.queue.put(('busy', {'value': False}))
            return
        except Exception as e:
            self.queue.put(('error', {
                'text': 'Обновление не вышло ({}: {})'.format(
                    type(e).__name__, e)}))
            self.queue.put(('busy', {'value': False}))
            return
        # подменой занимается отдельный процесс — он ждёт, пока мы выйдем
        self.queue.put(('quit_for_update', {}))

    def on_close(self):
        self.save_messages()
        self.remember_selection()
        if self.busy and not messagebox.askyesno(
                'Идёт работа', 'Сейчас выполняется прогон. Всё равно закрыть?'):
            return
        # снимаем таймер разбора очереди, иначе Tk ругается после destroy
        if self._drain_after_id is not None:
            try:
                self.root.after_cancel(self._drain_after_id)
            except tk.TclError:
                pass
            self._drain_after_id = None

        self.stop_flag.set()
        if self.client is not None:
            try:
                self.backend.submit(self._safe_disconnect(self.client)).result(timeout=3)
            except Exception:
                pass
        self.backend.shutdown()
        self.root.destroy()


class DraftWindow(tk.Toplevel):
    """Письмо одному человеку: посмотреть, поправить, сохранить.

    Нужно не только чтобы починить одно письмо. Разница между тем, что
    собрала программа, и тем, что вы отправили, — самый прямой источник
    правил: по 140 таким парам стало видно, что компанию собеседника вы
    не называете никогда, а «может тебе полезно будет» пишете постоянно.
    """

    def __init__(self, app, contact, known, made, note):
        super().__init__(app.root)
        self.app = app
        self.contact = contact
        self.known = known
        self.title('Письмо · {}'.format(contact.label))
        self.transient(app.root)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self.made = made

        head = ttk.Frame(self, padding=(PAD, PAD, PAD, 0))
        head.grid(row=0, column=0, sticky='ew')
        where = '  ·  '.join(part for part in (
            known.get('company') or '', known.get('role') or '',
            known.get('type') or '') if part)
        ttk.Label(head, text=(known.get('title') or contact.label)).pack(side='left')
        if where:
            ttk.Label(head, text='   ' + where, foreground=MUTED).pack(side='left')

        marks = ttk.Frame(self, padding=(PAD, 2, PAD, 6))
        marks.grid(row=1, column=0, sticky='ew')
        points = priority.score(known)
        ttk.Label(marks, foreground=MUTED, justify='left',
                  text='шанс {} {}  ·  что сделала программа: {}'.format(
                      points, priority.label(points), note)).pack(side='left')

        holder = ttk.Frame(self, padding=(PAD, 0, PAD, 0))
        holder.grid(row=2, column=0, sticky='nsew')
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.editor = tk.Text(holder, wrap='word', width=64, height=16,
                              undo=True, font=('Helvetica', 13), relief='solid',
                              borderwidth=1, highlightthickness=0, padx=8, pady=6)
        paint_text(self.editor)
        bar = ttk.Scrollbar(holder, command=self.editor.yview)
        self.editor.configure(yscrollcommand=bar.set)
        self.editor.grid(row=0, column=0, sticky='nsew')
        bar.grid(row=0, column=1, sticky='ns')
        self.editor.insert('1.0', (known.get('draft') or '').strip() or made)
        self.editor.edit_reset()

        hint = ttk.Label(
            self, padding=(PAD, 6, PAD, 0), foreground=MUTED, justify='left',
            text='Правка уйдёт в черновик вместо собранного текста и '
                 'сохранится за этим человеком.\n'
                 'Разницу программа запоминает — по ней потом видно, что в '
                 'правилах не так.')
        hint.grid(row=3, column=0, sticky='w')

        buttons = ttk.Frame(self, padding=PAD)
        buttons.grid(row=4, column=0, sticky='ew')
        ttk.Button(buttons, text='Сохранить', command=self.save).pack(side='left')
        ttk.Button(buttons, text='Вернуть как было',
                   command=self.reset).pack(side='left', padx=8)
        ttk.Button(buttons, text='Открыть в Telegram',
                   command=lambda: app.open_contact(contact)).pack(side='left')
        ttk.Button(buttons, text='Закрыть',
                   command=self.destroy).pack(side='right')

        app._fit_window(self, 720, 620)
        self.bind('<Escape>', lambda _e: self.destroy())
        self.editor.focus_set()

    def reset(self):
        self.editor.delete('1.0', 'end')
        self.editor.insert('1.0', self.made)

    def save(self):
        self.app.save_draft(self.contact, self.known, self.made,
                            self.editor.get('1.0', 'end-1c'))
        self.app.log('{} — письмо сохранено'.format(self.contact.label), 'ok')
        self.destroy()


# --------------------------------------------------------------------------

class SettingsWindow(object):
    """Всё редкое вынесено сюда, чтобы не мешать на главном экране."""

    def __init__(self, app):
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.title('Настройки')
        self.top.resizable(False, False)
        self.top.transient(app.root)

        wrap = ttk.Frame(self.top, padding=16)
        wrap.pack(fill='both', expand=True)
        wrap.columnconfigure(1, weight=1)

        self.vars = {}
        self.secrets = {}
        fields = [
            ('api_id', 'api_id', str(app.cfg.get('api_id', ''))),
            ('api_hash', 'api_hash', str(app.cfg.get('api_hash', ''))),
            ('min_delay_seconds', 'Пауза между людьми, от (с)',
             str(app.cfg.get('min_delay_seconds', 20))),
            ('max_delay_seconds', 'Пауза между людьми, до (с)',
             str(app.cfg.get('max_delay_seconds', 45))),
            ('daily_limit', 'Мягкий дневной лимит', str(app.cfg.get('daily_limit', config_module.DEFAULT_DAILY_LIMIT))),
            ('openrouter_key', 'Ключ OpenRouter', str(app.cfg.get('openrouter_key', ''))),
            ('openrouter_model', 'Модель для типов',
             str(app.cfg.get('openrouter_model', '') or openrouter.DEFAULT_MODEL)),
            ('openrouter_about', 'Чем вы занимаетесь',
             str(app.cfg.get('openrouter_about', '') or openrouter.DEFAULT_ABOUT)),
            ('openrouter_company', 'Ваша компания (через запятую)',
             str(app.cfg.get('openrouter_company', ''))),
            ('sender_name', 'Ваше имя в письме',
             str(app.cfg.get('sender_name', ''))),
            ('sender_intro', 'Кто вы (после «я …»)',
             str(app.cfg.get('sender_intro', ''))),
        ]
        for index, (key, label, value) in enumerate(fields):
            ttk.Label(wrap, text=label).grid(row=index, column=0, sticky='w', pady=4)
            var = tk.StringVar(value=value)
            self.vars[key] = var
            entry = ttk.Entry(wrap, textvariable=var, width=40)
            entry.grid(row=index, column=1, sticky='ew', padx=8, pady=4)
            if key in ('api_hash', 'openrouter_key'):
                # ключи видно на любом скриншоте и при показе экрана
                entry.configure(show='•')
                shown = tk.BooleanVar(value=False)
                self.secrets[key] = (entry, shown)
                ttk.Checkbutton(
                    wrap, text='показать', variable=shown,
                    command=lambda k=key: self._toggle_secret(k)).grid(
                        row=index, column=2, sticky='w')

        # Род отправителя: программой пользуются не только мужчины, а в
        # приветствии есть «пропустил», «звал», «буду рад».
        row = len(fields)
        ttk.Label(wrap, text='Пишу о себе').grid(row=row, column=0, sticky='w',
                                                 pady=4)
        self.gender_var = tk.StringVar(value=app.cfg.get('sender_gender') or 'м')
        gender_row = ttk.Frame(wrap)
        gender_row.grid(row=row, column=1, sticky='w', padx=8, pady=4)
        ttk.Radiobutton(gender_row, text='пропустил, буду рад', value='м',
                        variable=self.gender_var).pack(side='left')
        ttk.Radiobutton(gender_row, text='пропустила, буду рада', value='ж',
                        variable=self.gender_var).pack(side='left', padx=16)

        ttk.Label(wrap,
                  text='Пауза меньше 15 секунд заметно повышает шанс словить FLOOD_WAIT.\n'
                       'Для аккаунта без истории массовых рассылок разумный потолок — 20–30 в день.\n\n'
                       'Ключ OpenRouter нужен только для определения типов контактов.\n'
                       'google/gemini-2.5-flash-lite — $0.10 за миллион токенов и лучший\n'
                       'результат из проверенных. «Чем вы занимаетесь» модель использует,\n'
                       'чтобы отличить подрядчика от заказчика — без этого она путается.\n'
                       'Названия своей компании нужны, чтобы своих же сотрудников она\n'
                       'не записывала в клиенты: «Alto, alto.codes».\n\n'
                       'Имя и «кто вы» складываются в представление для тех, с кем\n'
                       'давно и формально не общались: «Меня Иван зовут, я организатор\n'
                       'AI Growth Day и гендиректор Alto».',
                  foreground=MUTED, justify='left').grid(
            row=len(fields) + 1, column=0, columnspan=3, sticky='w', pady=(10, 10))

        buttons = ttk.Frame(wrap)
        buttons.grid(row=len(fields) + 2, column=0, columnspan=3, sticky='w')
        ttk.Button(buttons, text='Сохранить', command=self.save).pack(side='left')
        ttk.Button(buttons, text='Показать инструкцию',
                   command=self.instructions).pack(side='left', padx=8)
        ttk.Button(buttons, text='Проверить обновления',
                   command=self.app.check_updates).pack(side='left')
        ttk.Button(buttons, text='Открыть папку с файлами',
                   command=self.open_folder).pack(side='left')
        ttk.Button(buttons, text='Сбросить «уже обработано»',
                   command=self.reset_progress).pack(side='left', padx=8)

        self.top.grab_set()

    def _toggle_secret(self, key):
        entry, shown = self.secrets[key]
        entry.configure(show='' if shown.get() else '•')

    def save(self):
        self.app.cfg['sender_gender'] = self.gender_var.get()
        for key, var in self.vars.items():
            value = var.get().strip()
            if key in ('min_delay_seconds', 'max_delay_seconds', 'daily_limit'):
                try:
                    self.app.cfg[key] = int(value)
                except ValueError:
                    messagebox.showerror('Ошибка', 'Поле «{}» должно быть числом'.format(key))
                    return
            else:
                self.app.cfg[key] = value

        low, high = config_module.delay_range(self.app.cfg)
        self.app.cfg['min_delay_seconds'], self.app.cfg['max_delay_seconds'] = low, high
        config_module.save(self.app.cfg)
        self.app.log('Настройки сохранены.', 'ok')
        if low < 15:
            self.app.log('Пауза меньше 15 с — Telegram это не любит.', 'warn')
        self.app.update_status()
        self.top.destroy()

    def instructions(self):
        self.top.destroy()
        self.app.show_onboarding()

    def open_folder(self):
        subprocess.Popen(['open', str(DATA_DIR)])

    def reset_progress(self):
        items, label = self.app.current_contacts()
        if items is None:
            messagebox.showinfo('Нечего сбрасывать', 'Сначала выберите источник контактов.')
            return
        sources = sorted({c.source for c in items})
        if not messagebox.askyesno(
                'Сброс',
                'Забыть, кому уже сделаны черновики, для «{}»?\n\n'
                'После этого они снова попадут в рассылку.'.format(label)):
            return
        for source in sources:
            progress.reset_done(source)
        self.app.log('Отметки сброшены для «{}».'.format(label), 'ok')
        self.app.update_status()


def run():
    root = tk.Tk()
    app = App(root)
    root.protocol('WM_DELETE_WINDOW', app.on_close)
    root.mainloop()


if __name__ == '__main__':
    run()
