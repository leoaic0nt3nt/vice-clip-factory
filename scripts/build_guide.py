#!/usr/bin/env python3
"""Rebuild the guide and original repository artwork. pip install reportlab pillow numpy"""
from __future__ import annotations
import argparse, json, math, random, re
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor

ROOT=Path(__file__).resolve().parents[1]
ASSETS=ROOT/'assets'; OUT=ROOT/'output/pdf'; FONTS=ASSETS/'fonts'
BG='#080808'; PAPER='#F1F1EB'; BLUE='#234DFF'; MUTED='#A6A6A0'
REPO='https://github.com/leoaic0nt3nt/vice-clip-factory'
PAGES=[]
def page(section,title,intro='',blocks=(),kind='text',dark=False,note='',code=None):
    PAGES.append(dict(section=section,title=title,intro=intro,blocks=list(blocks),kind=kind,dark=dark,note=note,code=code))

page('ПРАКТИЧЕСКИЙ ГАЙД','GTA VI\nCLIP FACTORY','От длинного стрима до вертикальной серии.',kind='cover',dark=True,note='Репозитории. Монтаж. Реклама. Автопостинг.\nСистема, которую можно собрать и проверить.')
page('01 / ПОНЯТЬ СИСТЕМУ','ОДИН СТРИМ.\nМНОГО ИСТОРИЙ.','Собираем конвейер, который помогает находить и выпускать сильные игровые моменты.',[
 ('Что делает система','Принимает разрешённый исходник, ищет кандидатов, собирает вертикальные ролики и ведёт очередь для нескольких каналов.'),
 ('Что определяет редактор','Контекст, понятность истории, права, бренд и качество. Автоматический балл помогает сортировать, но не доказывает виральность.'),
 ('Что находится в репозитории','Рабочее локальное ядро, девять реальных Git submodules с SHA pins, мост Medusa, inbox worker, инструкции подключения и примеры.')],note='Версия 1.0 • проверка источников: 16 ноября 2026')
page('02 / АРХИТЕКТУРА','ШЕСТЬ ШАГОВ.\nОДНА ОЧЕРЕДЬ.','Каждый этап оставляет файл или запись, которую можно проверить и повторить.',kind='architecture',dark=True,note='Сначала один исходник и один канал. Затем масштабирование без потери истории.')
page('03 / РАЗБОР РЕФЕРЕНСА','СТЕК ИЗ REELS\nПО ДЕЛУ.','Доступный второй ролик - презентация пяти инструментов на чёрном экране с ведущим и титрами. Он не доказывает работу готового автопилота.',[
 ('WhisperX + PySceneDetect','Первый даёт речь и временные метки. Второй ищет смену изображения. Ни один сам по себе не знает, где лучший игровой момент.'),
 ('Auto-Editor + FFmpeg','Auto-Editor помогает убрать выбранные паузы. FFmpeg режет, масштабирует, сводит дорожки и кодирует результат.'),
 ('Remotion + наше ядро','Remotion нужен для сложных React-шаблонов. Ядро связывает входы, кандидатов, рендер и очередь. MVP рендерит через FFmpeg.')],note='Полный разбор доступности двух ссылок и границы наблюдений: docs/reference-analysis.md')
page('04 / ОТКРЫТЫЕ КОМПОНЕНТЫ','НЕ СПИСОК ССЫЛОК.\nРАБОЧИЙ НАБОР.','Компоненты закрепляются на конкретных коммитах. Обновление становится отдельной проверяемой операцией.',[
 ('Забрать исходники целиком','Используй clone --recursive. Git submodule хранит точный коммит компонента и его оригинальную историю.'),
 ('Понять роль каждого','yt-dlp и TwitchAPI: источники; WhisperX: ASR; PySceneDetect: сцены; Auto-Editor: паузы; Remotion: оформление; FFmpeg: экспорт. Готовые clipping-движки - на следующей странице.'),
 ('Сверить версии и лицензии','Смотри upstream.lock.json и каталог upstream. Коммит фиксирует код, но не выдаёт права на стрим, музыку или бренд.')],note='Большие модели и бинарные зависимости устанавливаются отдельно. Они не помещаются в git.')
page('05 / ГОТОВЫЕ ДВИЖКИ','НЕ ПИСАТЬ\nВСЁ С НУЛЯ.','В набор входят готовые игровые и общие clipping-проекты. Их исходники закреплены отдельно от локального ядра.',[
 ('Medusa / игровой монтаж','Специализированный клиппер: игровой контент, blur-fit и facecam-композиции. AI-путь может отправлять текст и выбранные кадры внешнему провайдеру; это отдельный выбор и расходы.'),
 ('ClippyMe / второй маршрут','Дополнительный clipping-workflow. Его автопостинг использует внешний Zernio, а не встроенную локальную магию. Доступ, тариф и поддержку аккаунтов проверяй отдельно.'),
 ('Готовый мост Medusa','scripts/run_medusa.py проверяет права и передаёт локальный файл в закреплённый движок. По умолчанию LLM, ASR и обложки выключены; тяжёлая установка в этой поставке не прогонялась.')],code='python3 scripts/bootstrap.py --profile medusa\n.tools/medusa/bin/python scripts/run_medusa.py \\\n  examples/demo-source.mp4 \\\n  --config examples/config.json --source-id demo \\\n  --out output/medusa --clips 1',note='--captions включает ASR; --allow-cloud разрешает внешний LLM. Подробнее: docs/upstream-selection.md')
