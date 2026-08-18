# -*- coding: utf-8 -*-
"""Мастер первого запуска: как получить api_id и api_hash.

Для каждого шага сначала ищется настоящий скриншот в assets/onboarding/.
Если файла нет — рисуется схема на Canvas, так что мастер работает и без
картинок. Скриншоты можно докладывать по одному, код менять не нужно:
размер подгоняется сам, подсветка нужных мест описана в marks_*.

На шаге 5 намеренно оставлена схема с выдуманными ключами: настоящий
api_hash на картинке внутри раздаваемого приложения — это утечка.
"""
import tkinter as tk
import webbrowser
from tkinter import ttk

from .paths import bundled_dir

BG = '#ffffff'
INK = '#1f2328'
MUTED = '#8c959f'
LINE = '#d0d7de'
ACCENT = '#2f6feb'
HILITE = '#fff3c4'
HILITE_LINE = '#d4a72c'
FIELD = '#f6f8fa'

CANVAS_W = 640
CANVAS_H = 250
# Выше этого скриншот ужимается: иначе диалог не влезет на экран
MAX_SHOT_H = 330


# --------------------------------------------------------------------------
# Кисти
# --------------------------------------------------------------------------

def rounded(canvas, x1, y1, x2, y2, radius=8, **kw):
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kw)


def browser(canvas, x, y, w, h, url):
    """Рамка браузера с адресной строкой."""
    rounded(canvas, x, y, x + w, y + h, radius=10, fill=BG, outline=LINE, width=1)
    canvas.create_line(x, y + 34, x + w, y + 34, fill=LINE)
    for index in range(3):
        cx = x + 16 + index * 14
        canvas.create_oval(cx - 4, y + 13, cx + 4, y + 21, fill='#e6e8eb', outline='')
    rounded(canvas, x + 62, y + 9, x + w - 14, y + 26, radius=8, fill=FIELD, outline='')
    canvas.create_text(x + 72, y + 17, text=url, anchor='w', fill=MUTED, font=('Helvetica', 10))


def field(canvas, x, y, w, label, value='', highlight=False, height=30):
    if label:
        canvas.create_text(x, y - 8, text=label, anchor='sw', fill=MUTED,
                           font=('Helvetica', 10))
    rounded(canvas, x, y, x + w, y + height, radius=6,
            fill=HILITE if highlight else FIELD,
            outline=HILITE_LINE if highlight else LINE)
    if value:
        canvas.create_text(x + 10, y + height / 2, text=value, anchor='w', fill=INK,
                           font=('Helvetica', 11))


def button(canvas, x, y, text, w=None, primary=True):
    width = w or (len(text) * 7 + 26)
    rounded(canvas, x, y, x + width, y + 28, radius=6,
            fill=ACCENT if primary else FIELD,
            outline='' if primary else LINE)
    canvas.create_text(x + width / 2, y + 14, text=text,
                       fill='#ffffff' if primary else INK, font=('Helvetica', 11))
    return width


def arrow(canvas, x1, y1, x2, y2, text=''):
    canvas.create_line(x1, y1, x2, y2, fill=HILITE_LINE, width=2,
                       arrow='last', arrowshape=(10, 12, 4))
    if text:
        canvas.create_text((x1 + x2) / 2, min(y1, y2) - 12, text=text,
                           fill=HILITE_LINE, font=('Helvetica', 10, 'bold'))


def shots_dir():
    return bundled_dir() / 'assets' / 'onboarding'


