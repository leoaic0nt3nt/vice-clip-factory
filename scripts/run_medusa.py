#!/usr/bin/env python3
"""Bridge a permitted local file into the actual pinned Medusa gameplay engine.

Run with .tools/medusa/bin/python after bootstrap --profile medusa.
Model dependencies are intentionally isolated from the lightweight glue.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from viceclip.rights import check_rights, load_config


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--source-id', required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--clips', type=int, default=3)
    p.add_argument('--allow-cloud', action='store_true',
                   help='Enable upstream BYO-key model: may send transcript and frames; costs apply.')
    p.add_argument('--captions', action='store_true', help='Enable upstream local ASR; downloads model weights.')
    a = p.parse_args()
    check_rights(load_config(a.config), a.source_id, 'edit')
    if not a.input.is_file():
        p.error('Input must be an existing local file; this bridge never downloads a URL.')
    if not 1 <= a.clips <= 10:
        p.error('--clips must be 1..10')
    if a.out.exists() and any(a.out.iterdir()):
        p.error('Choose a new or empty output directory.')
    if a.allow_cloud and not os.environ.get('LLM_API_KEY'):
        p.error('--allow-cloud requires LLM_API_KEY; see docs/upstream-selection.md')
    if not a.allow_cloud:
        os.environ.pop('LLM_API_KEY', None)
    try:
        from medusacut.pipeline import generate_clips
    except ImportError as exc:
        p.error(f'Medusa environment missing: {exc}. Run bootstrap --profile medusa.')
    clips = generate_clips(
        str(a.input.resolve()), local_source=str(a.input.resolve()),
        out_dir=str(a.out.resolve()), max_clips=a.clips,
        min_len=20, max_len=55, layout='gameplay_blur',
        game_context='Gameplay highlight: preserve setup, action and payoff. Do not invent events.',
        score_virality=a.allow_cloud, captions=a.captions,
        thumbnails=False, thumb_ai=False,
    )
    print(json.dumps({'engine': 'medusa-clip', 'clips': len(clips),
                      'out': str(a.out.resolve()), 'cloud_allowed': a.allow_cloud,
                      'review_required': True}))


if __name__ == '__main__':
    main()