page('06 / ЛИЦЕНЗИИ КОМПОНЕНТОВ','У КАЖДОГО\nСВОИ УСЛОВИЯ.','Открытый код не означает единую лицензию для всей системы и не решает права на исходные видео.',[
 ('Upstream остаётся upstream','Сохраняй copyright notices и LICENSE каждого компонента. Проектная лицензия локального glue-кода не заменяет условия подключённых репозиториев.'),
 ('Особые случаи','Medusa распространяется под AGPL-3.0; модификации и сетевое использование требуют отдельной проверки обязанностей. У Remotion собственные коммерческие условия.'),
 ('Бинарники и модели','Лицензия FFmpeg зависит от фактической сборки и GPL-компонентов. У ASR-моделей и сервисов могут быть отдельные условия, токены и стоимость.')],note='Перед распространением своего продукта проверь upstream.lock.json, notices и фактический состав сборки.')
page('05 / ПЕРВЫЙ ЗАПУСК','СНАЧАЛА ДЕМО.\nПОТОМ GPU.','Базовый путь должен работать до подключения аккаунтов и тяжёлых моделей.',[
 ('Базовая среда','Рекомендуется Python 3.12 и FFmpeg/ffprobe. Bootstrap ставит ядро с Pillow в .venv; doctor показывает доступные фильтры и опциональные инструменты.'),
 ('Раздельные окружения','WhisperX и другие тяжёлые компоненты держи отдельно. Так конфликт CUDA, PyTorch или Python не ломает очередь публикаций.'),
 ('Проверка результата','Получить MP4 недостаточно: открой начало, границы склеек и финал. Проверь звук, субтитры, рекламную область и ffprobe. При отсутствии libass используется графический fallback; ASS хранится рядом.')],code='git clone --recursive '+REPO+'.git\ncd vice-clip-factory\npython3 scripts/bootstrap.py --profile core\nsource .venv/bin/activate\nviceclip doctor',note='Точные команды полного bootstrap и установленный scope: docs/setup.md')
page('08 / ПЕРВЫЙ MP4','ЗАПУСТИ\nВСЮ ЦЕПОЧКУ.','Это конкретный smoke test: собственная синтетика, готовый JSON-транскрипт и один вертикальный ролик.',[
 ('Создать вход','Генератор пишет examples/demo-source.mp4. Это тестовый паттерн с синтетическим звуком, не игровой материал. Команда отказывается перезаписывать существующий файл.'),
 ('Получить проверяемый результат','run создаёт plan.json, ASS-субтитры и MP4. Открой ролик, сравни таймкоды и проверь result.json. Для повторного рендера выбери новую output-папку. Демонстрация не вызывает публикацию.')],code='python scripts/make_demo.py\nCLIP_INPUT=examples/demo-source.mp4\nviceclip run --input "$CLIP_INPUT" \\\n  --source-id demo --config examples/config.json \\\n  --transcript examples/transcript.json \\\n  --top 1 --output-dir output/first-run',note='PySceneDetect подключается отдельной командой viceclip scenes; базовый run не использует его автоматически.')
page('06 / ИСТОЧНИКИ','НАЙТИ СТРИМ\nИ ПОЛУЧИТЬ ФАЙЛ.','Поиск новых авторов, наблюдение за партнёрами и получение медиа - отдельные шаги.',[
 ('Найти новых авторов','scripts/scout_sources.py ищет метаданные YouTube по запросу и Twitch по категории. Кандидаты получают needs_rights; поиск не даёт разрешение и не скачивает видео.'),
 ('Отслеживать партнёров','viceclip discover наблюдает за авторами из allowlist. Унаследовать разрешение можно только из явно выданного channel-wide grant; неизвестные авторы не становятся разрешёнными автоматически.'),
 ('Получить исходник','Начни с собственных записей OBS или файлов от согласованного партнёра. Храни автора, доказательство прав, ID, checksum и разрешённые площадки отдельно от результатов поиска.')],note='python scripts/scout_sources.py --output output/source-candidates.json\nAPI-ключи передаются через env; результат остаётся списком кандидатов на согласование прав.')
page('07 / ПРАВА НА КОНТЕНТ','РАЗРЕШЕНИЕ\nДОЛЖНО БЫТЬ ЯВНЫМ.','У стрима несколько слоёв прав. Согласие автора закрывает только те права, которыми он располагает.',[
 ('Стример и запись','Зафиксируй право скачивать, монтировать, публиковать, монетизировать и встраивать рекламу. Укажи каналы, срок и порядок отзыва.'),
 ('Издатель игры и музыка','Fan policy Rockstar описывает некоммерческое использование. Для коммерческого GTA-проекта отдельно проверь права издателя и музыкальные дорожки.'),
 ('Техническое правило','Пока подтверждения нет, источник остаётся в состоянии needs_permission. В коде нужны отдельные флаги разрешений и блокировка публикации.')],dark=True,note='Разрешение стримера и прохождение платформенной модерации не гарантируют коммерческие права.')
page('08 / БЫСТРЫЙ ВХОД','НЕ ПЕРЕСЧИТЫВАЙ\nВЕСЬ СТРИМ.','Длинный материал сначала анализируется в лёгком представлении. Полное качество нужно на финальном отрезке.',[
 ('Сохранить один раз','Файл и хеш становятся неизменным источником. URL, ID и автор хранятся отдельно от приватных токенов.'),
 ('Сделать proxy','Аудио mono 16 kHz для ASR; уменьшенная картинка для сцен и движения. Не гоняй 4K через все модели.'),
 ('Кэшировать результаты','transcript.json, сцены и кандидаты привязаны к хешу исходника и версии алгоритма. Изменение шаблона не запускает ASR заново.')],note='yt-dlp используется только для источника с подтверждённым разрешением и доступом без обхода ограничений.')
page('09 / ПОЧТИ В РЕАЛЬНОМ ВРЕМЕНИ','ОКНА,\nА НЕ БЕСКОНЕЧНОСТЬ.','Потоковая версия - следующий слой над проверенным файловым конвейером.',[
 ('Окна с перекрытием','Сохраняй разрешённый эфир небольшими блоками. Перекрытие сохраняет завязку события на границе файлов.'),
 ('Кандидат ждёт развязку','Не публикуй эмоциональный пик до окончания события. Добавь хвост после пика и дождись стабильного финала.'),
 ('Сначала измерить задержку','Считай время от события до готового MP4 и отдельно от MP4 до подтверждения публикации. Быстрый рендер не означает быстрый пост.')],note='Непрерывный захват эфира и webhook-сервис описаны как расширение; MVP работает с готовыми файлами.')
