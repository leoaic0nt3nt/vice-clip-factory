# Запуск с нуля

## 1. Забрать мегарепозиторий вместе с исходниками

```bash
git clone --recurse-submodules --shallow-submodules https://github.com/leoaic0nt3nt/vice-clip-factory.git
cd vice-clip-factory
python3 scripts/verify_upstream.py
```

Здесь девять настоящих Git submodules. Они закреплены по SHA, а не плавающей ветке. Обычный GitHub Download ZIP не включает их содержимое. Если забрали ZIP, используйте Git clone выше; если клонировали без флага, выполните `git submodule update --init --recursive --depth 1`.

Проверьте свободное место: полные исходники могут занимать несколько гигабайт; GPU-зависимости и веса моделей требуют дополнительного места. Для лёгкого пути достаточно исходников glue и системного FFmpeg; Remotion и ML-движки не нужны для первого теста. Можно инициализировать только `upstream/yt-dlp` и использовать bootstrap с `--skip-submodules`.

## 2. Лёгкий рабочий профиль

Python 3.12 рекомендуется. Установите FFmpeg: macOS `brew install ffmpeg`, Linux - пакет FFmpeg вашей системы. Затем:

```bash
python3 scripts/bootstrap.py --profile core
source .venv/bin/activate
viceclip doctor
python scripts/make_demo.py
viceclip run --input examples/demo-source.mp4 --source-id demo --config examples/config.json --transcript examples/transcript.json --top 1 --output-dir output/first-run
```

`make_demo.py` создаёт собственную синтетическую запись для проверки; путь сверяйте с выводом команды. Это не GTA VI. `run` выдаёт MP4, ASS-субтитры, план и JSON с исходными интервалами. Рендер отказывается перезаписывать существующие результаты: для следующего запуска выберите новую папку.

Без установки пакета: `PYTHONPATH=src python3 -m viceclip.cli doctor`. Основное ядро использует стандартную библиотеку и небольшой Pillow для растровых текстовых слоёв. Видео обрабатывает FFmpeg с libx264/overlay; ASS сохраняется как редактируемый sidecar. Если сборка не содержит libass, используется Pillow fallback. Отсутствие необязательных WhisperX, Auto-Editor или scene detector не мешает лёгкому тесту.

## 3. Готовые инструменты по этапам

```bash
# Опциональные границы сцен, в основной venv:
python3 scripts/bootstrap.py --profile scenes
viceclip scenes examples/demo-source.mp4 --output-dir output/scenes

# WhisperX в отдельной среде, чтобы не смешивать GPU зависимости:
python3 scripts/bootstrap.py --profile asr
viceclip asr examples/demo-source.mp4 --device cpu --output-dir output/asr

# Готовый игровой движок Medusa, отдельная ML-среда:
python3 scripts/bootstrap.py --profile medusa
.tools/medusa/bin/python scripts/run_medusa.py examples/demo-source.mp4 --config examples/config.json --source-id demo --out output/medusa --clips 1
```

Установка зависимостей и первый ASR могут скачать большие веса. CUDA/cuDNN, ускорение Apple Silicon, совместимость PyTorch и лицензии моделей проверяются отдельно. Исходные SHA закреплены, но `pip`-зависимости всех проектов не являются единым универсальным lockfile для любого GPU. В этой поставке тяжёлые ML-профили проверены по исходникам, без их полного запуска.

Medusa bridge по умолчанию отключает LLM, ASR и генерацию обложек. `--captions` включает локальный ASR. `--allow-cloud` явно разрешает использование внешней модели через `LLM_API_KEY`; текст и кадры могут уйти провайдеру, возникнут расходы. Названия моделей и цены выбираются по текущему каталогу провайдера. Не используйте upstream оценки стоимости как гарантированную цену.