def load_shot(file_name, crop=None):
    """Загружает настоящий скриншот. Возвращает None, если файла нет —
    тогда рисуется схема."""
    path = shots_dir() / file_name
    if not path.exists():
        return None
    try:
        full = tk.PhotoImage(file=str(path))
    except tk.TclError:
        return None
    if crop:
        x1, y1, x2, y2 = crop
        piece = tk.PhotoImage(width=max(1, x2 - x1), height=max(1, y2 - y1))
        piece.tk.call(piece, 'copy', full, '-from', x1, y1, x2, y2, '-to', 0, 0)
        full = piece

    # Снимок с retina-экрана приходит в двойном разрешении, а бывает и просто
    # большой. Tk умеет уменьшать только в целое число раз — этого хватает.
    factor = 1
    while (full.width() // factor > CANVAS_W or full.height() // factor > MAX_SHOT_H) \
            and factor < 4:
        factor += 1
    if factor > 1:
        full = full.subsample(factor, factor)

    return full


def frame_shot(canvas, image, x, y):
    """Скриншот в тонкой рамке, чтобы не сливался с фоном диалога."""
    w, h = image.width(), image.height()
    canvas.create_rectangle(x - 1, y - 1, x + w + 1, y + h + 1, outline=LINE)
    canvas.create_image(x, y, image=image, anchor='nw')
    return w, h


def mark(canvas, x1, y1, x2, y2, label='', side='right'):
    """Подсветка нужного места на скриншоте плюс подпись со стрелкой."""
    canvas.create_rectangle(x1, y1, x2, y2, outline=HILITE_LINE, width=2)
    if not label:
        return
    mid_y = (y1 + y2) / 2
    if side == 'right':
        canvas.create_text(x2 + 46, mid_y, text=label, anchor='w',
                           fill=HILITE_LINE, font=('Helvetica', 11, 'bold'))
        arrow(canvas, x2 + 40, mid_y, x2 + 6, mid_y)
    else:
        canvas.create_text(x1 - 46, mid_y, text=label, anchor='e',
                           fill=HILITE_LINE, font=('Helvetica', 11, 'bold'))
        arrow(canvas, x1 - 40, mid_y, x1 - 6, mid_y)


def link_row(canvas, x, y, w, text, highlight=False):
    if highlight:
        rounded(canvas, x - 6, y - 4, x + w, y + 22, radius=5,
                fill=HILITE, outline=HILITE_LINE)
    canvas.create_text(x, y + 9, text=text, anchor='w',
                       fill=ACCENT if not highlight else INK, font=('Helvetica', 11))


# --------------------------------------------------------------------------
# Шаги
# --------------------------------------------------------------------------

def draw_step1(canvas):
    browser(canvas, 60, 30, 520, 190, 'my.telegram.org')
    canvas.create_text(320, 95, text='Telegram', fill=INK, font=('Helvetica', 20, 'bold'))
    field(canvas, 200, 120, 240, '', '+7 999 123-45-67')
    button(canvas, 200, 165, 'Next', w=240)
    arrow(canvas, 470, 135, 450, 135, 'ваш номер')


def draw_step2(canvas):
    browser(canvas, 40, 30, 300, 190, 'my.telegram.org')
    canvas.create_text(190, 80, text='Enter your password', fill=INK,
                       font=('Helvetica', 12, 'bold'))
    field(canvas, 80, 110, 220, '', '• • • • •', highlight=True)
    button(canvas, 80, 160, 'Sign in', w=220)

    rounded(canvas, 390, 40, 590, 210, radius=14, fill=BG, outline=LINE)
    canvas.create_oval(404, 54, 428, 78, fill=ACCENT, outline='')
    canvas.create_text(438, 66, text='Telegram', anchor='w', fill=INK,
                       font=('Helvetica', 11, 'bold'))
    canvas.create_line(390, 90, 590, 90, fill=LINE)
    canvas.create_text(404, 112, text='Login code:', anchor='w', fill=MUTED,
                       font=('Helvetica', 10))
    canvas.create_text(404, 138, text='12345', anchor='w', fill=INK,
                       font=('Helvetica', 18, 'bold'))
    canvas.create_text(404, 172, text='Код приходит\nв само приложение', anchor='w',
                       fill=MUTED, font=('Helvetica', 10))
    arrow(canvas, 386, 130, 310, 128)


def draw_step3(canvas):
    browser(canvas, 60, 30, 520, 190, 'my.telegram.org')
    canvas.create_text(90, 70, text='Telegram core', anchor='w', fill=INK,
                       font=('Helvetica', 13, 'bold'))
    link_row(canvas, 96, 95, 220, 'API development tools', highlight=True)
    link_row(canvas, 96, 130, 220, 'Delete account or manage apps')
    link_row(canvas, 96, 160, 220, 'Log out')
    arrow(canvas, 420, 106, 330, 106, 'сюда')


def draw_step4(canvas):
    browser(canvas, 60, 20, 520, 210, 'my.telegram.org/apps')
    canvas.create_text(90, 72, text='Create new application', anchor='w', fill=INK,
                       font=('Helvetica', 12, 'bold'))
    field(canvas, 92, 100, 200, 'App title', 'invites')
    field(canvas, 316, 100, 200, 'Short name', 'invites')
    field(canvas, 92, 160, 424, 'Platform / URL', 'можно оставить как есть')
    button(canvas, 92, 200, 'Create application')


def draw_step5(canvas):
    # Значения нарочно выдуманные: настоящий api_hash в картинке внутри
    # раздаваемого приложения — это утечка ключа
    browser(canvas, 60, 14, 520, 224, 'my.telegram.org/apps')
    canvas.create_text(90, 64, text='App configuration', anchor='w', fill=INK,
                       font=('Helvetica', 12, 'bold'))
    field(canvas, 92, 102, 424, 'App api_id', '1234567', highlight=True)
    field(canvas, 92, 166, 424, 'App api_hash', 'a1b2c3d4e5f60718293a4b5c6d7e8f90',
          highlight=True)
    canvas.create_text(92, 222, text='Скопируйте оба значения — их и надо вставить ниже.',
                       anchor='w', fill=MUTED, font=('Helvetica', 10))


def marks_step1(canvas, ox, oy):
    mark(canvas, ox + 12, oy + 162, ox + 412, oy + 194, 'ваш номер')
    mark(canvas, ox + 12, oy + 232, ox + 76, oy + 272, 'затем сюда')


def marks_step3(canvas, ox, oy):
    mark(canvas, ox + 8, oy + 8, ox + 260, oy + 34, 'этот раздел')


def marks_step5(canvas, ox, oy):
    mark(canvas, ox + 8, oy + 8, ox + 420, oy + 40, 'api_id')
    mark(canvas, ox + 8, oy + 60, ox + 420, oy + 92, 'api_hash')


STEPS = [
    {
        'title': 'Шаг 1 из 5. Откройте my.telegram.org',
        'text': 'Программе нужны два ключа от Telegram. Они выдаются один раз и\n'
                'бесплатно. Откройте my.telegram.org и введите свой номер телефона —\n'
                'тот же, под которым вы сидите в Telegram.',
        'draw': draw_step1,
        'shot': 'step1-phone.png',
        'crop': (48, 268, 660, 556),
        'marks': marks_step1,
        'link': 'https://my.telegram.org',
    },
    {
        'title': 'Шаг 2 из 5. Введите код подтверждения',
        'text': 'Код придёт не в SMS, а в само приложение Telegram — в чат «Telegram».\n'
                'Впишите его в поле на сайте и нажмите Sign in.',
        'draw': draw_step2,
        'shot': 'step2-code.png',
        'crop': None,
        'marks': None,
        'link': '',
    },
    {
        'title': 'Шаг 3 из 5. Зайдите в API development tools',
        'text': 'После входа откроется список разделов. Нужен самый первый —\n'
                '«API development tools».',
        'draw': draw_step3,
        'shot': 'step3-tools.png',
        'crop': None,
        'marks': marks_step3,
        'link': '',
    },
    {
        'title': 'Шаг 4 из 5. Создайте приложение',
        'text': 'Заполните App title и Short name — подойдёт любое латинское слово,\n'
                'например invites. Остальные поля можно не трогать.\n'
                'Нажмите Create application.',
        'draw': draw_step4,
        'shot': 'step4-create.png',
        'crop': None,
        'marks': None,
        'link': '',
    },
    {
        'title': 'Шаг 5 из 5. Скопируйте ключи сюда',
        'text': 'Откроется страница с двумя значениями: App api_id (число) и\n'
                'App api_hash (длинная строка). Впишите их в поля ниже.',
        'draw': draw_step5,
        'shot': 'step5-keys.png',
        'crop': None,
        'marks': marks_step5,
        'link': '',
    },
]


# --------------------------------------------------------------------------

class Onboarding(object):
    """Возвращает (api_id, api_hash) или None, если закрыли."""

    def __init__(self, parent, api_id='', api_hash=''):
        self.result = None
        self.index = 0

        self.top = tk.Toplevel(parent)
        self.top.title('Первый запуск')
        self.top.configure(bg=BG)
        self.top.resizable(False, False)
        self.top.transient(parent)
        self.top.protocol('WM_DELETE_WINDOW', self.on_close)

        wrap = tk.Frame(self.top, bg=BG, padx=24, pady=20)
        wrap.pack(fill='both', expand=True)

        self.title_var = tk.StringVar()
        tk.Label(wrap, textvariable=self.title_var, bg=BG, fg=INK, anchor='w',
                 font=('Helvetica', 16, 'bold')).pack(fill='x')

        self.text_var = tk.StringVar()
        tk.Label(wrap, textvariable=self.text_var, bg=BG, fg=INK, anchor='w',
                 justify='left', font=('Helvetica', 12)).pack(fill='x', pady=(8, 12))

        self.canvas = tk.Canvas(wrap, width=CANVAS_W, height=CANVAS_H, bg=BG,
                                highlightthickness=0)
        self.canvas.pack()

        # Поля ключей — показываем только на последнем шаге
        self.fields = tk.Frame(wrap, bg=BG)
        self.api_id_var = tk.StringVar(value=api_id)
        self.api_hash_var = tk.StringVar(value=api_hash)

        tk.Label(self.fields, text='api_id', bg=BG, fg=INK, width=9, anchor='w',
                 font=('Helvetica', 12)).grid(row=0, column=0, pady=4)
        ttk.Entry(self.fields, textvariable=self.api_id_var, width=20).grid(
            row=0, column=1, sticky='w', pady=4)
        tk.Label(self.fields, text='api_hash', bg=BG, fg=INK, width=9, anchor='w',
                 font=('Helvetica', 12)).grid(row=1, column=0, pady=4)
        ttk.Entry(self.fields, textvariable=self.api_hash_var, width=44).grid(
            row=1, column=1, sticky='w', pady=4)

        self.error_var = tk.StringVar()
        self.error_label = tk.Label(wrap, textvariable=self.error_var, bg=BG, fg='#c0392b',
                                    anchor='w', font=('Helvetica', 11))
        self.error_label.pack(fill='x', pady=(6, 0))

        bar = tk.Frame(wrap, bg=BG)
        bar.pack(fill='x', pady=(14, 0))

        self.dots = tk.Canvas(bar, width=len(STEPS) * 18, height=14, bg=BG,
                              highlightthickness=0)
        self.dots.pack(side='left')

        self.next_button = ttk.Button(bar, text='Далее', command=self.on_next)
        self.next_button.pack(side='right')
        self.back_button = ttk.Button(bar, text='Назад', command=self.on_back)
        self.back_button.pack(side='right', padx=8)
        self.link_button = ttk.Button(bar, text='Открыть сайт', command=self.on_link)
        self.link_button.pack(side='left', padx=16)
        ttk.Button(bar, text='Пропустить', command=self.on_close).pack(side='left')

        # Enter — дальше, Esc — пропустить: мастер должен проходиться с клавиатуры
        self.top.bind('<Return>', lambda _e: self.on_next())
        self.top.bind('<KP_Enter>', lambda _e: self.on_next())
        self.top.bind('<Escape>', lambda _e: self.on_close())

        self.render()
        self.top.update_idletasks()
        self._center(parent)
        self.top.grab_set()
        self.top.focus_set()

    def _center(self, parent):
        width = self.top.winfo_width()
        height = self.top.winfo_height()
        if parent is not None and parent.winfo_viewable():
            x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - height) // 3
        else:
            x = (self.top.winfo_screenwidth() - width) // 2
            y = (self.top.winfo_screenheight() - height) // 3
        self.top.geometry('+{}+{}'.format(max(0, x), max(0, y)))

    def render(self):
        step = STEPS[self.index]
        self.title_var.set(step['title'])
        self.text_var.set(step['text'])
        self.error_var.set('')

        self.canvas.delete('all')

        shot = load_shot(step.get('shot', ''), step.get('crop')) if step.get('shot') else None
        if shot is not None:
            # ссылку держим сами, иначе картинку соберёт сборщик мусора
            self._shot = shot
            offset_x = max(10, (CANVAS_W - shot.width()) // 2)
            frame_shot(self.canvas, shot, offset_x, 10)
            if step.get('marks'):
                step['marks'](self.canvas, offset_x, 10)
            self.canvas.configure(height=max(CANVAS_H, shot.height() + 20))
        else:
            self._shot = None
            self.canvas.configure(height=CANVAS_H)
            step['draw'](self.canvas)

        self.dots.delete('all')
        for index in range(len(STEPS)):
            cx = 7 + index * 18
            filled = index <= self.index
            self.dots.create_oval(cx - 5, 2, cx + 5, 12,
                                  fill=ACCENT if filled else '#e6e8eb', outline='')

        last = self.index == len(STEPS) - 1
        if last:
            # before=: иначе поля уедут под кнопки, они упакованы раньше
            self.fields.pack(before=self.error_label, pady=(14, 0))
        else:
            self.fields.pack_forget()

        self.back_button.configure(state='disabled' if self.index == 0 else 'normal')
        self.next_button.configure(text='Сохранить и начать' if last else 'Далее')
        self.link_button.configure(state='normal' if step['link'] else 'disabled')

    def on_link(self):
        link = STEPS[self.index]['link']
        if link:
            webbrowser.open(link)

    def on_back(self):
        if self.index > 0:
            self.index -= 1
            self.render()

    def on_next(self):
        if self.index < len(STEPS) - 1:
            self.index += 1
            self.render()
            return

        api_id = self.api_id_var.get().strip()
        api_hash = self.api_hash_var.get().strip()

        if not api_id.isdigit():
            self.error_var.set('api_id — это число, например 1234567')
            return
        if len(api_hash) < 16:
            self.error_var.set('api_hash похож на обрезанный, скопируйте целиком')
            return

        self.result = (api_id, api_hash)
        self.close()

    def on_close(self):
        self.result = None
        self.close()

    def close(self):
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.destroy()


def show(parent, api_id='', api_hash=''):
    wizard = Onboarding(parent, api_id, api_hash)
    parent.wait_window(wizard.top)
    return wizard.result