page('10 / WHISPERX','УСЛЫШАТЬ\nИ ПРИВЯЗАТЬ.','Нужны точные метки слов и реплик, чтобы монтаж и субтитры переживали склейки.',[
 ('Вход и выход','Передай аудио из proxy в WhisperX. Сохрани язык, segments и words. Диаризация нужна только если помогает разделить говорящих.'),
 ('Игровой словарь','Пополняй список имён, локаций, сленга и событий. Команда «за мной» и шум мотора должны интерпретироваться по контексту.'),
 ('Если речь плохая','Громкая игра, музыка и разговор поверх друг друга дают ошибки. Низкую уверенность отправляй на проверку; не придумывай пропущенные слова.')],note='Модели могут загружаться отдельно; диаризация может требовать Hugging Face token и принятие условий.')
page('11 / PYSCENEDETECT','СКЛЕЙКА\nНЕ РАВНА СОБЫТИЮ.','Смена картинки помогает найти границы, но игра часто идёт одним непрерывным кадром.',[
 ('Что искать','Content/Adaptive detector отмечает заметные изменения. Это полезно для заставок, смены локаций и переключений камеры.'),
 ('Что не принимать за хайлайт','Чёрный экран, меню, загрузка и резкий поворот камеры могут давать сильный сигнал без интересной истории.'),
 ('Как использовать','Комбинируй сцены с речью, энергией звука и контекстом. Приоритет события определяется несколькими сигналами.')],note='MVP измеряет звук и межкадровое движение. PySceneDetect запускается отдельно: viceclip scenes INPUT --output-dir output/scenes.')
page('12 / AUTO-EDITOR','ТИХО\nНЕ ЗНАЧИТ СКУЧНО.','Погоня, ожидание выстрела и напряжённое движение могут работать без речи.',[
 ('Консервативное удаление','Автоматически сокращай только подтверждённые пустые интервалы: меню, длинное бездействие, ожидание соединения.'),
 ('Запас вокруг речи','Оставляй небольшие поля до и после слов. Не режь вдох, начало реакции или звук игрового события.'),
 ('Карта исходного времени','Сохраняй kept_ranges. После каждой склейки пересчитай титры, музыку, рекламный тайминг и итоговую длительность.')],dark=True,note='Паузы сокращаются консервативно с учётом движения. После отдельного viceclip autocut пересоздай транскрипт.')
page('13 / ОТБОР МОМЕНТОВ','БАЛЛ\nС ОБЪЯСНЕНИЕМ.','Каждый кандидат должен отвечать на вопрос: что здесь произошло и почему зрителю стоит досмотреть?',[
 ('Сигналы','Реакция автора, слова о неожиданном событии, всплеск звука, изменение картинки и достаточный контекст.'),
 ('Штрафы','Реклама исходного стрима, загрузки, повторы, клиффхэнгер без развязки, непонятная завязка и почти одинаковые окна.'),
 ('Честная граница MVP','Встроенный ранкер - эвристика. Он не распознаёт все игровые события и не предсказывает просмотры. Для этого нужен размеченный набор и модель.')],note='Храни evidence и confidence рядом со score. Один громкий крик не должен выигрывать автоматически.')
page('14 / РЕДАКТОРСКИЙ КОНТРОЛЬ','ШЕСТЬ ПРОВЕРОК\nПЕРЕД ВЫПУСКОМ.','Первые ролики проходят ручную приёмку. Автоматизация растёт вместе с подтверждённым качеством.',[
 ('История','За 1-2 секунды понятно, что происходит? Есть развязка? Заголовок совпадает с событием?'),
 ('Картинка и звук','Виден герой, маршрут и важный HUD? Речь разборчива? Текст не закрывает действие? Нет обрубленных слов?'),
 ('Права и публикация','Источник разрешён, музыка проверена, аккаунт выбран верно, реклама согласована и пост не является дублем.')],note='Любой критический провал = вернуть на монтаж. Общий высокий балл не компенсирует отсутствие прав.')
page('15 / ФОРМУЛА РОЛИКА','КРЮЧОК.\nСОБЫТИЕ. ФИНАЛ.','Стартовый монтажный рецепт: 20-45 секунд, если событие действительно укладывается в этот хронометраж.',[
 ('00:00-00:02 / обещание','Один конкретный вопрос или необычный факт в кадре. Например: «Он выбрал самый плохой момент для побега».'),
 ('00:02-00:30 / развитие','Убирай повторяющуюся информацию, сохраняй причинность. Дай зрителю увидеть выбор, препятствие и попытку.'),
 ('Последние секунды / развязка','Покажи исход. Заверши на реакции или ясной точке. Петля допустима, если она не скрывает финал.')],note='Хронометраж - редакторская гипотеза для теста, а не универсальное условие алгоритма площадки.')
page('16 / ВЕРТИКАЛЬНАЯ КОМПОЗИЦИЯ','СОХРАНИ\nСАМУ ИГРУ.','Автокроп по центру может обрезать цель, миникарту и второй автомобиль.',kind='vertical',dark=True,note='Макеты схематические. В примерах нет кадров GTA VI и имитации реального геймплея.')
page('17 / СУБТИТРЫ','КРУПНО.\nКОРОТКО. ВОВРЕМЯ.','Текст помогает понять реакцию, а не конкурирует с игровым интерфейсом.',[
 ('Реплика вместо простыни','Один короткий смысловой фрагмент, обычно до двух строк. Для быстрого текста сокращай фразу редакторски, сохраняя смысл.'),
 ('Точный тайминг','Сначала карта склеек, затем перевод исходных меток в время ролика. Слова внутри удалённого интервала не должны остаться в титрах.'),
 ('Контраст и безопасные зоны','Крупный светлый grotesk на тёмной подложке или с обводкой. Проверь на телефоне и в реальном интерфейсе обеих площадок.')],note='В MVP ASS-субтитры строятся из JSON-транскрипта. Полный ASR устанавливается отдельно.')
