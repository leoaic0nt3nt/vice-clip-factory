# Что проверено в поставке

Дата: 16 ноября 2026. Среда: macOS Apple Silicon, Python 3.12, FFmpeg 9.0.1. Это локальные инженерные проверки, а не подтверждение прав, монетизации или подключения аккаунтов.

| Проверка | Результат |
|---|---|
| Clone девяти upstream источников | Все присутствуют; HEAD каждого совпадает с upstream.lock.json |
| Установка core из исходников | `bootstrap --profile core --skip-submodules` успешно создал .venv и установил glue, pinned yt-dlp и Pillow |
| Unit / mocked API | 29 тестов пройдено, включая timing cuts, права, wrong-channel, dedup, ambiguous uploads, лимиты и disclosure |
| Scout новых авторов | Mock-тесты: без ключей сеть не вызывается; найденные авторы всегда needs_rights без разрешения на скачивание |
| GitHub Actions | Core checks успешно выполнены на Linux: установка, unit tests и compileall; ссылка в Actions репозитория |
| Компиляция Python | compileall src/scripts без ошибок |
| Сквозной CLI run | Собственная синтетика 45s -> plan/ASS/sidecar -> вертикальный MP4 |
| Результат demo | 20.00s, 1080×1920, 30fps, H.264/yuv420p, AAC48kHz, MP4 faststart, около 3.6MiB |
| Декодирование результата | FFmpeg decode-to-null без ошибок |
| Визуальная проверка MP4 | Проверены representative frames: кириллица, хук, полный исходный кадр и учебный cobalt sponsor slot |
| Очередь | add -> approve -> dry-run; без внешнего upload |
| Inbox worker | Реальный проход на fixture, затем второй проход: в журнале один input SHA, повторной обработки нет |
| PDF | 34 страницы 600×800pt, примерно 1.5MiB; все страницы отрендерены и просмотрены, переполнений текста не найдено |
| Публичный набор | Не включает cookies, токены, договоры, оригинальные Reels или PDF-референс |

Системный FFmpeg не содержит libass/drawtext. Проверенный renderer применяет Pillow overlays и сохраняет ASS для редактирования. Это реально использованный fallback, а не предположение о возможности установки другой сборки.

Демо - [готовый MP4](../output/demo/clip-demo.mp4), [кадр результата](../assets/render-preview.png), генератор - [make_demo.py](../scripts/make_demo.py). Transcript написан для проверки тайминга; в звуке fixture тестовый тон, а не русская речь. Это технический пример, не ролик GTA и не демонстрация ASR-точности.

## Не проверено в боевой среде

- OAuth consent каждого YouTube/Instagram аккаунта и фактическая live-загрузка.
- App Review/Business Verification, квоты конкретного проекта, прохождение monetization review.
- Runtime WhisperX, PySceneDetect, Auto-Editor, Medusa, Remotion и ClippyMe с их тяжёлыми зависимостями. Их исходники клонированы, роли и интерфейсы изучены; наличие source не равно успешному запуску.
- Получение коммерческих прав на GTA VI, музыку и чужие стримы.
- Near-live latency, обработка многочасового VOD, массовая нагрузка и экономическая прибыль.

CI workflow включён в репозиторий; его удалённое исполнение зависит от доступности GitHub Actions и биллинга аккаунта. Локальное прохождение тестов не подменяет статус CI.

## Повторить

```bash
python -m unittest discover -s tests -v
python scripts/verify_upstream.py
python scripts/make_demo.py
viceclip run --input examples/demo-source.mp4 --source-id demo --config examples/config.json --transcript examples/transcript.json --top 1 --output-dir output/new-proof
ffprobe -v error -show_format -show_streams output/new-proof/clips/clip-01.mp4
ffmpeg -v error -i output/new-proof/clips/clip-01.mp4 -f null -
```

Если fixture уже существует, используйте его или задайте генератору новый `--output`. Для пересборки PDF установите зависимости из `requirements-docs.txt` и запустите `python scripts/build_guide.py`; для визуального контроля используйте Poppler `pdftoppm`.
