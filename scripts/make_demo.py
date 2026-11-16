#!/usr/bin/env python3
"""Make an original small test fixture. NO GTA gameplay, voice or copyrighted assets."""
import argparse
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw, ImageFont

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--output", default="examples/demo-source.mp4")
p.add_argument("--duration", type=int, default=45)
a = p.parse_args()
target = Path(a.output).resolve()
target.parent.mkdir(parents=True, exist_ok=True)
if target.exists():
    raise SystemExit("Output exists; choose another path")
# Standard FFmpeg test pattern + animated shape. Silence 19-24s; quiet moving picture remains.
video = "testsrc2=size=1280x720:rate=30,drawbox=x=70:y=60:w=1140:h=100:color=0x0A1022@0.9:t=fill,drawbox=x=100:y=570:w=1080:h=60:color=0x234DFF:t=fill"
label_path = target.with_suffix(".label.png")
label = Image.new("RGBA",(1280,720),(0,0,0,0))
face = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf",32) if Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf").exists() else ImageFont.truetype("DejaVuSans-Bold.ttf",32)
ImageDraw.Draw(label).text((95,90),"SYNTHETIC FIXTURE - NOT GTA GAMEPLAY",font=face,fill="white")
label.save(label_path)
audio = "aevalsrc=if(between(t\\,19\\,24)\\,0\\,(0.06+0.15*between(t\\,10\\,17)+0.25*between(t\\,33\\,37))*sin(2*PI*440*t)):s=48000"
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i", video,
                "-f", "lavfi", "-i", audio, "-loop", "1", "-i", str(label_path), "-filter_complex", "[0:v][2:v]overlay=shortest=1[v]", "-map", "[v]", "-map", "1:a", "-t", str(a.duration), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-n", str(target)], check=True)
label_path.unlink()
print(target)
