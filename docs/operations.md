# Автономная эксплуатация

## Три уровня

1. **Локальный smoke test.** Синтетика -> анализ -> план -> вертикальный MP4, без API и моделей.
2. **Фабрика с review.** Партнёр передаёт файлы, worker обрабатывает inbox, ставит клипы в очередь, редактор проверяет и одобряет. Это рекомендуемый первый режим.
3. **Автопубликация по собственным правилам.** После проверки качества включаются auto-approval, расписание, лимиты и `--execute`. Требуются права, реальные OAuth и контроль отказов. Скоринг не доказывает качество эпизода.

## Постоянный inbox worker

Скопируйте собственные записи или согласованные файлы партнёра в `data/inbox`. Передавайте их атомарным переименованием после завершения копирования. Одна папка worker относится к одному `source-id` и его разрешению; для разных правообладателей запустите отдельные worker/state DB.

```bash
mkdir -p data/inbox
python scripts/watch_inbox.py --inbox data/inbox --config config.local.json --source-id your-owned-source --asr
```

Флаг `--asr` требует установленного WhisperX. Для тихого gameplay лёгкий режим может работать без него, но распознавание событий остаётся эвристическим. Worker ждёт стабильности файла, хеширует его, пишет manifest обработки и не рендерит тот же файл повторно. Выводы остаются в папке по SHA. После аварии незаконченный рендер нужно проверить и вынести его папку перед повтором; бесконтрольного retry нет. Запускайте один inbox worker на пару inbox/state DB; он не является распределённым планировщиком.

Чтобы только обрабатывать файлы, не добавляйте `automation.accounts`. Чтобы ставить их в YouTube очереди, дополните приватный конфиг:

```json
{
  "automation": {
    "accounts": ["youtube-main", "youtube-second"],
    "privacy": "private",
    "caption": "Авторский разбор. Источник: согласованный партнёр.",
    "auto_approve": false,
    "min_score": 80
  }
}
```

Это фрагмент основного конфига, не его замена. Для auto-approval нужны одновременно `auto_approve: true` и `--auto-approve`. Порог соответствует шкале glue, а не upstream «virality score». `--execute` разрешает внешнюю загрузку уже одобренных и наступивших по времени задач. Здесь эта команда не запускалась с настоящими аккаунтами.

```bash
# Разовый проход, без внешних загрузок:
python scripts/watch_inbox.py --inbox data/inbox --config config.local.json --source-id your-owned-source --once

# После настройки и проверки владельцем, для боевого сервера:
python scripts/watch_inbox.py --inbox data/inbox --config config.local.json --source-id your-owned-source --asr --auto-approve --execute
```

Inbox worker ставит в очередь YouTube. Для Instagram нужен уникальный публичный media URL каждого файла, поэтому используйте явную команду queue add или допишите delivery adapter своего object storage. Очередь поддерживает несколько аккаунтов обеих площадок; автоматически выгружать файлы в чужое облако эта поставка не обещает.

Для непрерывной работы используйте launchd на macOS или systemd на Linux, WorkingDirectory=корень репозитория, Python из `.venv`, секреты в приватной среде. Не открывайте токены в командной строке, логах и service-файлах репозитория. Автозапуск на компьютере пользователя здесь не установлен.

## Очередь и расписание

```bash
viceclip queue add output/clips/clip-01.mp4 --config config.local.json --source-id your-owned-source --account youtube-main --title 'Авторский разбор эпизода' --privacy private --schedule '2026-11-20T12:00:00+03:00'
viceclip queue approve 1 --db data/publish.sqlite3
viceclip queue run --config config.local.json --db data/publish.sqlite3
```

Имя MP4 возьмите из результата своего запуска. Расписание хранится в UTC, ввод требует timezone. Одна и та же кодировка на том же аккаунте дедуплицируется по SHA; на другом аккаунте это отдельная задача. Редакционная дедупликация между изменёнными версиями и похожими эпизодами требует дополнительного semantic/perceptual fingerprint. Массовое копирование одних роликов по каналам не становится оригинальным контентом.

`pending` требует approval, `approved` ждёт срока/лимита, `uploading` удерживает задачу, неоднозначный исход переходит в `unknown`. `uploaded` означает приём YouTube, `published` - ответ Instagram; `playback_verified` остаётся false. API receipt не подтверждает доступность и успешную обработку видео у зрителя. После network timeout не создавайте дубль. Сверьте existing session/container и кабинет платформы, затем используйте `queue reconcile ID --remote-id VERIFIED_ID`. Reconcile - подтверждение оператора, а не самостоятельная проверка API.

## Скорость и затраты

Сначала уменьшайте объём: быстрые признаки по всему VOD, подробная модель только на кандидатах, ASR один раз, cache по input SHA, один master encode, разные account jobs поверх готового файла. Не перекодируйте весь многочасовой поток для каждого кандидата. Для live-speed нужен chunk ingest с overlap и watermark; текущий local pipeline обрабатывает готовый файл, не гарантирует near-live.

Измеряйте ingest latency, input minutes, ASR RTF, render seconds/clip, model cost, число годных кандидатов и ручные минуты review. Чистая экономика: выручка интеграций + реально доступная платформенная монетизация - источники/права - GPU/API - хранение/трафик - редактор - налоги. Значения вводите из своего пилота; прогноз дохода по количеству загруженных роликов не является расчётом.

## Поддержка

Ежедневно: квоты, истечение токенов, застрявшие uploading/unknown, ошибки media processing, права/отзывы, свободный диск. Еженедельно: бэкап SQLite при остановленном worker, разрешённая аналитика каждого владельца, review качества, проверка upstream advisories. Обновления upstream делайте отдельной веткой, меняйте lock и прогоняйте fixture заново. Автоматическое обновление до latest в production отключено самим принципом SHA pins.