page('18 / РЕКЛАМА В РОЛИКЕ','РЕКЛАМЕ\nНУЖНО СВОЁ МЕСТО.','Верхняя или нижняя полоса настраивается отдельно от игрового слоя и субтитров.',[
 ('Шаблон кампании','ID кампании, текст, ссылка, период, разрешённые аккаунты и площадки. Один ролик не должен случайно получить старый оффер.'),
 ('Макет','Одна статичная полоса, узнаваемый стиль, читаемый текст. Не закрывай миникарту, прицел, реакцию автора и платформенные кнопки.'),
 ('Ограничения','Маркировка и paid promotion настройки зависят от страны и площадки. В РФ реклама в Instagram запрещена; такой маршрут по умолчанию выключается.')],note='Сначала коммерческие права на игру и запись. Рекламный баннер сам по себе этих прав не создаёт.')
page('19 / РЕНДЕР','СКОРОСТЬ\nНАДО ИЗМЕРЯТЬ.','Быстрый режим - результат профилирования, кэша и ограниченного числа финальных рендеров.',[
 ('Базовый экспорт','1080×1920, H.264, AAC, 30 fps, yuv420p, faststart. Проверь фактические требования площадки при обновлении API.'),
 ('Аппаратный кодировщик','VideoToolbox на Mac или NVENC на совместимом GPU могут ускорить экспорт. Сравни качество и размер с CPU-режимом.'),
 ('Тест на своём железе','Замерь source duration / processing time, p50/p95 и размер файла. Сначала proxy и top-N отбор, потом только финальный рендер.')],note='Remotion - для сложных графических шаблонов. Перед коммерческим использованием проверь актуальную лицензию.')
page('20 / YOUTUBE','КАЖДОМУ КАНАЛУ\nСВОЙ ДОСТУП.','Загрузка идёт через официальный Data API и OAuth пользователя, который управляет конкретным каналом.',[
 ('Подключение','Создай Cloud project, включи YouTube Data API, настрой OAuth consent и запроси минимально необходимый upload scope.'),
 ('Проверить владельца','После авторизации сопоставь channel ID с ожидаемым каналом. Для каждого канала храни отдельную привязку и refresh token.'),
 ('Подтверждение','Используй resumable upload. Сохрани video ID и статус. Принятие upload не подтверждает обработку и воспроизведение у зрителя; это отдельная проверка.')],note='Квоты и правила API меняются. Текущие ограничения смотри в docs/platform-research.md, а не в старом Reels.')
page('21 / INSTAGRAM','СНАЧАЛА КОНТЕЙНЕР.\nПОТОМ REEL.','Официальная публикация требует подходящего профессионального аккаунта, разрешений и доступного медиа.',[
 ('Настройка приложения','Выбери поддерживаемый login flow и scope для публикации. Для внешних клиентов могут требоваться App Review и расширенный доступ.'),
 ('Подготовка файла','Для URL-based пути ролик должен быть доступен Meta по HTTPS. Локальный путь на компьютере не является публичным video_url.'),
 ('Жизненный цикл','Создай REELS container, опрашивай status_code, дождись готовности и только потом вызывай media_publish. Сохрани media ID.')],note='Sponsored Instagram jobs заблокированы до проверенного partnership flow. Лимиты и токены требуют отдельной проверки.')
page('22 / НЕСКОЛЬКО КАНАЛОВ','ОДНА ФАБРИКА.\nРАЗНЫЕ РЕДАКЦИИ.','Каналы отличаются аудиторией, языком и подачей. Копирование одного файла во всю сеть - слабая стратегия.',[
 ('Профиль канала','Язык, темы, тон заголовков, бренд, часовой пояс, расписание, допустимые кампании и отдельный OAuth-доступ.'),
 ('Маршрутизация','Кандидат назначается подходящему аккаунту. Одинаковые события в близких роликах объединяются до постановки в очередь.'),
 ('Ресурсы и квоты','Общий лимит проекта API и лимит аккаунта - разные вещи. Храни резерв, учитывай очередь и останавливай отправку при ошибках авторизации.')],dark=True,note='Название аккаунта в интерфейсе не подтверждает его channel ID или Instagram user ID.')
page('23 / ОЧЕРЕДЬ ПУБЛИКАЦИЙ','НЕ ДУБЛИРУЙ\nНЕИЗВЕСТНЫЙ РЕЗУЛЬТАТ.','Публикация - операция с побочным эффектом. Повторять её вслепую опасно для канала.',[
 ('Уникальный ключ','Хеш ролика + платформа + account ID. Очередь не создаёт второй такой job при повторном запуске.'),
 ('Явные состояния','pending → approved → uploading. Успешный ответ даёт uploaded для YouTube или published для Instagram. Неоднозначный ответ даёт unknown и требует сверки.'),
 ('Повтор только после проверки','Ошибка до отправки может быть повторяемой. После неизвестного результата ищи уже созданный ресурс. Нужны remote ID, журнал и временные метки.')],note='Dry-run по умолчанию. Состояние approved и явный --execute нужны для реального внешнего действия.')
