# Платформы, права и реальные ограничения

Проверено: **16 ноября 2026 года**. Это инженерное исследование по первичным источникам. Квоты, доступность функций и нормы меняются; перед коммерческим запуском перепроверяйте кабинет приложения и применимые правила. Раздел про РФ - предварительный анализ, а не заключение по конкретной рекламной кампании. Машиночитаемые ссылки: [sources.json](sources.json).

## 1. Статус GTA VI и откуда брать видео

Официальная страница Rockstar указывает **19 ноября 2026 года**, PS5 и Xbox Series X|S. На дату исследования игра ещё не вышла. Из этого следует практическое решение: до релиза проверять конвейер на собственных разрешённых тестовых записях, чётко помечая синтетические демонстрации; не представлять их как GTA VI. [GTA VI - Rockstar](https://www.rockstargames.com/VI).

Публичная fan-content политика Take-Two описывает некоммерческое использование и отдельно относит заработок на видео и продвижение товаров/услуг к коммерческому. Она запрещает публикацию утечек до релиза, отдельно вырезанных сюжетных кинематиков и ряда иных материалов. Нельзя считать эту страницу общей коммерческой лицензией на фабрику рекламных нарезок. Для такой модели отдельно прояснить коммерческие права издателя. Согласие стримера не заменяет права на игру, музыку или других участников. [Rockstar copyright policy](https://support.rockstargames.com/articles/7bNaeoMFTV0iUDGhStTXvz/policy-on-posting-copyrighted-rockstar-games-material).

Рекомендуемый маршрут - собственная запись или согласованный партнёр-стример, который передаёт исходные файлы/разрешённый live-feed. Не использовать утечки, закрытые трансляции или обход ограничений. Приоритет источников: собственный OBS, партнёрский чистый feed, легально доступные файлы от партнёра; затем клипы, доступные через официально разрешённый API.

## 2. Поиск источников и получение медиа - разные операции

**YouTube.** Data API годится для метаданных и обнаружения видео; это не download API. Developer Policies отдельно ограничивают загрузку/хранение аудиовизуальных копий без письменного одобрения YouTube. Поэтому discovery может показать кандидата, но ingest должен получить оригинал от правообладателя по отдельному разрешённому каналу. Не превращать разрешение автора в автоматическое разрешение на скачивание через платформу. Эти же политики ограничивают объединение API-данных разных content owners: аналитические панели должны учитывать границы разрешённой агрегации. [YouTube Developer Policies, section III.E](https://developers.google.com/youtube/terms/developer-policies).

**Twitch discovery.** `GET /helix/streams` позволяет искать трансляции по категории и пользователям; при пагинации возможны пропуски и повторы. Не зашивать неподтверждённый GTA VI `game_id`: получить ID категории и сохранить. [Streams API](https://dev.twitch.tv/docs/api/reference/streams).

**Twitch VOD.** `GET /helix/videos` возвращает метаданные по пользователю, категории или ID. URL страницы VOD не является прямой ссылкой на файл; универсального обещания загрузки любого чужого VOD в этом endpoint нет. [Videos API](https://dev.twitch.tv/docs/api/reference/videos).

**Twitch Clips.** `GET /helix/clips` - обнаружение. Официальный `GET /helix/clips/downloads` выдаёт временные landscape/portrait URLs: до десяти clip ID в запросе, доступ broadcaster/editor и нужные `channel:manage:clips` либо `editor:manage:clips`. Документация указывает 100 запросов в минуту для download endpoint. Скачивание по API не даёт самостоятельных прав на коммерческую перепубликацию. [Clips API](https://dev.twitch.tv/docs/api/reference/clips).

**Вывод для продукта:** база discovery сохраняет только URL/ID/метаданные кандидатов. Media downloader работает лишь после связи с разрешением и разрешённым способом получения файла. Источник без прав получает состояние `needs_rights`, а не бесконечные попытки обхода.

## 3. YouTube: официальная загрузка и несколько каналов

Загрузка - `videos.insert`, обычно resumable. Требуется OAuth, минимальный upload scope - `https://www.googleapis.com/auth/youtube.upload`; API key не заменяет авторизацию владельца. Обычное приложение загружает в канал, связанный с OAuth, а не в произвольный `channel_id` из YAML. CMS `onBehalfOfContentOwnerChannel` предназначен для соответствующих content partners. [videos.insert](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert), [Authorization](https://developers.google.com/youtube/data/guides/authentication).

На 16.11.2026 документация квот описывает раздельные buckets: **100 вызовов `videos.insert` в день**, 100 вызовов `search.list`, 10 000 units для остальных endpoints. Один upload стоит 1 unit своего Video Uploads bucket. Это квоты проекта, не обещание каждому каналу и не гарантия отсутствия отдельных ограничений аккаунта. Смотрите фактическую квоту Cloud Console. Для увеличения квот сохраняется compliance audit. [Quota and audits](https://developers.google.com/youtube/data/guides/quota_and_compliance_audits).

Важное изменение: запись **2 ноября 2026** в revision history отменяет прежнее правило private-only для загрузок из непроверенных API-проектов. Если `privacyStatus` не указан, документация теперь описывает public по умолчанию, с возможными настройками создателя. Поэтому приложение должно **всегда задавать privacyStatus явно**; безопасный первый технический тест - private. Не копировать устаревшее утверждение, что любая публикация нового проекта обязательно ждёт аудита. [Revision history](https://developers.google.com/youtube/data/revision_history).

Для нескольких каналов: отдельная запись credentials/token reference для каждого авторизованного канала, явная проверка соответствия аккаунта очереди, изолированное расписание. OAuth verification, тестовый режим consent screen, refresh token lifecycle и API compliance audit - разные процессы; факт успешного OAuth не подтверждает production readiness.

Resumable session URI сохранять до передачи байтов. После обрыва продолжать существующую сессию, проверяя принятый диапазон; не создавать новую загрузку вслепую. [Resumable upload protocol](https://developers.google.com/youtube/data/guides/using_resumable_upload_protocol).

Квадратные/вертикальные видео до трёх минут могут классифицироваться как Shorts. В рамках продукта разумный стартовый пресет - 20-55 секунд, но это редакционное решение. Каноническая страница на момент проверки предупреждает о глобальном блокировании Shorts длиннее минуты с активной copyright claim; отдельные локализованные страницы могут запаздывать, поэтому не строить систему на пограничном исключении. [Three-minute Shorts](https://support.google.com/youtube/answer/15424877?hl=en).

## 4. Instagram: login, Reels и несколько профилей

API обслуживает **professional Business/Creator accounts**. Текущая overview различает три маршрута: Unified Login, Instagram Login и Facebook Login. Для новых интеграций Meta рекомендует Unified Login; Instagram Login не требует Facebook Page, Facebook Login требует Page. Для собственных/управляемых аккаунтов существует Standard Access; обслуживание сторонних аккаунтов через Advanced Access требует App Review и Business Verification. [Instagram Platform overview](https://developers.facebook.com/documentation/instagram-platform/overview).

Для Instagram Login scopes - `instagram_business_basic` и `instagram_business_content_publish`, host `graph.instagram.com`. Facebook Login использует `instagram_basic`, `instagram_content_publish`, `pages_read_engagement` и при определённых ролях дополнительные права. Credentials и IG account ID должны относиться к одному login-маршруту; их нельзя механически смешивать. Поток Reels: создать container с `media_type=REELS` и публично доступным видео, дождаться `FINISHED`, затем `media_publish`. Статусы также включают `IN_PROGRESS`, `ERROR`, `EXPIRED`, `PUBLISHED`; container живёт 24 часа. В этой же документации API partnership label описан для Facebook Login, поэтому Instagram Login нельзя считать достаточным для полностью автоматического sponsored workflow. [Content Publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing).

**Числа квот в официальных страницах расходятся.** Publishing guide пишет 100 posts / rolling 24h, а `content_publishing_limit` reference приводит `quota_total=50`. Не считать 100 гарантией. Перед постановкой и исполнением очереди читать `quota_usage` и фактический `config.quota_total` / `quota_duration`, учитывать остальные публикации аккаунта и usage headers. Если runtime не возвращает конфигурацию, использовать консервативный локальный лимит до проверки аккаунта, а не предполагать бесконечную квоту. [Publishing limit endpoint](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit).

Reels API specs на дату проверки: MP4/MOV, H.264/HEVC, progressive 4:2:0, 23-60 fps, AAC до 48 kHz, длительность 3 секунды - 15 минут, до 300 MB; 9:16 рекомендуется. Универсальный выход проекта: 1080×1920, 30 fps, H.264 yuv420p, AAC 48 kHz / 128 kbps, MP4 `+faststart`. Это пересечение требований и рабочий пресет, а не обещание охвата. Версию Graph API фиксировать в конфиге после проверки; текущая media reference указывает v27.0. [IG User Media](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media).

Для нескольких Instagram профилей нужны отдельные account ID, token reference, лимиты и состояние каждого профиля. Планировщик хранит собственное время исполнения: не создавать контейнеры на несколько дней вперёд. Публичный URL должен жить достаточно долго для загрузки Meta; localhost не подходит. Не записывать подписанные media URLs и access tokens в открытый GitHub.

## 5. Монетизация, оригинальность и реклама

YouTube рассматривает reused content и generic/repetitive/inauthentic content на уровне канала. Разрешение автора и отсутствие copyright claim сами по себе не гарантируют монетизацию. Минимальная подрезка, субтитры и массовое копирование не заменяют существенный авторский вклад; допустимость определяется содержанием и оценкой платформы. Формат проекта должен добавлять осмысленный рассказ, разбор, контекст или заметную творческую переработку. Та же страница продолжает отмечать приостановку показа Google/YouTube рекламы пользователям в России. [Channel monetization policies](https://support.google.com/youtube/answer/1311392?hl=en).

Для игровых роликов YouTube отдельно связывает коммерческую монетизацию с правами издателя; длинная демонстрация игры без нужной ценности может не пройти. Это ещё один независимый уровень проверки. [Video game content](https://support.google.com/youtube/answer/138161?hl=en).

Встроенная интеграция продукта/спонсорства требует disclosure и соблюдения правил категории и местного права. Флажок YouTube paid promotion не равен универсальному юридическому оформлению. [Branded content labels](https://support.google.com/youtube/answer/154235?hl=en).

YouTube запрещает вшивать сторонние рекламные видеоблоки в форматах, сопоставимых с его собственными pre/mid/post-roll и bumpers. Поэтому шаблон верхней/нижней полосы должен быть частью согласованной авторской интеграции, а не конвейером чужих рекламных роликов; сам факт статичной плашки не гарантирует допустимость конкретной рекламы. [Embedded sponsorship rules](https://support.google.com/youtube/answer/3364658?hl=en).

**Если проект ведётся в контексте РФ:** реклама в Instagram требует отдельного блокирующего правила. Федеральный закон № 72-ФЗ от 07.04.2025 ввёл запрет с 01.09.2025; официальный государственный ресурс, подготовленный с ФАС, прямо относит к запрещённым площадкам Instagram/Facebook. Не предлагать обход через другой аккаунт, иностранный IP или формальную маркировку. Применимость к оператору, аудитории и конкретной кампании проверяет профильный специалист. [Объясняем.рф + ФАС](https://xn--90aivcdt6dxbc.xn--p1ai/articles/questions/internet-mobile/internet/gde-nelzya-razmeshchat-reklamu-s-1-sentyabrya-/). [Официальная публикация 72-ФЗ](https://publication.pravo.gov.ru/document/0001202504070018) была недоступна для чтения инструментом при этой проверке; текст запрета подтверждён доступным официальным разъяснением. Нельзя выдавать эту проверку за полный аудит всех изменений закона.

Практический дефолт: `sponsored_instagram=false` для RU; demo banners помечены как учебные макеты и не публикуются. Нерекламные редакционные ролики проходят отдельную проверку. Для любых рынков оценивать категории спонсора, права на бренд/креатив, договор, disclosure и локальные требования до включения автоматизации.

## 6. Что реально делает стек из референса

| Инструмент | Что делает | Чего не доказывает |
|---|---|---|
| [WhisperX](https://github.com/m-bain/whisperX) | ASR, выравнивание слов, при необходимости diarization | Не выбирает автоматически лучший игровой эпизод и не гарантирует точную расшифровку речи поверх эффектов |
| [PySceneDetect](https://www.scenedetect.com/docs/latest/) | Границы смен кадра/сцен, экспорт списка | Камера в игре двигается непрерывно; scene cut не равен payoff |
| [Auto-Editor](https://auto-editor.com/) | Монтаж по уровню аудио, движению, субтитрам | Тихое прицеливание или ожидание может быть смысловым setup |
| [Remotion](https://www.remotion.dev/docs/license) | Параметризованная графика/рендер React | Для коммерческого использования проверить актуальную лицензию; это не автоматически MIT |
| [FFmpeg](https://ffmpeg.org/legal.html) | Декодирование, фильтры, склейка, финальный encode | Лицензия сборки зависит от включённых компонентов; нельзя объявлять любой бинарник LGPL без проверки |

WhisperX публикует ускорение в конкретных условиях; не переносить его цифру на полный end-to-end pipeline. Проверять собственный RTF: processing_seconds / input_seconds. В игре комбинировать звук, речь, движение, chat spikes и структуру setup/payoff. Удалять тишину только после защиты смыслового контекста. Для MVP допустима эвристика; семантическую модель и доказанную «виральность» она не заменяет.

## 7. Инженерный контракт перед autopublish

Это рекомендации архитектуры, выведенные из требований и наблюдаемых отказов API, а не дополнительные обещания платформ.

1. **Rights ledger:** source owner, proof reference, срок, территории, разрешённые площадки, право на монтаж/коммерческое использование, разрешение издателя, музыка, участники, revocation status. Секретные договоры остаются вне public repo.
2. **Source allowlist:** discovery может предложить нового автора; загрузка и публикация ждут разрешения. Для локального теста rights mode `demo` не даёт допуска в live publisher.
3. **Три независимых статуса:** media ready, platform accepted, end-user playable. Успешный POST не доказывает, что ролик опубликован, обработан и доступен зрителю.
4. **Ключ идемпотентности:** hash источника + интервал + версия монтажа + platform + account ID. SQLite/Postgres unique constraint и atomic lease на job. Общий ролик на разных платформах - разные publish jobs.
5. **До побочного эффекта сохранять intent:** upload session/container ID и состояние. После таймаута финального publish ставить `needs_reconcile`, опрашивать существующую сессию/container. Не повторять создание загрузки без проверки.
6. **Разные ошибки - разные действия:** 401 refresh/re-auth; permissions/app review - остановка аккаунта; quota - перенос; invalid media - пересборка; неоднозначный network timeout - сверка. Retry с backoff ограничен и не превращается в дубли.
7. **Multi-account:** токены по account reference, свои очереди и расписания, общий проектный quota budget YouTube, account publishing budget Instagram. Не умножать приложения ради обхода квот и блокировок.
8. **Live gate:** не выпущенный/утёкший материал, недоказанные права, рекламный запрет, отсутствующий disclosure, невалидный файл или неоднозначный статус блокируют конкретную задачу. Технический dry-run всё ещё работает.
9. **Обратная связь:** оценка модели - ранжирование кандидатов, а не прогноз гарантированного охвата. Начать с ручного review первых выпусков; сравнивать удержание и досмотры по разрешённым данным каждого владельца.
10. **Эксплуатация:** secrets manager или локальные env, редактирование логов, резервная копия state DB, expiration/revocation токенов, кнопка pause account и ручной reconcile. Запуск автопоста требует реальных OAuth credentials и проверки целевого аккаунта.

## Граница проверенного

Проверены публичные документы. В рамках исследования **не** создавались OAuth-приложения, не проходились App Review/Business Verification/audit, не получались права от стримеров/издателя, не выполнялись live-загрузки, не подтверждались монетизация и реальные просмотры. Успешные локальные примеры должны описываться отдельно от подключения боевых аккаунтов.
