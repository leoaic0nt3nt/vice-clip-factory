#!/usr/bin/env python3
"""Build the eight-page customer guide. Keep instructions concrete and testable."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess

from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
OUT = ROOT / "output/pdf"
FONTS = ASSETS / "fonts"
REPO = "https://github.com/leoaic0nt3nt/vice-clip-factory"
BG, PAPER, BLUE = "#080808", "#F1F1EB", "#234DFF"

PAGES = [
    {
        "section": "КОРОТКИЙ ГАЙД",
        "title": "КОНТЕНТ-ЗАВОД\nДЛЯ GTA VI",
        "kind": "cover",
        "dark": True,
        "intro": "Vice Clip Factory сам ищет записи, делает вертикальные клипы и публикует их в YouTube и Instagram.",
        "blocks": [],
        "note": "Настрой источники и каналы. Добавь свой продукт или реферальную ссылку. Запусти автопилот.",
    },
    {
        "section": "УСТАНОВКА",
        "title": "КАК УСТАНОВИТЬ\nПРОГРАММУ",
        "intro": "Скачай архив продукта и распакуй его на Mac или Linux.",
        "blocks": [
            ("Открой папку в Терминале", "Запусти установщик. Он подготовит программу и в конце покажет команду активации.", "bash install.sh"),
            ("Активируй программу и войди в Codex", "Выполни команду source из вывода установщика. Затем один раз войди в аккаунт с доступом к GPT-6.2 Sol.", "codex login"),
            ("Можно через Codex", "Открой папку продукта в Codex и напиши «Установи Vice Clip Factory и помоги сделать пробный выпуск».", None),
        ],
        "note": "Для выбора моментов отдельные кадры и расшифровка речи передаются в Codex.",
        "link": ("Скачать установочный пакет", REPO + "/releases/latest"),
    },
    {
        "section": "ПОДКЛЮЧЕНИЕ",
        "title": "КАК ПОДКЛЮЧИТЬ\nКАНАЛЫ",
        "intro": "Запусти мастер для англоязычного канала.",
        "top_code": "viceclip setup --language en",
        "blocks": [
            ("YouTube", "Укажи файл подключения Google, выбери свой канал в открывшемся браузере и разреши публикацию.", None),
            ("Проверь канал до запуска", "Подставь имя аккаунта из мастера. Первую приватную загрузку проверь в YouTube Studio, включая видимость, звук и ограничения.", "viceclip youtube check --account youtube-1"),
            ("Instagram", "Нужен профессиональный профиль. Подключи официальный доступ и доставку видео по подсказкам мастера.", None),
        ],
        "note": "Проверка подключения показывает доступ к каналу. Приватная загрузка отдельно проверяет публикацию и обработку.",
        "links": [
            ("Проверка и загрузка YouTube", REPO + "/blob/main/docs/YOUTUBE.md"),
            ("Подключение аккаунтов", REPO + "/blob/main/docs/CONNECT_ACCOUNTS.md"),
        ],
    },
    {
        "section": "ИСТОЧНИКИ",
        "title": "КАКИЕ ВИДЕО\nНАРЕЗАТЬ",
        "intro": "Начни с одного англоязычного канала, одного формата и одного оффера.",
        "blocks": [
            ("Выбери исходники", "Используй собственную запись или материал с разрешением для выбранного формата. Для выпуска на английском выбирай английскую речь или сцену, понятную без слов.", None),
            ("Настрой язык выпуска", "Команда меняет язык заголовков для новых клипов. Субтитры сохраняют язык исходной речи. Русскую серию веди отдельно, если у неё есть аудитория и оффер.", "viceclip market --language en"),
            ("Добавь записи в работу", "Укажи в мастере каналы YouTube или Twitch и частоту выпуска. Поиск по теме можно включить с ключом подключения.", None),
        ],
        "note": "Готовый файл положи в ~/.viceclip/inbox. Язык выпуска и география аудитории проверяются отдельно в YouTube Studio.",
    },
    {
        "section": "РЕКЛАМА И ССЫЛКА",
        "title": "КАК ДОБАВИТЬ\nРЕКЛАМУ И ССЫЛКУ",
        "intro": "Сохрани одну кампанию для всех новых клипов YouTube.",
        "blocks": [
            ("Сохрани предложение", "В мастере укажи название продукта, HTTPS-ссылку и расположение плашки в кадре.", "viceclip promo"),
            ("Подключи креатив", "Создай PNG, JPEG или WebP в ChatGPT Image. Добавь картинку в тот же профиль. Геймплей идёт под анимацией.", "viceclip promo --artwork ./promo.png --position top --start 4 --seconds 4"),
            ("Добавь ссылку в профиль", "Скопируй URL кампании из viceclip revenue link во внешние ссылки профиля YouTube.", None),
        ],
        "note": "Готовые ролики сохраняют прежнее оформление. В Instagram выходит чистая версия. Обычный URL в описании Shorts некликабелен.",
        "link": ("О внешних ссылках YouTube", "https://support.google.com/youtube/answer/13748639?hl=ru"),
    },
    {
        "section": "ЗАПУСК",
        "title": "КАК ЗАПУСТИТЬ\nАВТОПИЛОТ",
        "intro": "Сначала проверь первый клип, затем включи постоянный выпуск.",
        "blocks": [
            ("Сделай пробный проход", "Команда подготовит ролики без публикации. Открой результат в папке ~/.viceclip/output.", "viceclip autopilot tick --preview"),
            ("Проверь результат", "Проверь кадр 9:16, сюжет и короткие субтитры Roboto Condensed Black с подсветкой слова. Оцени плашку и ссылку на телефоне.", None),
            ("Включи публикации", "После подключения аккаунтов запусти постоянный режим. Он будет искать новые записи, нарезать и выпускать ролики по расписанию.", "viceclip autopilot start --persistent --execute"),
        ],
        "note": "Команда с --execute выполняет реальные публикации. Для работы 24/7 машина должна оставаться включённой и иметь интернет.",
    },
    {
        "section": "ДОХОД",
        "title": "КАК ПОЛУЧАТЬ\nДОХОД",
        "intro": "На первые 14 дней выбери одно предложение и отслеживай заказы и комиссии.",
        "blocks": [
            ("Свой продукт или спонсор", "Веди зрителя на страницу товара или заказа. Для спонсора заранее согласуй размещение, оплату и метрики отчёта.", None),
            ("Партнёрские комиссии", "Возьми персональную ссылку партнёра и выгружай подтверждённые комиссии из его кабинета.", "viceclip revenue import partner-export.csv\nviceclip revenue report"),
            ("Монетизация YouTube", "Доход от Shorts подключается отдельно через YPP и AdSense. Проверь условия в Творческой студии для страны владельца аккаунта.", None),
        ],
        "note": "В конце теста сравни удержание, переходы и подтверждённые конверсии. Язык видео сам по себе не меняет страну выплат.",
        "links": [
            ("План теста на 14 дней", REPO + "/blob/main/docs/MONETIZATION.md"),
            ("Монетизация YouTube", "https://support.google.com/youtube/answer/72851?hl=ru"),
            ("Как подготовить CSV", REPO + "/blob/main/docs/REVENUE.md"),
        ],
    },
    {
        "section": "УПРАВЛЕНИЕ",
        "title": "КАК УПРАВЛЯТЬ\nВЫПУСКОМ",
        "intro": "Эти команды показывают работу автопилота и помогают остановить выпуск.",
        "top_code": "viceclip autopilot status\nviceclip autopilot logs\nviceclip autopilot stop",
        "blocks": [
            ("Нет новых клипов", "Проверь ссылки на источники и наличие новых доступных записей. Посмотри последнюю причину в logs.", None),
            ("Клипы готовы, но не вышли", "Проверь подключение аккаунта, время следующего выпуска и дневной лимит. Убедись, что автопилот запущен с --execute.", None),
            ("Нужен ещё один канал", "Скажи Codex: «Подключи ещё один канал через viceclip connect и сохрани мои источники и настройки».", None),
        ],
        "note": "Поменять рекламу: viceclip promo. Отключить её для новых роликов: viceclip promo --off.",
        "link": ("Инструкция и обновления продукта", REPO),
    },
]


def register_fonts():
    for weight in ("Light", "Regular", "Bold"):
        pdfmetrics.registerFont(TTFont("Inter" + weight, str(FONTS / ("InterTight-" + weight + ".ttf"))))


def text_width(value, size, font="InterLight"):
    return pdfmetrics.stringWidth(value, font, size)


def wrap(value, size, width, font="InterLight"):
    result = []
    for paragraph in str(value).split("\n"):
        line = ""
        for word in paragraph.split():
            proposed = (line + " " + word).strip()
            if line and text_width(proposed, size, font) > width:
                result.append(line)
                line = word
            else:
                line = proposed
        result.append(line)
    return result


def text(c, x, y, value, size=15, color=BG, font="InterLight", width=528, leading=None):
    c.setFillColor(HexColor(color))
    c.setFont(font, size)
    lines = wrap(value, size, width, font)
    for line in lines:
        if text_width(line, size, font) > width + 0.3:
            raise ValueError("Horizontal text overflow: " + line)
        c.drawString(x, y, line)
        y -= leading or size * 1.25
    return y


def code(c, y, value, dark=False):
    lines = str(value).split("\n")
    height = 26 + len(lines) * 17
    c.setFillColor(HexColor("#171D30" if dark else "#DFDFD8"))
    c.rect(36, y - height, 528, height, fill=1, stroke=0)
    text(c, 49, y - 20, value, 12.4, PAPER if dark else BG,
         "InterRegular", width=502, leading=17)
    return y - height - 24


def base(c, number, page):
    dark = page.get("dark", False)
    fg = PAPER if dark else BG
    c.setFillColor(HexColor(BG if dark else PAPER))
    c.rect(0, 0, 600, 800, fill=1, stroke=0)
    text(c, 36, 765, "@LEO_AI_CONTENT", 10, fg, "InterRegular")
    c.setFillColor(HexColor(fg))
    c.setFont("InterLight", 10)
    c.drawRightString(564, 765, page["section"])
    c.setStrokeColor(HexColor("#555550" if dark else "#4E4E48"))
    c.setLineWidth(0.45)
    c.line(36, 42, 564, 42)
    text(c, 36, 22, "ЛЕО / VICE CLIP FACTORY", 8.5, fg)
    c.setFont("InterLight", 8.5)
    c.drawRightString(564, 22, f"{number:02d} / {len(PAGES):02d}")
    return fg


def page_note(c, page):
    y = 104 if page.get("links") or page.get("link") else 91
    y = text(c, 36, y, page.get("note", ""), 10.2, "#777772",
             width=528, leading=13)
    if y < 49:
        raise ValueError("Note overlaps footer: " + page["title"])
    links = page.get("links") or ([page["link"]] if page.get("link") else [])
    x = 36
    for label, url in links:
        c.setFont("InterRegular", 9.5)
        c.setFillColor(HexColor(BLUE))
        c.drawString(x, 57, label)
        w = text_width(label, 9.5, "InterRegular")
        c.linkURL(url, (x, 53, x + w, 68), relative=0, thickness=0)
        x += w + 28


def draw_page(c, number, page):
    fg = base(c, number, page)
    if page.get("kind") == "cover":
        c.drawImage(str(ASSETS / "sequence-art.png"), 0, 105, width=600, height=340)
        c.drawImage(str(ASSETS / "gta-vi-logo-trimmed.png"), 36, 625,
                    width=155, height=120, mask="auto")
        text(c, 36, 578, page["title"], 43, PAPER, leading=46)
        text(c, 39, 493, "VICE CLIP FACTORY", 17, "#9EB1FF", "InterRegular")
        text(c, 39, 459, page["intro"], 16, PAPER, width=465, leading=20)
        text(c, 36, 89, page["note"], 12.2, PAPER, width=528, leading=15)
        text(c, 36, 51, "Независимый продукт. Не связан с Rockstar Games.", 8.3, "#AAA9A4")
        return

    title_size = 43
    title_lines = page["title"].split("\n")
    while max(text_width(line, title_size) for line in title_lines) > 528:
        title_size -= 0.5
    y = text(c, 36, 683, page["title"], title_size, fg, leading=title_size * 1.05)
    y -= 20
    y = text(c, 37, y, page["intro"], 17, fg, width=520, leading=21)
    y -= 25
    if page.get("top_code"):
        y = code(c, y, page["top_code"])
    for index, (heading, body, command) in enumerate(page["blocks"], 1):
        text(c, 36, y, f"{index:02d}", 10.5, BLUE, "InterRegular")
        y = text(c, 66, y, heading, 18, fg, "InterRegular", width=498, leading=21)
        y -= 8
        y = text(c, 66, y, body, 14.8, fg, width=494, leading=18.4)
        y -= 18
        if command:
            y = code(c, y, command)
        else:
            y -= 5
    if y < 125:
        raise ValueError(f"Body overlaps note on page {number}: {y:.1f}")
    page_note(c, page)


def markdown():
    fence = chr(96) * 3
    lines = [
        "# Vice Clip Factory - короткий гайд",
        "",
        f"[Продукт и загрузка]({REPO}/releases/latest)",
        "",
        "8 страниц: установка, каналы, источники, реклама, запуск и получение дохода.",
        "",
    ]
    for number, page in enumerate(PAGES, 1):
        lines.extend([f"## {number:02d}. " + page["title"].replace("\n", " "), "", page["intro"], ""])
        if page.get("top_code"):
            lines.extend([fence + "sh", page["top_code"], fence, ""])
        for heading, body, command in page["blocks"]:
            lines.extend(["### " + heading, "", body, ""])
            if command:
                lines.extend([fence + "sh", command, fence, ""])
        if page.get("note"):
            lines.extend(["> " + page["note"], ""])
        links = page.get("links") or ([page["link"]] if page.get("link") else [])
        if links:
            lines.extend([" · ".join(f"[{label}]({url})" for label, url in links), ""])
    (ROOT / "docs/GUIDE.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main():
    global REPO
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", default=REPO)
    args = parser.parse_args()
    previous = REPO
    REPO = args.repo_url.rstrip("/")
    for page in PAGES:
        if page.get("link"):
            label, url = page["link"]
            page["link"] = (label, url.replace(previous, REPO))
        if page.get("links"):
            page["links"] = [(label, url.replace(previous, REPO)) for label, url in page["links"]]
    if len(PAGES) != 8:
        raise ValueError("The customer guide must contain exactly eight pages")
    register_fonts()
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "GTA-VI-Clip-Factory-Guide-by-Leo.pdf"
    c = canvas.Canvas(str(target), pagesize=(600, 800), pageCompression=1)
    c.setTitle("Vice Clip Factory | GTA VI: контент-завод")
    c.setAuthor("Лео · @leo_ai_content")
    c.setSubject("Как установить, подключить каналы, добавить рекламу и запустить выпуск игровых клипов")
    for number, page in enumerate(PAGES, 1):
        draw_page(c, number, page)
        c.showPage()
    c.save()
    markdown()
    if shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-scale-to", "1000",
                        "-singlefile", "-png", str(target), str(ASSETS / "guide-cover")], check=True)
    print(json.dumps({"pdf": str(target), "pages": len(PAGES)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
