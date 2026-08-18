#!/bin/bash
# Архив с исходным кодом для сайта.
#
#   bash packaging/source.sh   ->  telegram-invite-source.zip
#
# Кладём только то, что написано руками. Рядом с проектом лежат рабочие
# файлы — вход в Telegram, выгруженные контакты, собранные приложения с
# вшитым ключом. Ни одно из этого в архив попасть не должно, поэтому
# берём список файлов явным перечислением, а не «всё кроме».
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=telegram-invite-source.zip
rm -f "$OUT"

INCLUDE=(app tools tests packaging docs assets/icon.png assets/onboarding
         .github .gitignore README.md requirements.txt requirements-build.txt
         run.py run_gui.py create_messages.py template.txt
         telegram_invite.spec telegram_invite_gui.spec)

zip -qr "$OUT" "${INCLUDE[@]}" \
    -x '*/__pycache__/*' '*.pyc' '*/.DS_Store' '*/.pytest_cache/*'

# Проверяем, что внутрь не заехало лишнее. Дешевле остановить сборку, чем
# потом отзывать выложенный архив.
BAD=$(unzip -Z1 "$OUT" | grep -Ei 'credentials\.json|\.session|bundled_openrouter|contacts\.json|reactions\.json|dataset\.json|edits\.jsonl|/state/|/dist/|/build/|/release/|/package' || true)
if [ -n "$BAD" ]; then
  echo "В архив попало лишнее:"; echo "$BAD"; rm -f "$OUT"; exit 1
fi

# И что внутри нет ключей. Сами значения берём из локальных настроек и
# нигде не печатаем.
python3 - "$OUT" <<'PY'
import json, pathlib, subprocess, sys, zipfile
cfg_path = (pathlib.Path.home() / 'Library/Application Support'
            / 'TelegramInvite/credentials.json')
secrets = []
if cfg_path.exists():
    cfg = json.loads(cfg_path.read_text())
    for field in ('openrouter_key', 'api_hash', 'api_id'):
        value = str(cfg.get(field) or '').strip()
        if len(value) >= 6:
            secrets.append((field, value))
found = []
with zipfile.ZipFile(sys.argv[1]) as z:
    for name in z.namelist():
        if name.endswith('/'):
            continue
        blob = z.read(name)
        for field, value in secrets:
            if value.encode() in blob:
                found.append('{} -> {}'.format(name, field))
if found:
    print('В архиве нашлись секреты:', *found, sep='\n  ')
    sys.exit(1)
print('Секретов внутри нет (проверено полей: {})'.format(len(secrets)))
PY

echo "Готово: $OUT ($(du -h "$OUT" | cut -f1), файлов $(unzip -Z1 "$OUT" | wc -l | tr -d ' '))"
