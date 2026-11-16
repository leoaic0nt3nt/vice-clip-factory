"""Pillow text plates for FFmpeg builds without libass. ASS remains the edit source."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def font(size, configured=None):
    paths = [configured, "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"]
    for path in paths:
        if path:
            try:
                return ImageFont.truetype(path,size)
            except OSError:
                pass
    raise ValueError("Install a Cyrillic-capable font or set style.font_path")


def plate(path, text, top, size, color="white", width=900, configured=None):
    image = Image.new("RGBA",(1080,1920),(0,0,0,0))
    draw = ImageDraw.Draw(image)
    face = font(size,configured)
    lines, current = [], ""
    for word in str(text).split():
        proposed = (current + " " + word).strip()
        if draw.textbbox((0,0),proposed,font=face)[2] > width and current:
            lines.append(current)
            current = word
        else:
            current = proposed
    if current:
        lines.append(current)
    # Long speech is chunked into separate timed plates by plates(); never omit words.
    if len(lines) > 4:
        raise ValueError("Text plate is too long; shorten hook/sponsor copy or split transcript segments")
    for index,line in enumerate(lines):
        box = draw.textbbox((0,0),line,font=face,stroke_width=2)
        x = (1080-(box[2]-box[0]))/2
        draw.text((x,top+index*(size+12)),line,font=face,fill=color,stroke_width=2,stroke_fill="#0A1022")
    image.save(path)


def plates(output_dir, cid, captions, hook, duration, style):
    output_dir = Path(output_dir)
    result = []
    def add(kind,text,top,size,start,end,color="white",width=900):
        path = output_dir/f"{cid}-{kind}.png"
        plate(path,text,top,size,color,width,style.get("font_path"))
        result.append((path,start,end))
    add("brand",style.get("brand","@leo_ai_content"),140,32,0,duration,"#5274FF")
    add("hook",hook,250 if style.get("sponsor_position")=="top" else 285,60 if style.get("sponsor_position")=="top" else 65,0,min(5,duration))
    if style.get("sponsor_text"):
        prefix = "Реклама / Paid promotion: " if style.get("sponsored") else "DEMO SLOT: "
        add("sponsor",prefix+style["sponsor_text"],540 if style.get("sponsor_position")=="top" else 1550,34,0,duration,width=870)
    for index,s in enumerate(captions):
        # Preserve every word in long WhisperX sentence segments using timed chunks.
        words = str(s["text"]).split()
        chunks, chunk = [], []
        for word in words:
            if chunk and len(" ".join(chunk+[word])) > 65:
                chunks.append(chunk)
                chunk = []
            chunk.append(word)
        if chunk:
            chunks.append(chunk)
        passed = 0
        for part,chunk in enumerate(chunks):
            start = s["start"]+(s["end"]-s["start"])*passed/max(1,len(words))
            passed += len(chunk)
            end = s["start"]+(s["end"]-s["start"])*passed/max(1,len(words))
            add(f"caption-{index:03d}-{part:02d}"," ".join(chunk),1325,52,start,end)
    return result
