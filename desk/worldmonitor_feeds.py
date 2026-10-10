"""Refresh verified upstream cache feeds; never log provider output or secrets."""
import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEEDS = {'crypto-quotes': 300, 'fear-greed': 900}


def refresh(node, feed):
    try:
        result = subprocess.run(
            [node, f'scripts/seed-{feed}.mjs'],
            cwd=ROOT / 'integrations/worldmonitor', capture_output=True,
            text=True, timeout=180,
        )
        completed = []
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get('event') == 'seed_complete':
                completed.append(event)
        event = completed[-1] if completed else {}
        # A zero exit code alone can mean a skipped/failed upstream seed.
        count = event.get('recordCount')
        ok = result.returncode == 0 and event.get('state') == 'OK' and type(count) is int and count > 0
        return {'feed': feed, 'ok': ok, 'records': count if type(count) is int else None,
                'exit_code': result.returncode}
    except subprocess.TimeoutExpired:
        return {'feed': feed, 'ok': False, 'error': 'timeout'}
    except OSError:
        return {'feed': feed, 'ok': False, 'error': 'process_unavailable'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', default=shutil.which('node'))
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if not args.node:
        parser.error('Provide --node PATH to a Node.js 24 executable')
    due = dict.fromkeys(FEEDS, 0)
    try:
        while True:
            success = True
            for feed, interval in FEEDS.items():
                if time.monotonic() >= due[feed]:
                    result = refresh(args.node, feed)
                    print(json.dumps(result), flush=True)
                    success &= result['ok']
                    due[feed] = time.monotonic() + interval
            if args.once:
                return 0 if success else 1
            time.sleep(max(1, min(30, min(due.values()) - time.monotonic())))
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