Текущий Auto-Editor написан на Nim. Установка - по [его официальной инструкции](https://auto-editor.com/installing); не пытайтесь `pip install -e upstream/auto-editor` для этой ревизии. После установки:

```bash
viceclip autocut examples/demo-source.mp4 --output output/first-pass.mp4
```

Повторно расшифруйте результат: после удаления пауз старые таймкоды относятся к старому файлу. В gameplay тихая сцена может быть важной: базовый путь режет лишь защищённые правилами паузы. Remotion включён исходниками как опциональный renderer для отдельного React-проекта; MVP рендерит FFmpeg и не требует сборки монорепозитория Remotion.

## 4. Разрешённые источники и discovery

Скопируйте `examples/config.json` в `config.local.json`. Не включайте боевые каналы в публичный пример. Для каждого источника укажите автора, ссылку на доказательство разрешения, срок и площадки; настройте разрешённый способ получения медиа. Согласие стримера, условия платформы и коммерческие права на игру/музыку - разные основания.

В `creators` включите только согласованных авторов, задайте YouTube channel ID или Twitch broadcaster ID. API key YouTube и Twitch client ID/token передаются через env с именами из конфигурации. Команда получает метаданные, а не подтверждает права:

```bash
viceclip discover --config config.local.json --lookback-hours 24 --output output/discovered.json
```

`--merge-config` переносит в sources только результаты с явно выданным channel-wide grant. Новые неизвестные авторы не получают разрешение автоматически. YouTube Data API не даёт права скачивать медиа; основной рабочий маршрут - исходник от партнёра в локальном inbox/хранилище. Универсальная загрузка чужих Twitch VOD через Helix тоже не обещается. Подробнее: [исследование платформ](platform-research.md).

## 5. YouTube OAuth и несколько каналов

В Google Cloud создайте приложение и включите YouTube Data API v3. Настройте OAuth consent и Desktop OAuth client. Для upload и проверки целевого канала запросите scopes `youtube.upload` и `youtube.readonly`. Для каждого канала отдельно пройдите разрешение владельца и сохраните выданный refresh token в приватном файле. Не публикуйте client secret и token в GitHub.

В `accounts` у каждого канала свои `id`, `remote_id`, `token_env` и/или `oauth_token_file`. Publisher проверяет соответствие OAuth канала указанному ID. API key для этого не подходит. Формат приватного файла для автоматического refresh:

```json
{
  "client_id": "YOUR_DESKTOP_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET",
  "refresh_token": "YOUR_ACCOUNT_REFRESH_TOKEN",
  "token_uri": "https://oauth2.googleapis.com/token",
  "expires_at": 0
}
```

Создайте `.secrets/`, сохраните файл как `.secrets/youtube-main.json` и установите `chmod 600 .secrets/youtube-main.json`. Glue обновляет Google access token через refresh endpoint. Переменная `token_env`, если задана, имеет приоритет над файлом: просроченный env token нужно заменить/удалить. Создание consent screen, OAuth consent и прохождение verification не автоматизированы этой сборкой. Квоты смотрите в своём проекте, не умножайте приложения для обхода лимитов.

## 6. Instagram OAuth и несколько профилей

Нужен professional Business/Creator профиль, соответствующее Meta-приложение и разрешение на content publishing. Выберите один маршрут Login и используйте совместимые host, scopes, ID и токен. Для Instagram Login обычно нужны `instagram_business_basic` и `instagram_business_content_publish`. Обслуживание сторонних пользователей может требовать App Review/Business Verification. Не используйте пароль аккаунта или неофициальный browser-login publisher.

Каждый аккаунт имеет свой `remote_id` и env token. Пример API base в конфиге закреплён, но его нужно сверить перед запуском. Для Reels нужен публичный HTTPS MP4 URL: localhost и приватный локальный файл для Meta недоступны. Пока автоматическая доставка файлов в object storage не включена: разместите файл сами или подключите своё хранилище. URL должен указывать на тот же проверенный MP4, сохраняться достаточно долго и не попадать с секретной подписью в public repo.

Publisher создаёт container, ждёт FINISHED и вызывает media_publish. Он читает фактический content_publishing_limit. Long-lived Meta token и отзыв разрешений требуют отдельного сопровождения; вечный токен не обещается. Sponsored Instagram jobs заблокированы до реализации проверенного partnership flow; для контекста РФ реклама в Instagram имеет дополнительный запрет.

## 7. Проверка без публикации

```bash
python -m unittest discover -s tests -v
python scripts/verify_upstream.py
viceclip queue list --db data/publish.sqlite3
viceclip queue run --db data/publish.sqlite3 --config config.local.json
```

Последняя команда без `--execute` не вызывает загрузки. Для реального первого теста задайте YouTube privacy `private`, проверьте целевой аккаунт, разрешения, файл и очередь. Live upload в рамках подготовки этого репозитория не выполнялся.
