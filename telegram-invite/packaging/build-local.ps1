# Локальная сборка под Windows.
# Запуск в PowerShell: powershell -ExecutionPolicy Bypass -File packaging\build-local.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

python -m venv .venv-build
& .\.venv-build\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-build.txt

pyinstaller --clean --noconfirm telegram_invite.spec

Remove-Item -Recurse -Force package -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path package | Out-Null
Copy-Item dist\telegram-invite.exe package\
Copy-Item template.txt package\
Copy-Item docs\README-windows.txt "package\Прочитай меня.txt"

Compress-Archive -Path package\* -DestinationPath telegram-invite-windows.zip -Force
Write-Host ""
Write-Host "Готово: telegram-invite-windows.zip"
