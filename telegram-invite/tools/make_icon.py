# -*- coding: utf-8 -*-
"""Иконка приложения: бумажный самолётик поверх листа черновика.

Смысл прямой: программа не отправляет, а кладёт черновик — поэтому лист
с текстом на первом плане, а самолётик серый и в стороне, он ещё не
полетел. Рисуем кодом, чтобы иконку можно было пересобрать и подправить,
а не хранить бинарник, про который никто не помнит, как он сделан.

    python tools/make_icon.py          # assets/icon.icns и assets/icon.png
"""
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'assets'

# Рисуем крупно и уменьшаем — так края выходят гладкими без сглаживания
# вручную. 1024 — самый большой размер, который просит macOS.
SIZE = 1024
SCALE = 4
BIG = SIZE * SCALE

# Синий Telegram сверху вниз, как в системных иконках
TOP = (64, 169, 226)
BOTTOM = (28, 122, 190)
PAPER = (255, 255, 255)
LINE = (176, 196, 210)
PLANE = (222, 240, 251)
PLANE_SHADE = (176, 213, 238)


def rounded_gradient(size, radius):
    """Скруглённый квадрат с вертикальной заливкой."""
    base = Image.new('RGB', (1, size), TOP)
    for y in range(size):
        k = y / max(1, size - 1)
        base.putpixel((0, y), tuple(
            int(TOP[i] + (BOTTOM[i] - TOP[i]) * k) for i in range(3)))
    gradient = base.resize((size, size))

    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1),
                                           radius=radius, fill=255)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(gradient, (0, 0), mask)
    return out


def draw():
    img = rounded_gradient(BIG, int(BIG * 0.225))
    d = ImageDraw.Draw(img)
    u = BIG / 100.0            # единица = 1% стороны, чтобы считать в долях

    # Самолётик — позади листа и приглушённый: сообщение ещё не отправлено,
    # оно лежит черновиком. Складывается из двух треугольников, как обычный
    # бумажный: широкое крыло и отогнутый хвост потемнее.
    def poly(points, fill):
        d.polygon([(x * u, y * u) for x, y in points], fill=fill)

    poly([(30, 44), (92, 16), (56, 56)], PLANE)          # крыло
    poly([(56, 56), (92, 16), (52, 82)], PLANE_SHADE)    # хвост в тени

    # Лист черновика
    left, top, right, bottom = 10 * u, 34 * u, 56 * u, 88 * u
    fold = 12 * u
    d.polygon([(left, top), (right - fold, top), (right, top + fold),
               (right, bottom), (left, bottom)], fill=PAPER)
    # загнутый угол
    d.polygon([(right - fold, top), (right, top + fold),
               (right - fold, top + fold)], fill=(226, 238, 246))

    # Строки текста: последняя короче — черновик не дописан
    widths = (0.80, 0.86, 0.72, 0.86, 0.40)
    y = top + 11 * u
    for index, width in enumerate(widths):
        x0 = left + 6 * u
        x1 = x0 + (right - left - 12 * u) * width
        d.rounded_rectangle((x0, y, x1, y + 3.4 * u), radius=1.7 * u,
                            fill=LINE if index else (120, 146, 166))
        y += 8.2 * u

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main():
    ASSETS.mkdir(exist_ok=True)
    icon = draw()
    png = ASSETS / 'icon.png'
    icon.save(png)

    iconset = ASSETS / 'icon.iconset'
    if iconset.exists():
        for item in iconset.iterdir():
            item.unlink()
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        icon.resize((size, size), Image.LANCZOS).save(
            iconset / 'icon_{0}x{0}.png'.format(size))
        icon.resize((size * 2, size * 2), Image.LANCZOS).save(
            iconset / 'icon_{0}x{0}@2x.png'.format(size))
    icns = ASSETS / 'icon.icns'
    subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', str(icns)],
                   check=True)
    for item in iconset.iterdir():
        item.unlink()
    iconset.rmdir()
    print('готово: {} и {}'.format(png.name, icns.name))


if __name__ == '__main__':
    sys.exit(main())