page('24 / ЕЖЕДНЕВНАЯ РАБОТА','СМОТРИ\nНА УЗКИЕ МЕСТА.','Система должна объяснять, почему файл не стал опубликованным роликом.',[
 ('Журнал','source ID, clip ID, версия плана, account ID, статус, external ID и код ошибки. Секреты и подписанные URL в лог не попадают.'),
 ('Сигналы для оператора','Нет места на диске, истёк токен, недоступен исходник, отказ API, зависший контейнер, отсутствие разрешений или провал QA.'),
 ('Восстановление','Отдельно копируй базу очереди, конфиги без токенов и реестр прав. Проверяй восстановление до запуска сетки каналов.')],note='Неизвестная публикация не исчезает из отчёта. Она остаётся задачей для оператора.')
page('28 / УРОВНИ АВТОНОМНОСТИ','ОТ ДЕМО\nДО INBOX WORKER.','Постоянная фабрика получает разрешённые локальные файлы и последовательно увеличивает степень автоматизации.',[
 ('Уровень 1 / локально','Синтетика → анализ → MP4. Без внешних API и тяжёлых моделей. Это проверяет монтажное ядро.'),
 ('Уровень 2 / с редактором','watch_inbox.py ждёт стабильности файлов, хеширует их и не обрабатывает повторно. Один worker относится к одному источнику прав. В YouTube очередь добавляются выбранные аккаунты из automation.'),
 ('Уровень 3 / после настройки','Для auto-approval нужны automation.auto_approve=true и --auto-approve. Только --execute включает внешнюю отправку. Instagram требует отдельного публичного media URL для каждого файла.')],code='mkdir -p data/inbox\npython scripts/watch_inbox.py \\\n  --inbox data/inbox --config config.local.json \\\n  --source-id your-owned-source --once',note='Доставка через атомарное переименование. Боевой автозапуск здесь не установлен. Инструкции: docs/operations.md')
page('25 / ЭКОНОМИКА','ДОХОД\nНЕ РАВЕН ПРОСМОТРАМ.','Модель начинается с разрешённых способов монетизации и стоимости выпуска.',[
 ('Себестоимость','Обработка + хранение + трафик + ручная проверка + доля автора + поддержка. Раздели постоянные и переменные расходы.'),
 ('Рабочие источники дохода','Услуга монтажа владельцам контента, разрешённые интеграции и доход платформы при выполнении её условий. Спонсорство требует собственных прав.'),
 ('Пример расчёта, не прогноз','100 роликов × 40 ₽ переменных затрат = 4 000 ₽. При 10 000 ₽ выручки и 8 000 ₽ постоянных расходов итог -2 000 ₽.')],note='Изменённые титры и рамка не гарантируют одобрение YouTube reused/inauthentic content monetization.')
page('26 / ПЛАН ЗАПУСКА','СЕМЬ ДНЕЙ\nНА ПЕРВУЮ ПЕТЛЮ.','Проверяем одну рабочую цепочку на разрешённом материале до подключения новых каналов.',[
 ('Дни 1-2 / основа','Собрать права, клонировать компоненты, пройти doctor, выпустить демонстрационный MP4 и один реальный тестовый монтаж.'),
 ('Дни 3-4 / качество','Разметить 30-50 моментов, сравнить кандидатов с редактором, настроить паузы, титры, hook и шаблон.'),
 ('Дни 5-7 / доставка','Подключить один аккаунт каждой доступной площадки, проверить приватный/тестовый сценарий, подтвердить external ID и собрать первые метрики.')],note='Публикация GTA VI возможна после появления разрешённых материалов. Дата официального релиза на момент гайда: 19.11.2026.')
page('27 / БОЛЬШАЯ ИНСТРУКЦИЯ','ОДИН БРИФ\nДЛЯ АГЕНТА.','Передай этот запрос вместе с репозиторием. Дальше агент должен показывать доказательства каждого шага.',[
 ('Запрос','«Собери серию вертикальных игровых роликов из разрешённых источников. Начни с реестра прав и одного тестового файла. Используй закреплённые upstream-компоненты, сохраняй таймкоды и промежуточные результаты.'),
 ('Требования','Отбирай понятные истории с развязкой. Не удаляй тихую игровую сцену только по уровню звука. Сохраняй важный HUD, пересчитывай титры после склеек и проверяй рекламу по кампании.'),
 ('Условия выпуска','Для каждого аккаунта веди отдельный доступ и очередь без дублей. До выпуска покажи top-N, причины выбора и QA. Отчитайся MP4, планом монтажа, журналом и external ID. Не называй dry-run публикацией».')],note='Полные конфиги, команды и критерии расширения находятся рядом с кодом в docs/.')
page('28 / ПЕРВОИСТОЧНИКИ','ПРОВЕРЯЙ\nВЛАДЕЛЬЦА ПРАВ.','Источники, на которые опираются правила системы. Кликабельные ссылки открывают официальную документацию.',kind='sources',note='Полный список и дата проверки: docs/sources.json и docs/platform-research.md')
page('29 / ЧТО ДАЛЬШЕ','СОБЕРИ.\nПРОВЕРЬ. УЛУЧШИ.','Рабочая система растёт из проверенного конвейера, а не из обещания бесконечного контента.',[
 ('Первый результат','Разрешённый исходник → кандидаты с объяснением → вертикальный MP4 → проверенная очередь. Это базовая единица проекта.'),
 ('Следующий уровень','WhisperX и PySceneDetect в рабочих средах, обучение ранкера на размеченных событиях, автоматический трекинг, управление кампаниями и мониторинг эфиров.'),
 ('Открыть проект','Исходники, зафиксированные компоненты, команды, визуальные примеры и этот гайд доступны в GitHub. Начни с README и docs/setup.md.')],kind='last',dark=True,note='ЛЕО / @LEO_AI_CONTENT\n'+REPO)

# -------------------------- Original artwork --------------------------
def font(size,weight='Light'):
    return ImageFont.truetype(str(FONTS/f'InterTight-{weight}.ttf'),size)
def text(draw,xy,value,size=30,fill=PAPER,weight='Light'):
    draw.text(xy,value,font=font(size,weight),fill=fill,spacing=int(size*.04))
