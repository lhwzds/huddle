#!/usr/bin/env python3
"""Run the Huddle evaluation dataset against a running server."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

import httpx

from huddle.config import load_config
from huddle_eval.core import configured_message_chars, load_cases
from huddle_eval.runner import default_base, default_judge, judge_setup, run_case

ROOT = Path(__file__).resolve().parent.parent


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', default=default_base(),
                   help='Huddle HTTP base URL (env HUDDLE_EVAL_BASE, default http://localhost:8080)')
    p.add_argument('--only', nargs='+', metavar='ID', help='run only these case IDs')
    p.add_argument('--category', help='run only this category')
    p.add_argument('--judge', default=default_judge(), metavar='MODEL_KEY',
                   help='judge model key from config/huddle.yaml (default: the active model of the config)')
    p.add_argument('--no-judge', action='store_true', help='skip the LLM judge')
    p.add_argument('--repeat', type=int, default=1, metavar='N')
    p.add_argument('--concurrency', type=int, default=2, metavar='K')
    p.add_argument('--quiet-ms', type=int, default=0, metavar='MS',
                   help='accepted for compatibility and ignored: idle waits for the conversation to stop being active')
    p.add_argument('--access-code-env', default='HUDDLE_ACCESS_CODE', metavar='VAR',
                   help='env var holding the invite code of a protected deployment (used when set)')
    p.add_argument('--idle-timeout-s', type=float, default=90, metavar='SECONDS')
    return p


async def main(args):
    if args.repeat < 1 or args.concurrency < 1 or args.quiet_ms < 0 or args.idle_timeout_s <= 0:
        raise ValueError('--repeat and --concurrency must be positive')
    # The server's own config: the message length limit and the judge model.
    try:
        cfg = load_config()
    except (OSError, ValueError) as exc:
        raise ValueError(f'cannot read the Huddle config: {exc}')
    cases = load_cases(ROOT / 'eval/cases', configured_message_chars(cfg))
    if args.only:
        missing = set(args.only) - {c['id'] for c in cases}
        if missing: raise ValueError(f'unknown case IDs: {sorted(missing)}')
        cases = [c for c in cases if c['id'] in args.only]
    if args.category:
        cases = [c for c in cases if c['category'] == args.category]
    if not cases: raise ValueError('no cases selected')
    judge_config, judge_model = None, None
    if not args.no_judge:
        # The judge is a model of the server's own config: the active one
        # unless --judge names another.
        judge_model, judge_config = judge_setup(args.judge, cfg)
    async with httpx.AsyncClient(base_url=args.base.rstrip('/'), timeout=30) as client:
        code = os.environ.get(args.access_code_env)
        if code:
            (await client.post('/api/access', json={'code': code})).raise_for_status()
        try:
            r = await client.get('/api/health'); r.raise_for_status(); model = r.json().get('model', 'unknown')
        except Exception:
            model = 'unknown'  # Each case records its own connection error.
        sem = asyncio.Semaphore(args.concurrency)
        async def run(case, repeat):
            async with sem: return await run_case(client, case, repeat, judge_config, idle_timeout_s=args.idle_timeout_s)
        results = await asyncio.gather(*(run(case, repeat) for case in cases for repeat in range(1, args.repeat + 1)))
    print(f'{"case":32} {"checks":>9} {"judge":>9} {"first ms":>10} {"steer ms":>10}  error')
    for result in results:
        checks, judges, m = result['checks'], result['judge'], result['metrics']
        first = [v['ms'] for v in m.get('first_delta_ms', []) if v['ms'] is not None]
        steer = m.get('median_steer_first_delta_ms')
        print(f'{result["id"]:32} {sum(c["pass"] for c in checks):>3}/{len(checks):<5} {sum(j.get("verdict") is True for j in judges):>3}/{len(judges):<5} {min(first) if first else "-":>10} {round(steer, 1) if steer is not None else "-":>10}  {result["error"] or ""}')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    safe_model = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(model))
    path = ROOT / 'eval/results' / f'{stamp}-{safe_model}.json'; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'timestamp': stamp, 'model': model, 'base': args.base, 'judge_model': judge_model, 'cases': results}, ensure_ascii=False, indent=2) + '\n')
    print(f'Results: {path}')


if __name__ == '__main__':
    try: asyncio.run(main(parser().parse_args()))
    except (ValueError, KeyError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr); sys.exit(2)
