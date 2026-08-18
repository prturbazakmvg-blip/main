#!/bin/bash
# Двойной клик по этому файлу открывает Терминал и запускает программу.
cd "$(dirname "$0")" || exit 1

# Снимаем карантин, который macOS вешает на всё скачанное из интернета.
xattr -dr com.apple.quarantine . 2>/dev/null

if [ ! -x ./telegram-invite ]; then
  chmod +x ./telegram-invite 2>/dev/null
fi

./telegram-invite
