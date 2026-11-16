#!/usr/bin/env python3
"""Create a buyer package from an explicit file list; never include live credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from viceclip import __version__

FILES=['install.sh','pyproject.toml','LICENSE','THIRD-PARTY.md','README.md','AGENTS.md',
       'docs/QUICKSTART.md','docs/CONNECT_ACCOUNTS.md','docs/FAQ.md','docs/TERMS.md','docs/GUIDE.md',
       'docs/verification.md','sales/OFFER.md','sales/SELLER_PLAYBOOK.md','sales/ORDER_TEMPLATE.md',
       'sales/SALES_COPY.md','scripts/scout_sources.py','scripts/run_medusa.py','scripts/make_demo.py',
       'examples/config.json','examples/transcript.json','output/demo/clip-demo.mp4',
       'output/pdf/GTA-VI-Clip-Factory-Guide-by-Leo.pdf','upstream.lock.json']


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=ROOT/'dist'/f'Vice-Clip-Factory-{__version__}.zip')
    a=p.parse_args()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    files=[ROOT/path for path in FILES]
    files += [x for x in (ROOT/'docs').glob('*') if x.is_file() and x.suffix in ('.md','.json')]
    files += [x for x in (ROOT/'src/viceclip').glob('*.py')]
    files += [x for x in (ROOT/'assets').rglob('*') if x.is_file() and x.suffix in ('.png','.ttf','.txt','.json')]
    for name in ('yt-dlp','whisperx','pyscenedetect','auto-editor','remotion','ffmpeg','twitchapi','medusa-clip','clippyme'):
        folder=ROOT/'upstream'/name
        if folder.exists():
            files += [x for x in folder.glob('*') if x.is_file() and x.name.lower().startswith(('license','copying','notice'))]
    missing=[str(x.relative_to(ROOT)) for x in files if not x.is_file()]
    if missing:p.error('Missing package files: '+', '.join(missing))
    manifest={}
    prefix=f'Vice-Clip-Factory-{__version__}/'
    with zipfile.ZipFile(a.output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(set(files)):
            rel=path.relative_to(ROOT)
            if path.is_symlink():p.error('Symlinks are not allowed in the buyer package')
            data=path.read_bytes();manifest[str(rel)]=hashlib.sha256(data).hexdigest()
            archive.writestr(prefix+str(rel),data)
        archive.writestr(prefix+'README_FIRST.txt',
            'VICE CLIP FACTORY\n\n1. Open Terminal in this folder and run: bash install.sh\n'
            '2. Activate the environment as printed, then: viceclip setup\n'
            '3. Start: viceclip autopilot start --persistent\n\n'
            'Russian guide: output/pdf/GTA-VI-Clip-Factory-Guide-by-Leo.pdf\n'
            'Account connection: docs/CONNECT_ACCOUNTS.md\n'
            'Your credentials and computer/server remain under your control.\n')
        archive.writestr(prefix+'MANIFEST.json',json.dumps({'version':__version__,'files':manifest},indent=2)+'\n')
    print(json.dumps({'package':str(a.output.resolve()),'files':len(manifest),
                      'size':a.output.stat().st_size,'sha256':hashlib.sha256(a.output.read_bytes()).hexdigest()},indent=2))


if __name__=='__main__':main()