def atmosphere(w,h,seed=11):
    rng=np.random.default_rng(seed); y,x=np.mgrid[0:h,0:w]
    glow=np.exp(-(((x-w*.60)/(w*.38))**2+((y-h*.64)/(h*.28))**2))
    rim=np.exp(-(((x-w*.75)/(w*.20))**2+((y-h*.60)/(h*.18))**2))
    a=np.zeros((h,w,3),dtype=np.float32)+7
    a[:,:,0]+=glow*8+rim*45; a[:,:,1]+=glow*42+rim*50; a[:,:,2]+=glow*230+rim*15
    a+=rng.normal(0,3,(h,w,1)); return Image.fromarray(np.uint8(np.clip(a,0,255)))
def film_object(w=1200,h=1000):
    im=atmosphere(w,h);d=ImageDraw.Draw(im)
    # Perspective screens, nested as an editing sequence; original geometric artwork.
    for i in range(5):
        x=int(w*.12+i*w*.135); y=int(h*.48-i*h*.075)
        width=int(w*.23);height=int(h*.37)
        p=[(x,y),(x+width,y-40),(x+width+24,y+height-40),(x+24,y+height)]
        d.polygon([(a+12,b+22) for a,b in p],fill=(5,8,20))
        d.polygon(p,fill=(210-i*20,218-i*16,238-i*4))
        inset=[(x+12,y+12),(x+width-10,y-24),(x+width+12,y+height-53),(x+36,y+height-16)]
        d.polygon(inset,fill=(12+i*6,22+i*5,44+i*8))
        # Cobalt route on each frame; a stylised game-space timeline, no gameplay.
        d.line([(x+35,y+height-40),(x+width*.55,y+height*.48),(x+width-14,y+20)],fill=(53,96,255),width=8)
        d.line([(x+55,y+height-43),(x+width*.62,y+height*.48),(x+width+5,y+20)],fill=(115,150,255),width=2)
        for j in range(6):
            yy=y+30+j*int(height*.12)
            d.polygon([(x+5,yy),(x+12,yy-1),(x+13,yy+8),(x+6,yy+9)],fill=(32,38,56))
    # Original film ribbon in foreground.
    pts=[]
    for t in np.linspace(0,1,180):
        pts.append((w*(-.06+1.1*t),h*(.85+.055*math.sin(t*math.pi*3))))
    for i in range(len(pts)-1):
        xx,yy=pts[i];xn,yn=pts[i+1];shade=int(130+100*i/len(pts))
        d.polygon([(xx,yy),(xn,yn),(xn,yn+65),(xx,yy+65)],fill=(shade,shade,min(255,shade+12)))
    for i in range(16):
        t=i/15;xx=w*(-.06+1.1*t);yy=h*(.85+.055*math.sin(t*math.pi*3))
        d.rectangle((xx,yy+13,xx+34,yy+52),fill=(12,28,75))
    return im

def make_art():
    ASSETS.mkdir(exist_ok=True)
    film_object().save(ASSETS/'sequence-art.png')
    im=atmosphere(1600,800,22);d=ImageDraw.Draw(im)
    obj=film_object(1000,800); mask=Image.new('L',(1000,800)); mask.putdata([min(255,int(x*255/180)) for y in range(800) for x in range(1000)]);im.paste(obj,(650,0),mask);d=ImageDraw.Draw(im)
    text(d,(64,44),'@LEO_AI_CONTENT',21,weight='Regular')
    text(d,(64,145),'VICE\nCLIP FACTORY',107)
    text(d,(68,410),'GTA VI · VERTICAL VIDEO SYSTEM',24,fill='#8EA5FF',weight='Regular')
    text(d,(68,483),'Из стрима - в истории.\nИз компонентов - в систему.',38)
    d.line((65,700,1535,700),fill='#5F6270',width=1)
    text(d,(68,730),'OPEN SOURCE / PINNED UPSTREAMS / LOCAL FIRST',18,fill='#B6B7C1')
    text(d,(1160,730),'GUIDE BY LEO / 2026',18,fill='#B6B7C1')
    im.save(ASSETS/'hero.png')
    # Architecture with an editorial flow instead of a wall of cards.
    im=Image.new('RGB',(1600,900),BG);d=ImageDraw.Draw(im)
    text(d,(70,45),'ОДНА ИСТОРИЯ. ШЕСТЬ ЭТАПОВ.',64)
    text(d,(72,135),'Каждый результат проверяем. Каждый запуск повторяем.',29,fill=MUTED)
    items=[('01','ИСТОЧНИК','Права · файл · хеш'),('02','АНАЛИЗ','Речь · сцены · звук'),('03','ОТБОР','Кандидаты · контекст'),('04','МОНТАЖ','9:16 · титры · бренд'),('05','ПРИЁМКА','QA · допуск · версия'),('06','ОЧЕРЕДЬ','Аккаунт · API · ID')]
    for k,(n,ti,sub) in enumerate(items):
        col=k%3;row=k//3;x=70+col*510;y=290+row*230
        text(d,(x,y),n,30,fill='#6B88FF');d.line((x,y+60,x+450,y+60),fill='#39405C',width=1)
        text(d,(x,y+82),ti,43);text(d,(x,y+146),sub,24,fill=MUTED)
        if col<2:text(d,(x+454,y+92),'→',35,fill='#6B88FF')
    d.line((70,790,1530,790),fill='#707070',width=1)
    text(d,(70,824),'DRY-RUN → APPROVED → PUBLISHED / UNKNOWN → RECONCILE',24,fill='#8EA5FF')
    im.save(ASSETS/'architecture.png')
    # Three schematic layouts and an original wireframe road.
    im=Image.new('RGB',(1600,1080),PAPER);d=ImageDraw.Draw(im)
    text(d,(65,40),'ВЕРТИКАЛЬНО. БЕЗ ПОТЕРИ ИГРЫ.',62,fill=BG)
    text(d,(68,124),'СХЕМАТИЧЕСКИЕ МАКЕТЫ / НЕ КАДРЫ GTA VI',22,fill=BLUE,weight='Regular')
    for idx,label in enumerate(['HUD SAFE','FOCUS CROP','SPONSOR SLOT']):
        x=75+idx*515;y=215;ww=400;hh=712
        d.rounded_rectangle((x-8,y-8,x+ww+8,y+hh+8),radius=25,fill='#17191E')
        d.rectangle((x,y,x+ww,y+hh),fill='#071025')
        if idx==0:top=220;bot=455
        else:top=135;bot=555
        # dark cinematic roadway diagram
        d.polygon([(x,y+bot),(x+ww,y+bot),(x+ww*.60,y+top),(x+ww*.42,y+top)],fill='#14328C')
        for j in range(7):
            yy=y+top+(bot-top)*(j/7)**1.7;wid=ww*.47*(j/7)**1.7
            d.line((x+ww*.51-wid,yy,x+ww*.51+wid,yy),fill='#3759C1',width=1)
        d.line((x+ww*.51,y+top,x+ww*.51,y+bot),fill='#A7B9FF',width=2)
        d.rounded_rectangle((x+ww*.5-24,y+bot-72,x+ww*.5+24,y+bot-34),radius=5,fill='#EBEDEB')
        # HUD explicit
        d.rectangle((x+15,y+bot-52,x+88,y+bot-8),outline='#8095D6',width=1)
        text(d,(x+21,y+bot-45),'MAP',12,fill='#8095D6')
        text(d,(x+22,y+29),'SCHEMATIC DEMO',16,fill='#8EA5FF',weight='Regular')
        text(d,(x+22,y+65),'ВОТ ЭТО\nПОВОРОТ',41)
        if idx==2:
            d.rectangle((x,y+105,x+ww,y+157),fill=BLUE)
            text(d,(x+18,y+120),'РЕКЛАМА / БРЕНД',19,weight='Regular')
        d.rectangle((x+25,y+584,x+ww-25,y+645),fill='#080A12')
        text(d,(x+43,y+598),'Точный текст реакции',24)
        text(d,(x,y+hh+30),label,23,fill=BG,weight='Regular')
    im.save(ASSETS/'vertical-examples.png')

