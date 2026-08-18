# -*- coding: utf-8 -*-
"""Страница скачивания из тех же чисел, что и манифест."""
import datetime
import pathlib
import sys

version, digest, size, notes = sys.argv[1:5]
src_size = sys.argv[5] if len(sys.argv) > 5 else '0'
page = pathlib.Path('packaging/site/index.html.tpl').read_text(encoding='utf-8')
MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
          'августа', 'сентября', 'октября', 'ноября', 'декабря')
today = datetime.date.today()
for mark, value in (('__VERSION__', version), ('__SHA__', digest),
                    ('__SIZE__', '{:.1f}'.format(int(size) / 1048576)),
                    ('__DATE__', '{} {}'.format(today.day, MONTHS[today.month - 1])),
                    ('__SRCSIZE__', str(round(int(src_size) / 1024))),
                    ('__NOTES__', notes or 'Очередная сборка.')):
    page = page.replace(mark, value)
sys.stdout.write(page)
