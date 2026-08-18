<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Черновики приглашений в Telegram</title>
<style>
  :root {
    --bg: #f6f8fa; --card: #ffffff; --ink: #1f2328; --muted: #656d76;
    --line: #d8dee4; --accent: #2f81f7; --accent-ink: #ffffff;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0d1117; --card: #161b22; --ink: #e6edf3; --muted: #8b949e;
      --line: #30363d; --accent: #388bfd; --accent-ink: #ffffff;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 48px 20px 80px; background: var(--bg); color: var(--ink);
    font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  .wrap { max-width: 680px; margin: 0 auto; }
  header { display: flex; align-items: center; gap: 20px; margin-bottom: 8px; }
  header img { width: 84px; height: 84px; border-radius: 19px; flex: none; }
  h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: -0.02em; }
  .sub { color: var(--muted); margin: 0; }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    padding: 24px; margin-top: 28px;
  }
  .get { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
  a.button {
    display: inline-block; background: var(--accent); color: var(--accent-ink);
    text-decoration: none; padding: 12px 22px; border-radius: 8px;
    font-weight: 600; white-space: nowrap;
  }
  a.button:hover { filter: brightness(1.08); }
  .meta { color: var(--muted); font-size: 14px; }
  h2 { font-size: 19px; margin: 34px 0 10px; }
  h3 { font-size: 15px; margin: 22px 0 6px; color: var(--ink); }
  ol, ul { padding-left: 22px; margin: 0; }
  li { margin: 6px 0; }
  code {
    background: var(--bg); border: 1px solid var(--line); border-radius: 5px;
    padding: 1px 6px; font-size: 13px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  pre {
    background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
    padding: 12px 14px; overflow-x: auto; font-size: 13px; margin: 8px 0 0;
  }
  pre code { border: 0; padding: 0; background: none; }
  .sha { word-break: break-all; font-size: 12px; }
  footer { color: var(--muted); font-size: 13px; margin-top: 36px; }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <img src="icon.png" alt="">
    <div>
      <h1>Черновики приглашений</h1>
      <p class="sub">Раскладывает личные приглашения черновиками по диалогам Telegram.<br>
         Для macOS на Apple Silicon — M1 и новее.</p>
    </div>
  </header>

  <div class="card">
    <h2 style="margin-top:0">Установка одной строкой</h2>
    <p>Откройте «Терминал» (лупа в правом верхнем углу → наберите
       «Терминал»), вставьте строку целиком и нажмите Enter. Программа
       скачается, встанет в «Программы» и откроется.</p>
    <pre><code>[ "$(uname -m)" = arm64 ] || { echo "Нужен Mac на Apple Silicon (M1 и новее)"; exit 1; }; mkdir -p ~/Applications &amp;&amp; curl -fsSL https://tgsoft.fi.leadget.ru/latest.zip -o /tmp/ti.zip &amp;&amp; ditto -xk /tmp/ti.zip ~/Applications &amp;&amp; open -n ~/Applications/"Черновики приглашений.app"</code></pre>
    <p class="meta" style="margin-bottom:0">Это единственный способ,
       при котором macOS не задаёт ни одного вопроса: файл, скачанный
       не браузером, не помечается карантином, и проверка разработчика
       не включается. Дальше программа обновляется сама, и Терминал
       больше не понадобится никогда.</p>
  </div>

  <div class="card">
    <div class="get">
      <a class="button" href="latest.zip">Скачать __VERSION__</a>
      <span class="meta">macOS 11 и новее · Apple Silicon · __SIZE__ МБ · __DATE__</span>
    </div>
    <p class="meta">__NOTES__</p>
    <p class="meta" style="margin-bottom:0">Если качаете кнопкой, macOS
       спросит про непроверенного разработчика — как это пройти, написано
       ниже.</p>
  </div>

  <h2>Если скачали кнопкой</h2>
  <ol>
    <li>Распакуйте и перенесите приложение в «Программы».
        <b>После этого больше его не перемещайте</b>: разрешение,
        которое вы выдадите, привязано к месту.</li>
    <li>Двойной клик. macOS скажет, что не смогла проверить программу, —
        нажмите «Готово».</li>
    <li><b>Сразу же</b> откройте <b>Системные настройки →
        Конфиденциальность и безопасность</b> и пролистайте вниз: там
        появится строка про «Черновики приглашений» и кнопка
        <b>«Всё равно открыть»</b>. Кнопка живёт недолго — если ушли
        и вернулись, повторите двойной клик.</li>
    <li>Появится ещё одно окно — в нём нажмите <b>«Открыть»</b>.
        Именно оно запускает программу; если вместо этого снова щёлкнуть
        по значку в Finder, macOS может заблокировать заново.</li>
  </ol>
  <p class="meta">Правый клик → «Открыть» больше не помогает: этот способ
     Apple убрала в macOS 15. Если после всего программа всё равно
     «мигает и закрывается» — значит разрешение не прижилось; используйте
     установку одной строкой выше, она работает всегда.</p>

  <h2>Дальше обновляется сама</h2>
  <p>Программа проверяет версию при запуске и предлагает обновиться. Скачает,
     заменит себя и откроется заново — без вопросов про разработчика и без
     Терминала: обновление, скачанное самой программой, метку карантина
     не получает.</p>

  <h2>Что понадобится</h2>
  <ul>
    <li>Вход в Telegram по номеру телефона — программа проведёт по шагам.</li>
    <li>api_id и api_hash с my.telegram.org — мастер показывает по скриншотам,
        делается один раз.</li>
    <li>Ключ для разбора контактов уже вшит, свой заводить не нужно.</li>
  </ul>

  <h2>Исходный код</h2>
  <p>Программа написана на Python, собирается в .app через PyInstaller.
     Внутри — правила подготовки текста, разбор контактов и всё остальное;
     ключей и переписки в архиве нет.</p>
  <p><a href="source.zip">telegram-invite-source.zip</a> — __SRCSIZE__ КБ.
     Как собрать своё: <code>bash packaging/build-gui.sh</code>, нужен
     Python 3.13 с Tk 9 (<code>brew install python-tk@3.13</code>).</p>

  <h2 id="debug">Если что-то не работает</h2>

  <h3>Значок мигнул и погас</h3>
  <p><b>Если версия старее 2.5.6 — просто обновитесь.</b> В 2.5.6 починена
     ошибка «ValueError: not enough values to unpack»: на части компьютеров
     macOS сообщает свою версию короче обычного, и библиотека Telegram
     падала на этом ещё до появления окна.</p>
  <p><b>Шаг 1. Процессор.</b> Меню Apple → «Об этом Mac». Если там Intel,
     а не «Чип M1/M2/M3» — эта сборка не запустится вообще, нужна отдельная
     под Intel. Напишите нам, соберём.</p>
  <p><b>Шаг 2. Что сказала программа.</b> Начиная с 2.5.4 она не умирает
     молча: причину показывает окном и дописывает в файл. Откройте его —
     скопируйте строку в «Терминал»:</p>
  <pre><code>open -e ~/Library/Application\ Support/TelegramInvite/state/crash.txt</code></pre>
  <p><b>Шаг 3. Запуск с выводом.</b> Если файла нет — значит программа
     не дошла даже до перехвата. Запустите её из «Терминала», ошибка
     напечатается прямо в окне:</p>
  <pre><code>~/Applications/"Черновики приглашений.app"/Contents/MacOS/telegram-invite-gui</code></pre>
  <p class="meta">Если приложение лежит в общей папке «Программы», замените
     <code>~/Applications</code> на <code>/Applications</code>.</p>

  <h3>Программа не пускает при запуске</h3>
  <p>macOS считает её непроверенной, потому что она не куплена в App Store.
     Снять метку карантина с уже установленной копии — одна строка,
     подставлять ничего не надо:</p>
  <pre><code>xattr -dr com.apple.quarantine ~/Applications/"Черновики приглашений.app" /Applications/"Черновики приглашений.app" 2>/dev/null; echo готово</code></pre>
  <p class="meta">Надёжнее всего этого избежать — ставить программу
     установкой одной строкой сверху страницы: тогда карантина не будет
     вовсе.</p>

  <h3>Не входит в Telegram</h3>
  <ul>
    <li>«api_id и api_hash не подходят друг к другу» — ключи скопированы
        с ошибкой. «Настройки» → «Показать инструкцию», там по скриншотам,
        где их взять на my.telegram.org.</li>
    <li>«Слишком много попыток входа» — Telegram придержал номер. Это
        проходит само, обычно за час; чаще входить не стоит.</li>
    <li>Код приходит в само приложение Telegram, в чат «Telegram», а не
        по SMS.</li>
  </ul>

  <h3>Не разбирает контакты или не дорабатывает текст</h3>
  <p>Первая строка в журнале программы говорит, откуда взялся ключ
     OpenRouter. Если там «Ключа OpenRouter нет» — сборка скачана
     не отсюда; переустановите по ссылке с этой страницы.</p>

  <h3>Всё встало посреди рассылки</h3>
  <ul>
    <li>«Telegram просит подождать N с» — это нормально, программа ждёт
        сама и продолжает.</li>
    <li>«Связь с Telegram оборвалась» — программа трижды пробует
        переподключиться и только потом останавливается. Созданные
        черновики уже отмечены, следующий запуск продолжит с того же места.</li>
    <li>«PEER_FLOOD» — аккаунт придержали за массовые обращения. Рассылку
        нужно прекратить на несколько дней и написать в @SpamBot.</li>
  </ul>

  <h3>Что прислать, если ничего не помогло</h3>
  <p>Три файла из папки с настройками — в них нет ни переписки, ни ключей:</p>
  <pre><code>open ~/Library/Application\ Support/TelegramInvite/state</code></pre>
  <ul>
    <li><code>crash.txt</code> — если есть;</li>
    <li><code>log.txt</code> — журнал последних запусков;</li>
    <li>версию программы: «Настройки» → там же кнопка «Проверить обновления».</li>
  </ul>

  <footer>
    Программа ничего не отправляет: она кладёт текст черновиком в поле ввода
    диалога, отправляете вы руками.<br>
    sha256 <span class="sha">__SHA__</span>
  </footer>

</div>
</body>
</html>
