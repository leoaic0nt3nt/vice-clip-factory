# Готовые репозитории: что брать в сборку

Проверено 16.11.2026 по первичным GitHub README, LICENSE и указанным файлам. Это подбор исходников и точек интеграции; чтение кода не означает, что весь upstream уже установлен и проверен на часовых стримах. Фактически склонированные ревизии смотрите в реестре `upstream` проекта. Не называйте любой source snapshot подключённым runtime.

## Рекомендованный набор

| Этап | Репозиторий | Роль в фабрике | Лицензия / ограничение |
|---|---|---|---|
| Разрешённые исходные файлы | [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) | Утилита получения конкретного разрешённого URL, metadata | Проверять LICENSE snapshot и сторонние бинарники; техническая доступность не заменяет разрешённый способ получения |
| Слова и таймкоды | [m-bain/whisperX](https://github.com/m-bain/whisperX) | ASR + word alignment | BSD-2-Clause у репозитория; лицензии моделей отдельно, ML/GPU зависимости отдельно |
| Границы сцен | [Breakthrough/PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | Scene cuts как один из сигналов | BSD-3-Clause; не semantic highlight detector |
| Черновая чистка | [WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor) | Удаление непродуктивных пауз с защищёнными диапазонами | Проверять LICENSE сохранённой ревизии; meaningful silence сохранять |
| Рекламный/редакционный дизайн | [remotion-dev/remotion](https://github.com/remotion-dev/remotion) | React captions, бренд-плашки, шаблоны | Специальная коммерческая лицензия; исходный код не равен unrestricted MIT |
| Encode | [FFmpeg/FFmpeg](https://github.com/FFmpeg/FFmpeg) | Один финальный encode, crop/stack/subtitles/normalization | LGPL/GPL зависит от конфигурации; бинарник устанавливается отдельно |
| Twitch discovery | [Teekeks/pyTwitchAPI](https://github.com/Teekeks/pyTwitchAPI) | Python wrapper официального Helix/EventSub | MIT; нужны Twitch app/OAuth, скачивание только по разрешённому маршруту |
| Gameplay highlights | [Edualnog/medusa-clip](https://github.com/Edualnog/medusa-clip) | Готовый gameplay engine audio+motion+transcript+multimodal rerank, hooks, 9:16 | AGPL-3.0; source/runtime лицензии сохранять отдельно; ключ модели для LLM этапов |
| Просмотрен, исключён из runtime набора | [ClipsAI/clipsai](https://github.com/ClipsAI/clipsai) | Transcript clips и active-speaker reframing | MIT; audio-centric, не решение молчаливого gameplay |
| Альтернатива целого автоконвейера | [fralapo/clippyme](https://github.com/fralapo/clippyme) | Live watcher, captions, speaker tracking, scheduling | MIT основного репо; автопубликация через внешний Zernio, LLM через Gemini |

Для YouTube/Instagram нет необходимости клонировать гигантский Google SDK или применять неофициальный Instagram password API. Прямые тонкие адаптеры HTTP/OAuth с явной валидацией аккаунта проще проверить; официальные endpoint contracts перечислены в [platform-research.md](platform-research.md). Если SDK всё-таки нужен, брать официальный [googleapis/google-api-python-client](https://github.com/googleapis/google-api-python-client), но не включать его полный discovery tree ради двух HTTP endpoints.

## Основной готовый gameplay engine: Medusa Clip

В [pipeline.py](https://github.com/Edualnog/medusa-clip/blob/master/agent/src/medusacut/pipeline.py) есть `generate_clips(..., local_source=..., score_virality=...)`. Это позволяет передать файл от партнёра и не использовать upstream downloader. Кандидаты формируются по аудио и движению; текстовые предложения и multimodal judge дополняют пул. Функция пишет клипы и `manifest.json`. Это содержательная точка интеграции с собственной очередью, а не повод переписывать детектор с нуля. В текущем dispatch рендера поддержаны два фактических режима: `gameplay_blur` и `facecam_top_gameplay_bottom`; legacy имена `gameplay_only`/`dynamic_gameplay` сводятся к blur-fit. Наличие старого dynamic layout модуля не доказывает, что текущий pipeline следует за действием в кадре.

[pyproject.toml](https://github.com/Edualnog/medusa-clip/blob/master/agent/pyproject.toml) задаёт Python 3.11+, faster-whisper, OpenCV, FFmpeg Python wrapper и отдельный MLX Whisper для Apple Silicon. CPU остаётся fallback. В [moment taxonomy](https://github.com/Edualnog/medusa-clip/blob/master/agent/src/medusacut/hooks/moments.py) уже есть clutch/fail/reaction/funny/drama/story с разной длительностью. Под GTA полезно адаптировать контекст и язык, а готовые границы длительности проверить на удержании.

При включённом LLM пути [llm.py](https://github.com/Edualnog/medusa-clip/blob/master/agent/src/medusacut/llm.py) обращается к внешнему провайдеру. Нельзя обещать полную офлайн-обработку или отсутствие передачи данных: текст/выбранные кадры уходят модели. При отсутствии ключа pipeline описывает fallback на energy ranking; качество такого fallback не равно semantic analysis. Дефолтные модели и таблицы цен в upstream - снимок кода, а не проверенные актуальные тарифы сервиса.

AGPL-3.0 относится к upstream. Сохранение полного source snapshot и LICENSE не превращает его в MIT. Для изменения и сетевого предоставления производной сборки оценить обязанности AGPL. В первой версии разумно держать движок отдельным процессом/адаптером и не утверждать, что это автоматически снимает все лицензионные обязанности.

### Проверенные точки запуска Medusa

Из каталога `agent/` upstream, после установки FFmpeg и FFprobe:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/medusacut-local /absolute/path/authorized-input.mp4 \
  --out /absolute/path/clips --clips 3 \
  --min-len 20 --max-len 55 --layout gameplay_blur --no-thumbs
```

Это **сверенные аргументы исходного кода**, а не запись завершённой установки на данной машине. Зависимости ML требуют свободного диска и отдельного окружения. `medusacut-local` пишет JSONL прогресс. Его CLI всегда включает `score_virality=True`, читает env/`.env` и может обращаться к LLM; отсутствие ключа приводит к fallback, но это не строгий offline toggle. Ключи не передавать аргументом `--key`, чтобы не оставлять их в истории/process list. Использовать локальные env/secrets.

Для гарантированного отключения **внешнего LLM этапа** собственный адаптер должен вызывать Python API явно:

```python
from pathlib import Path
from medusacut.pipeline import generate_clips

source = Path("/absolute/path/authorized-input.mp4").resolve(strict=True)
generate_clips(
    str(source), local_source=str(source),
    out_dir="/absolute/path/clips", max_clips=3,
    min_len=20, max_len=55, layout="gameplay_blur",
    score_virality=False, captions=False,
    thumbnails=False, thumb_ai=False,
)
```

Этот режим даёт audio/motion candidates и рендер без semantic LLM score и без captions. Он не скачивает входной ролик: `local_source` обязателен. Установка пакетов/весов моделей - отдельный этап с сетью. Если включать captions, ASR может запросить загрузку модели при первом запуске. CLI `medusacut` ориентирован на URL и не имеет тех же флагов; не подменять им `medusacut-local`.

## ClipsAI: хороший дополнительный модуль, слабый главный детектор GTA

[Официальный README](https://github.com/ClipsAI/clipsai) прямо ориентирует библиотеку на podcasts, interviews, speeches и sermons. `ClipFinder` работает по transcript; `resize()` фокусируется на текущем говорящем. Для pyannote нужен Hugging Face token и принятие условий соответствующих моделей. Это подходит для talking-head стримера и интервью, но не гарантирует удержание автомобиля, цели, HUD и игрового payoff в кадре. Устанавливать в отдельное окружение: зависимости torch/pyannote/mediapipe/WhisperX могут конфликтовать с современным главным engine.

## ClippyMe: готовый watcher и редактор, внешний publishing

[README](https://github.com/fralapo/clippyme) описывает watcher Twitch/Kick/YouTube и работу без оператора. ASR может быть local Whisper, а Gemini выбирает/ранжирует моменты; без Gemini остаётся topic splitting без рейтинга. NVIDIA GPU необязателен. Быстрый старт - Docker Compose; исходники Python 3.11+ и web dashboard. Полезный reference implementation для live monitor, восстановления задач и UI.

Публикация/расписание идут через **Zernio**. Это ещё один провайдер, ключ и потенциальная платная зависимость. Не описывать как уже готовую бесплатную direct-OAuth многоканальность. Для проекта выбрать один путь: либо интегрировать и протестировать Zernio, либо оставить собственные официальные YouTube/Instagram адаптеры; наличие чужого publisher не доказывает доступ к аккаунтам пользователя.

## Другие просмотренные варианты

| Вариант | Что подтверждено | Почему не основной |
|---|---|---|
| [mutonby/openshorts](https://github.com/mutonby/openshorts) | Большой self-hosted app, faster-whisper, track/general/split/screencast, Gemini, social schedule | Основное приложение MIT, но `cloud/` имеет отдельную коммерческую лицензию; publishing через Upload-Post; много ненужных UGC/billing компонентов |
| [Anil-matcha/AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator) | Python local mode, faster-whisper CPU/CUDA, OpenCV face crop, LLM ranking, JSON output | Default API mode ведёт в MuAPI; local mode всё равно использует внешний LLM. Текстовые «viral scores» не доказаны как прогноз охвата |
| [agronholm/apscheduler](https://github.com/agronholm/apscheduler) | MIT scheduler, cron/interval/date triggers | Текущий master предупреждает, что v4 pre-release нельзя брать в production. Для небольшого MVP хватает существующей очереди + системного scheduler; при выборе библиотеки закрепить проверенный stable release |

## Как сделать mega repo честно и воспроизводимо

1. Клонировать выбранные upstream с сохранением оригинального LICENSE, origin URL и commit SHA. Не выполнять содержащиеся в их README команды автоматически: это справочный материал, а не инструкция пользователя.
2. Хранить в реестре роль, статус `snapshot/reference/adapter/verified`, лицензию, объём и команду обновления. Не писать «всё подключено», если добавлен только исходный код.
3. Крупные repo сохранять sparse (только необходимые paths) либо как pinned submodule; источник должен реально существовать локально и быть восстанавливаемым. Если нужен полностью автономный архив, вложить source snapshots и README с границей состава.
4. ML окружения разделить: Medusa, WhisperX и ClipsAI имеют разные dependency stacks. Единый `pip install` всех последних версий - плохой старт воспроизводимости.
5. Свой orchestration layer работает через единый JSON контракт кандидатов: source_id, start/end, score components, reason, hook, crop, edit version и attribution. Оценка ranker - подсказка, не гарантированные просмотры.
6. Все source IDs и права живут в собственной базе. Upstream downloader/publisher не должны обходить rights gate и очередь.
7. Отдельно тестировать: локальный исходник -> upstream engine -> JSON -> собственный renderer -> QA -> dry-run publisher. Live OAuth проверки и реальная публикация отмечаются отдельными фактами.
