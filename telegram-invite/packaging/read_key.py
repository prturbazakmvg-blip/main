# -*- coding: utf-8 -*-
"""Печатает ключ OpenRouter из локальных настроек — для сборки.

Отдельным файлом, а не строкой внутри bash: ключ не должен мелькать ни в
аргументах команды, ни в истории оболочки.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.paths import CREDENTIALS_PATH        # noqa: E402

try:
    with open(CREDENTIALS_PATH, encoding='utf-8') as f:
        sys.stdout.write(str(json.load(f).get('openrouter_key', '')).strip())
except (OSError, ValueError):
    pass
