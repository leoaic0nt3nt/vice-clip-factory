#!/usr/bin/env python3
"""Watch an authorized local inbox, render and enqueue clips. Dry-run publishing by default."""
import argparse
import datetime as dt
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from viceclip.media import fingerprint
from viceclip.queue import Queue
from viceclip.rights import check_rights, get_account, load_config


def tick(args):
    config = load_config(args.config)
    check_rights(config, args.source_id, 'edit')
    accounts = config.get('automation', {}).get('accounts', [])
    if args.auto_approve and config.get('automation', {}).get('auto_approve') is not True:
        raise ValueError('--auto-approve also requires automation.auto_approve=true in private config')
    if any(get_account(config, a)['platform'] == 'instagram' for a in accounts):
        raise ValueError('Inbox worker currently enqueues YouTube only; Instagram needs per-file public media URLs. Use queue add for Instagram.')
    state_path = Path(args.state)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(state_path)
    db.execute('CREATE TABLE IF NOT EXISTS processed (sha TEXT PRIMARY KEY, path TEXT, run_dir TEXT, completed_at TEXT)')
    queue = Queue(args.publish_db)
    for path in sorted(Path(args.inbox).glob('*')):
        if path.suffix.lower() not in ('.mp4', '.mov', '.mkv') or not path.is_file():
            continue
        if time.time() - path.stat().st_mtime < args.stable_seconds:
            continue
        sha = fingerprint(path)
        if db.execute('SELECT 1 FROM processed WHERE sha=?', (sha,)).fetchone():
            continue
        run_dir = Path(args.output) / sha[:20]
        result_file = run_dir / 'result.json'
        if not result_file.exists():
            command = [sys.executable, '-m', 'viceclip.cli', 'run', '--input', str(path.resolve()),
                       '--config', str(Path(args.config).resolve()), '--source-id', args.source_id,
                       '--top', str(args.top), '--output-dir', str(run_dir.resolve())]
            if args.asr:
                command += ['--asr']
            subprocess.run(command, cwd=ROOT, check=True)
        if fingerprint(path) != sha:
            raise RuntimeError('Inbox file changed during processing; deliver files by atomic rename')
        result = json.loads(result_file.read_text())
        plan = json.loads((run_dir / 'plan.json').read_text())
        outputs = result['outputs']
        for index, output in enumerate(outputs):
            clip = plan['clips'][index]
            file = output['file'] if isinstance(output, dict) else output
            for account in accounts:
                metadata = {'title': clip.get('hook', 'Gameplay highlight')[:100],
                            'caption': config.get('automation', {}).get('caption', ''),
                            'privacy': config.get('automation', {}).get('privacy', 'private'),
                            'sponsored': config.get('style', {}).get('sponsored', False)}
                job = queue.add(file, args.source_id, account, metadata, config)
                score = clip.get('score', 0)
                threshold = config.get('automation', {}).get('min_score', 80)
                if args.auto_approve and score >= threshold and job['status'] == 'pending':
                    queue.approve(job['id'])
        with db:
            db.execute('INSERT OR IGNORE INTO processed VALUES(?,?,?,?)',
                       (sha, str(path.resolve()), str(run_dir.resolve()), dt.datetime.now(dt.timezone.utc).isoformat()))
        print(json.dumps({'processed': path.name, 'clips': len(outputs), 'accounts': len(accounts)}), flush=True)
    print(json.dumps({'publish': queue.run(config, execute=args.execute)}), flush=True)
    db.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inbox', default='data/inbox')
    p.add_argument('--config', required=True)
    p.add_argument('--source-id', required=True)
    p.add_argument('--state', default='data/inbox.sqlite3')
    p.add_argument('--publish-db', default='data/publish.sqlite3')
    p.add_argument('--output', default='output/inbox')
    p.add_argument('--top', type=int, default=3)
    p.add_argument('--stable-seconds', type=int, default=60)
    p.add_argument('--interval', type=int, default=60)
    p.add_argument('--asr', action='store_true')
    p.add_argument('--once', action='store_true')
    p.add_argument('--auto-approve', action='store_true', help='Opt-in rules gate; does not prove editorial quality.')
    p.add_argument('--execute', action='store_true', help='Actually upload due approved jobs.')
    a = p.parse_args()
    if not Path(a.inbox).is_dir():
        p.error('Create the authorized inbox first.')
    if a.interval < 10 or a.top < 1:
        p.error('interval >= 10 and top >= 1 required')
    while True:
        try:
            tick(a)
        except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
            print(f'worker stopped: {exc}', file=sys.stderr)
            return 1
        if a.once:
            return 0
        time.sleep(a.interval)


if __name__ == '__main__':
    raise SystemExit(main())