# -------------------------- PDF layout --------------------------
def register():
    for key in ['Light','Regular','Bold']:
        pdfmetrics.registerFont(TTFont('Inter'+key,str(FONTS/f'InterTight-{key}.ttf')))

def width(s,size,font='InterLight'):return pdfmetrics.stringWidth(s,font,size)
def wrap(s,size,max_width,font='InterLight'):
    result=[]
    for para in s.split('\n'):
        line=''
        for word in para.split():
            if width((line+' '+word).strip(),size,font)>max_width and line:
                result.append(line);line=word
            else:line=(line+' '+word).strip()
        result.append(line)
    return result

def tx(c,x,y,s,size=15,color=BG,font='InterLight',leading=None,max_width=528):
    c.setFillColor(HexColor(color));c.setFont(font,size)
    lines=wrap(s,size,max_width,font)
    for line in lines:
        c.drawString(x,y,line);y-=leading or size*1.22
    return y

def base(c,i,p):
    dark=p['dark'];fg=PAPER if dark else BG;bg=BG if dark else PAPER
    c.setFillColor(HexColor(bg));c.rect(0,0,600,800,fill=1,stroke=0)
    tx(c,36,765,'@LEO_AI_CONTENT',10,fg,font='InterRegular')
    label=p['section'];c.setFont('InterLight',10);c.setFillColor(HexColor(fg));c.drawRightString(564,765,label)
    c.setStrokeColor(HexColor('#555550' if dark else '#4E4E48'));c.setLineWidth(.45);c.line(36,42,564,42)
    tx(c,36,22,'ЛЕО / VICE CLIP FACTORY',8.5,fg)
    c.setFont('InterLight',8.5);c.drawRightString(564,22,f'{i:02d} / {len(PAGES):02d}')
    return fg

def note(c,p,fg):
    if p['note']:
        y=tx(c,36,90,p['note'],10.2,'#A9A9A3' if p['dark'] else '#5D5D58',max_width=528,leading=13)
        if y<48:raise ValueError('Footer overlap '+p['title'])

