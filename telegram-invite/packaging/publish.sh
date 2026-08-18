#!/bin/bash
# Выкладывает свежую сборку на сервер обновлений.
#
#   bash packaging/publish.sh "что нового одной строкой"
#
# Собирает приложение, кладёт архив на tgsoft.fi.leadget.ru и обновляет
# latest.json — после этого все запущенные копии предложат обновиться сами.
#
# Раздача открыта: страница и архив доступны по ссылке без логина. В архиве
# лежит вшитый ключ OpenRouter — держите на нём лимит расходов, потолок
# убытка при утечке равен этому лимиту.
set -euo pipefail
cd "$(dirname "$0")/.."

NOTES="${1:-}"
HOST=rdpuser@fi.leadget.ru
REMOTE=/var/www/tgsoft/telegram-invite
BASE_URL=https://tgsoft.fi.leadget.ru/telegram-invite

VERSION=$(python3 -c "import re,pathlib;print(re.search(r\"'([^']+)'\", pathlib.Path('app/__init__.py').read_text()).group(1))")
echo "Версия: $VERSION"

if ssh "$HOST" "test -f $REMOTE/telegram-invite-$VERSION.zip"; then
  echo "На сервере уже лежит $VERSION. Поднимите номер версии в app/__init__.py"
  exit 1
fi

bash packaging/build-gui.sh

ARCHIVE=telegram-invite-macos-gui.zip
[ -f "$ARCHIVE" ] || { echo "Нет $ARCHIVE — сборка не удалась"; exit 1; }
SUM=$(shasum -a 256 "$ARCHIVE" | cut -d' ' -f1)
SIZE=$(stat -f%z "$ARCHIVE")
echo "sha256: $SUM  ($SIZE байт)"

# Проверяем, что версия внутри архива совпадает с заявленной: перепутать
# легко, а обновление зациклится — программа будет ставить то же самое.
INSIDE=$(unzip -p "$ARCHIVE" "*/Contents/Info.plist" | plutil -extract CFBundleShortVersionString raw -o - -- -)
[ "$INSIDE" = "$VERSION" ] || { echo "В архиве версия $INSIDE, а ждали $VERSION"; exit 1; }

ssh "$HOST" "mkdir -p $REMOTE"
scp -q "$ARCHIVE" "$HOST:$REMOTE/telegram-invite-$VERSION.zip"
# Постоянный адрес для установки одной строкой: в команде не должно быть
# номера версии, иначе её придётся переписывать после каждой сборки.
ssh "$HOST" "cp -f $REMOTE/telegram-invite-$VERSION.zip /var/www/tgsoft/latest.zip"

python3 - "$VERSION" "$SUM" "$SIZE" "$NOTES" <<'PY' > /tmp/latest.json
import json, sys
version, digest, size, notes = sys.argv[1:5]
print(json.dumps({
    'version': version,
    'url': 'https://tgsoft.fi.leadget.ru/telegram-invite/telegram-invite-{}.zip'.format(version),
    'sha256': digest,
    'size': int(size),
    'notes': notes,
}, ensure_ascii=False, indent=2))
PY
scp -q /tmp/latest.json "$HOST:$REMOTE/latest.json"
rm -f /tmp/latest.json

# Страница скачивания собирается из тех же чисел, что и манифест: иначе на
# сайте будет одна версия, а обновление принесёт другую.
bash packaging/source.sh
SRC=telegram-invite-source.zip
SRCSIZE=$(stat -f%z "$SRC")
scp -q "$SRC" "$HOST:/var/www/tgsoft/source.zip"

python3 packaging/render_site.py "$VERSION" "$SUM" "$SIZE" "$NOTES" "$SRCSIZE" > /tmp/tgsoft-index.html
scp -q /tmp/tgsoft-index.html "$HOST:/var/www/tgsoft/index.html"
scp -q assets/icon.png "$HOST:/var/www/tgsoft/icon.png"
rm -f /tmp/tgsoft-index.html
echo "Страница:  https://tgsoft.fi.leadget.ru/"
ssh "$HOST" "chmod 644 $REMOTE/* && ls -la $REMOTE"

echo
echo "Выложено: $BASE_URL/telegram-invite-$VERSION.zip"
echo "Манифест: $BASE_URL/latest.json"
echo "Запущенные копии предложат обновиться при следующем запуске."
