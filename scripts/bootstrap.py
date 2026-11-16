#!/usr/bin/env python3
"""Install glue and pinned upstream sources. GPU dependencies are opt-in."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=ROOT, check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', choices=['core', 'scenes', 'asr', 'medusa', 'all'], default='core')
    p.add_argument('--skip-submodules', action='store_true')
    a = p.parse_args()
    if sys.version_info < (3, 10):
        p.error('Python 3.10+ required; Python 3.12 recommended.')
    if not a.skip_submodules:
        run('git', 'submodule', 'update', '--init', '--recursive', '--depth', '1')
    for exe in ('ffmpeg', 'ffprobe'):
        if not shutil.which(exe):
            p.error(f'{exe} missing. macOS: brew install ffmpeg; Ubuntu: apt install ffmpeg')
    env = ROOT / '.venv'
    if not (env / 'bin/python').exists():
        venv.EnvBuilder(with_pip=True).create(env)
    py = env / 'bin/python'
    run(py, '-m', 'pip', 'install', '-e', '.', '-e', 'upstream/yt-dlp')
    if a.profile in ('scenes', 'all'):
        run(py, '-m', 'pip', 'install', '-e', 'upstream/pyscenedetect[opencv]')
    if a.profile in ('asr', 'all'):
        asr = ROOT / '.tools/whisperx'
        if not (asr / 'bin/python').exists():
            venv.EnvBuilder(with_pip=True).create(asr)
        run(asr / 'bin/python', '-m', 'pip', 'install', str(ROOT / 'upstream/whisperx'))
    if a.profile in ('medusa', 'all'):
        if sys.version_info < (3, 11):
            p.error('Medusa requires Python 3.11+, recommend 3.12.')
        medusa = ROOT / '.tools/medusa'
        if not (medusa / 'bin/python').exists():
            venv.EnvBuilder(with_pip=True).create(medusa)
        run(medusa / 'bin/python', '-m', 'pip', 'install', str(ROOT / 'upstream/medusa-clip/agent'))
    print('\nReady. source .venv/bin/activate && viceclip doctor')
    print('Source commits frozen; transitive pip dependencies are not a universal GPU lock.')
    print('ASR models download on first use; CUDA/cuDNN and model terms need separate setup.')


if __name__ == '__main__':
    main()
