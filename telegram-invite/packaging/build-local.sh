#!/bin/bash
# Локальная сборка под macOS. Запуск: bash packaging/build-local.sh
set -e
cd "$(dirname "$0")/.."

python3 -m venv .venv-build
# shellcheck disable=SC1091
source .venv-build/bin/activate
pip install --upgrade pip
pip install -r requirements-build.txt

pyinstaller --clean --noconfirm telegram_invite.spec

rm -rf package
mkdir -p package
cp dist/telegram-invite package/
cp template.txt package/
cp docs/README-macos.txt "package/Прочитай меня.txt"
cp packaging/Запустить.command package/
chmod +x package/telegram-invite "package/Запустить.command"
codesign --force --deep --sign - package/telegram-invite

cd package && zip -r -y ../telegram-invite-macos.zip .
echo ""
echo "Готово: telegram-invite-macos.zip"