def draw_page(c,i,p):
    fg=base(c,i,p);kind=p['kind']
    if kind=='cover':
        c.drawImage(str(ASSETS/'sequence-art.png'),0,109,width=600,height=500)
        tx(c,36,676,p['title'],66,PAPER,leading=68)
        tx(c,39,499,p['intro'],20,PAPER,max_width=480)
        note(c,p,fg);return
    if kind=='architecture':
        tx(c,36,684,p['title'],47,PAPER,leading=49)
        tx(c,38,556,p['intro'],16,PAPER,max_width=510)
        items=[('01','ИСТОЧНИК','Разрешения, файл, хеш'),('02','АНАЛИЗ','Речь, сцены, звук'),('03','ОТБОР','Истории и объяснения'),('04','МОНТАЖ','Титры, 9:16, бренд'),('05','ПРИЁМКА','Качество и допуск'),('06','ПУБЛИКАЦИЯ','Аккаунт, статус, ID')]
        for k,(n,t,s) in enumerate(items):
            x=36+(k%2)*274;y=461-(k//2)*112
            tx(c,x,y,n,13,'#8EA5FF');tx(c,x+33,y,t,22,PAPER)
            tx(c,x+33,y-29,s,13,MUTED,max_width=220)
            c.setStrokeColor(HexColor('#434343'));c.line(x,y-52,x+242,y-52)
        note(c,p,fg);return
    if kind=='vertical':
        tx(c,36,683,p['title'],49,PAPER,leading=51)
        tx(c,38,550,p['intro'],17,PAPER,max_width=510)
        c.drawImage(str(ASSETS/'vertical-examples.png'),25,130,width=550,height=371.25)
        note(c,p,fg);return
    if kind=='sources':
        draw_sources(c,p,fg);return
    # Main editorial template with measured body blocks.
    y=683;title_lines=p['title'].split('\n');title_size=46
    while max(width(s,title_size) for s in title_lines)>528:title_size-=1
    y=tx(c,36,y,p['title'],title_size,fg,leading=title_size*1.04)
    y-=23;y=tx(c,37,y,p['intro'],17,fg,max_width=520,leading=21);y-=31
    blocks=p['blocks'];code=p['code']; compact=bool(code)
    for n,(heading,body) in enumerate(blocks,1):
        tx(c,36,y,f'{n:02}',11,'#8EA5FF' if p['dark'] else BLUE,font='InterRegular')
        y=tx(c,66,y,heading,17 if compact else 19,fg,font='InterRegular',max_width=498,leading=20 if compact else 22)
        y-=8;y=tx(c,66,y,body,12.6 if compact else 14.6,fg,max_width=494,leading=15 if compact else 18)
        y-=16 if compact else 27
    if code:
        lines=code.split('\n');hh=len(lines)*13+26
        if y-hh<115:
            # Setup page has more compact body spacing than the general template.
            raise ValueError('Code block overflows: '+p['title'])
        c.setFillColor(HexColor('#DFDFD8'));c.rect(36,y-hh,528,hh,fill=1,stroke=0)
        tx(c,49,y-18,code,10.7,BG,font='InterRegular',leading=13,max_width=502)
        y-=hh
    if y<111:raise ValueError(f'Body overlap {i}: {y:.1f} '+p['title'])
    if kind=='last':
        # Keep a small clickable repo label separate from body.
        c.linkURL(REPO,(36,61,564,99),relative=0,thickness=0)
    note(c,p,fg)

def draw_sources(c,p,fg):
    tx(c,36,683,p['title'],48,fg,leading=50)
    tx(c,37,551,p['intro'],16.5,fg,max_width=515,leading=20)
    links=[
      ('Rockstar / GTA VI','https://www.rockstargames.com/VI'),
      ('Rockstar / copyright policy','https://support.rockstargames.com/articles/7bNaeoMFTV0iUDGhStTXvz/policy-on-posting-copyrighted-rockstar-games-material'),
      ('YouTube / videos.insert','https://developers.google.com/youtube/data/reference/rest/v3/videos/insert'),
      ('YouTube / monetization policies','https://support.google.com/youtube/answer/1311392'),
      ('Meta / content publishing','https://developers.facebook.com/documentation/instagram-platform/content-publishing'),
      ('Twitch / API reference','https://dev.twitch.tv/docs/api/reference/'),
      ('Upstream / зафиксированные компоненты',REPO+'/tree/main/upstream'),
      ('Полный аудит API и прав',REPO+'/blob/main/docs/platform-research.md'),
    ]
    y=480
    for n,(label,url) in enumerate(links,1):
        tx(c,36,y,f'{n:02}',10,BLUE,font='InterRegular');tx(c,66,y,label,17,fg,font='InterRegular')
        tx(c,66,y-21,'Открыть официальный источник' if n<7 else 'Открыть в репозитории',11,BLUE)
        c.linkURL(url,(66,y-29,560,y+17),relative=0,thickness=0)
        c.setStrokeColor(HexColor('#C6C6BE'));c.line(66,y-38,564,y-38)
        y-=46
    note(c,p,fg)

def markdown():
    lines=['# GTA VI Clip Factory - гайд от Лео','',f'Репозиторий: {REPO}','','Дата проверки: 16 ноября 2026. Статус: рабочее локальное ядро и план подключений; реальные публикации требуют аккаунтов, разрешений и проверки.','']
    for i,p in enumerate(PAGES,1):
        lines.extend([f'## {i:02d}. '+p['title'].replace('\n',' '),'',p['intro'],''])
        if p['kind']=='architecture':lines.extend(['Источник → анализ → отбор → монтаж → приёмка → очередь публикаций.',''])
        for h,b in p['blocks']:lines.extend(['### '+h,'',b,''])
        if p['code']:lines.extend(['```sh',p['code'],'```',''])
        if p['note']:lines.extend(['> '+p['note'].replace('\n','\n> '),''])
    lines.extend(['## Полные первоисточники','','[Аудит платформ](platform-research.md) · [Реестр источников](sources.json) · [Установка](setup.md) · [Архитектура](architecture.md)'])
    (ROOT/'docs/GUIDE.md').write_text('\n'.join(lines)+'\n')

def main():
    global REPO
    a=argparse.ArgumentParser();a.add_argument('--repo-url',default=REPO);args=a.parse_args()
    old=REPO;REPO=args.repo_url.rstrip('/')
    for p in PAGES:
        p['note']=p['note'].replace(old,REPO)
        if p.get('code'):p['code']=p['code'].replace(old,REPO)
    for idx,p in enumerate(PAGES):
        if idx and ' / ' in p['section']:p['section']=f'{idx:02} / '+p['section'].split(' / ',1)[1]
    register();make_art();markdown();OUT.mkdir(parents=True,exist_ok=True)
    path=OUT/'GTA-VI-Clip-Factory-Guide-by-Leo.pdf'
    c=canvas.Canvas(str(path),pagesize=(600,800),pageCompression=1)
    c.setTitle('GTA VI Clip Factory | Гайд от Лео');c.setAuthor('Лео · @leo_ai_content')
    c.setSubject('Репозитории, монтаж, рекламные шаблоны и автопостинг игровых вертикальных клипов')
    for i,p in enumerate(PAGES,1):draw_page(c,i,p);c.showPage()
    c.save();print(json.dumps({'pdf':str(path),'pages':len(PAGES)},ensure_ascii=False))
if __name__=='__main__':main()
