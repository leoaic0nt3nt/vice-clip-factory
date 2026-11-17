"""Snapshot still artwork and turn it into a timed in-content sponsor overlay."""

from hashlib import sha256
from io import BytesIO
import math
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image, ImageDraw, ImageOps

MAX_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 16_000_000
LIME = "#D6FC72"


def timing(start, seconds):
    if type(start) not in (float, int) or not math.isfinite(start) or not 0 <= start <= 44:
        raise ValueError("Начало показа должно быть от 0 до 44 секунд")
    if type(seconds) not in (float, int) or not math.isfinite(seconds) or not 1 <= seconds <= 8:
        raise ValueError("Длительность показа должна быть от 1 до 8 секунд")
    return float(start), float(seconds)


def _read_image(data):
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in ("PNG", "JPEG", "WEBP") or getattr(image, "n_frames", 1) != 1:
                raise ValueError("Для промо нужен статичный PNG, JPEG или WebP")
            if image.width * image.height > MAX_PIXELS:
                raise ValueError("Изображение промо больше 16 Мп")
            frame = ImageOps.exif_transpose(image).convert("RGBA")
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("Не удалось прочитать изображение промо") from error
    if not frame.getbbox():
        raise ValueError("Изображение промо полностью прозрачное")
    return frame


def _read_local(path):
    path = Path(path).expanduser()
    if not path.is_file():
        raise ValueError("Файл изображения промо не найден")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Изображение промо больше 12 МБ")
    return data


def snapshot(source, directory):
    """Canonicalize to a private content-addressed PNG, independent of the original."""
    image = _read_image(_read_local(source))
    image = image.crop(image.getbbox())
    image.thumbnail((768, 768), Image.Resampling.LANCZOS)
    encoded = BytesIO()
    image.save(encoded, format="PNG", optimize=True)
    content = encoded.getvalue()
    digest = sha256(content).hexdigest()
    directory = Path(directory).resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / (digest + ".png")
    if not target.exists():
        with NamedTemporaryFile(dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(content)
                stream.close()
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
    artwork = {"path": str(target), "sha256": digest}
    read_artwork(artwork)
    return artwork


def read_artwork(artwork):
    data = _read_local(artwork["path"])
    if sha256(data).hexdigest() != artwork["sha256"]:
        raise ValueError("Изображение промо изменилось. Загрузите его заново через viceclip promo")
    return _read_image(data)


def _card(title, cta, artwork, language, paid, font, text_image):
    image = Image.new("RGBA", (560, 128))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((1, 1, 558, 126), 22, fill=(12, 17, 17, 248),
                           outline=(144, 164, 147, 155), width=2)
    draw.rounded_rectangle((14, 15, 112, 112), 14, fill=(30, 42, 36, 255))
    if artwork:
        art = read_artwork(artwork)
        art.thumbnail((88, 88), Image.Resampling.LANCZOS)
        image.alpha_composite(art, (63 - art.width // 2, 64 - art.height // 2))
    else:
        draw.polygon(((53, 43), (53, 86), (83, 64)), fill=LIME)
    label = ("PAID PROMOTION" if paid else "PREVIEW") if language == "en" else ("РЕКЛАМА" if paid else "ПРИМЕР")
    draw.text((132, 12), label, font=font(17), fill=LIME, anchor="lt")
    body = text_image(title, size=32, width=402, max_lines=2, configured=None, stroke=0, max_height=56)
    image.alpha_composite(body, (129, 32))
    action = text_image(cta, size=23, width=402, max_lines=1, configured=None, stroke=0, color="#D6FC72")
    image.alpha_composite(action, (129, 91))
    return image


def layers(style, duration, hook_end, font, text_image):
    """Use a single entrance and exit. The underlying gameplay and audio continue."""
    start, length = timing(style.get("sponsor_start", 4), style.get("sponsor_duration", 4))
    length = min(length, duration)
    start = min(start, max(0., duration - length))
    end = start + length
    language = style.get("sponsor_language", "ru")
    cta = style.get("sponsor_cta") or ("Link in profile" if language == "en" else "Ссылка в профиле")
    badge = _card(style["sponsor_text"], cta, style.get("sponsor_artwork"), language,
                  style.get("sponsored"), font, text_image)
    if style.get("sponsor_position", "top") == "bottom":
        y = 1560 if style.get("caption_mode") == "legacy" else 1490
    else:
        y = 430 if start < hook_end else 185
    fade = min(.2, length / 4)
    frames = []
    for index in range(5):
        fraction = (index + 1) / 5
        eased = fraction * (2 - fraction)
        layer = badge.copy()
        layer.putalpha(badge.getchannel("A").point(lambda value: round(value * eased)))
        offset = round(34 * (1 - eased))
        frames.append((start + fade * index / 5, start + fade * fraction, layer, y + offset))
        frames.append((end - fade * fraction, end - fade * index / 5, layer, y + offset))
    frames.append((start + fade, end - fade, badge, y))
    return frames
