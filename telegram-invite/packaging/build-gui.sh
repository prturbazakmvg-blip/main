#!/bin/bash
# Сборка оконной версии под macOS. Запуск: bash packaging/build-gui.sh
#
# Две разные папки, и путать их нельзя:
#   release/     — чистая, только из неё делается zip; после упаковки стирается;
#   package-gui/ — рабочая копия на этой машине. Скрипт её не удаляет.
#
# Ключи, сессия и прогресс лежат не рядом с приложением, а в
# ~/Library/Application Support/TelegramInvite — общие для всех копий.
set -e
cd "$(dirname "$0")/.."

APP="Черновики приглашений.app"

if [ ! -d .venv-build ]; then
  python3 -m venv .venv-build
fi
# shellcheck disable=SC1091
source .venv-build/bin/activate
pip install --upgrade pip
pip install -r requirements-build.txt

python -c "import tkinter" 2>/dev/null || {
  echo ""
  echo "В этом Python нет tkinter. Для Homebrew-питона нужно один раз выполнить:"
  echo "    brew install python-tk@3.13"
  exit 1
}

python - <<'PY'
import tkinter, sys
if float(tkinter.TkVersion) < 9.0:
    sys.exit('Tcl/Tk {} — эмодзи в тексте поедут. Нужен Tk 9: brew install python-tk@3.13'
             .format(tkinter.TkVersion))
PY

# --- ключ OpenRouter вшивается в сборку, чтобы коллегам не заводить свой.
# Берётся из переменной OPENROUTER_KEY или из локальных настроек. В
# репозитории файла нет: он живёт ровно на время сборки.
KEY_FILE=bundled_openrouter.key
rm -f "$KEY_FILE"
if [ -n "$OPENROUTER_KEY" ]; then
  printf '%s' "$OPENROUTER_KEY" > "$KEY_FILE"
else
  python packaging/read_key.py > "$KEY_FILE" || true
fi
if [ -s "$KEY_FILE" ]; then
  echo "Ключ OpenRouter будет вшит в сборку: $(cut -c1-14 "$KEY_FILE")..."
else
  rm -f "$KEY_FILE"
  echo "Ключа OpenRouter нет — коллеги введут свой в «Настройках»"
fi

pyinstaller --clean --noconfirm telegram_invite_gui.spec
rm -f "$KEY_FILE"          # в исходниках ключу не место

# --- архив для раздачи: ничего личного внутрь не попадает
rm -rf release
mkdir -p release
cp -R "dist/$APP" release/
cp template.txt release/
cp docs/README-macos.txt "release/Прочитай меня.txt"

# Ad-hoc подпись: на Apple Silicon без неё приложение не запустится
codesign --force --deep --sign - "release/$APP"
codesign --verify --strict "release/$APP"

if find release -name 'credentials.json' -o -name '*.session' -o -name 'state' | grep -q .; then
  echo "СТОП: в архив попали личные файлы"; exit 1
fi

rm -f telegram-invite-macos-gui.zip
(cd release && zip -qr -y ../telegram-invite-macos-gui.zip .)

# --- рабочая копия: обновляем только само приложение
mkdir -p package-gui
rm -rf "package-gui/$APP"
cp -R "release/$APP" package-gui/
[ -f package-gui/template.txt ] || cp template.txt package-gui/

# Лишние копии .app сбивают с толку: Finder и Spotlight запускают любую.
# Оставляем ровно одну — рабочую.
rm -rf "release/$APP" "dist/$APP"
rmdir release 2>/dev/null || true

# --- ставим в ~/Applications
# Пока .app лежит в Загрузках, macOS при каждом запуске спрашивает доступ
# к папке, и после пересборки спрашивает заново: подпись меняется, и система
# считает приложение новым. В ~/Applications этого нет, и права там не нужны.
INSTALLED="$HOME/Applications/$APP"
mkdir -p "$HOME/Applications"
rm -rf "$INSTALLED"
cp -R "package-gui/$APP" "$HOME/Applications/"

echo ""
echo "Готово."
echo "  запускать:      ~/Applications/$APP"
echo "  раздавать:      telegram-invite-macos-gui.zip"
echo "  рабочая копия:  package-gui/  (ключи, сессия и прогресс не тронуты)"
