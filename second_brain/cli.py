# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Explicit local sources, synthetic demo and local root diagnostics."""
import sys

# Both console scripts and python -m import this module before locking code.
if sys.platform not in ('linux', 'darwin'):
    print('daybook supports Linux and macOS; Windows is not supported yet')
    raise SystemExit(2)

import argparse
import json
import os
from pathlib import Path
from .errors import UserError, explanation
from . import daybook, dayconfig
from .executables import resolve
from .privacy import Boundary
from .storage import write


def doctor(config):
    """Stat configured roots and resolve executables; no auth/network/content calls."""
    present, absent = 0, 0
    for machine in config['machines']:
        for tool in ('claude', 'codex'):
            for source in machine.get(tool, []):
                if Path(source).expanduser().is_dir():
                    present += 1
                else:
                    absent += 1
    tools = {}
    for name in ('git', 'claude', 'codex'):
        try:
            resolve(name, config)
            tools[name] = True
        except FileNotFoundError:
            tools[name] = False
    roots = config['local']['repo_roots']
    missing = [p for p in roots if not Path(p).expanduser().is_dir()]
    return {'config_ok': True, 'platform': sys.platform, 'supported': sys.platform in ('linux', 'darwin'),
            'tools': tools, 'transcript_roots_present': present, 'transcript_roots_missing': absent,
            'repo_roots_present': sum(Path(p).expanduser().is_dir() for p in roots),
            'repo_roots_missing': len(missing), 'missing_repo_roots': missing,
            'plugins_selected': len(config['plugins']), 'model': config['model'],
            'network_default': False, 'provenance': 'Unmarked user turns are unattributed; only explicit human/owner turns are credited.'}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='daybook', description='Local daily briefs from explicitly selected repos and transcripts')
    sub = parser.add_subparsers(dest='command', required=True)
    demo = sub.add_parser('demo'); demo.add_argument('--out', type=Path, default=Path('daybook-demo'))
    init = sub.add_parser('init'); init.add_argument('--config', type=Path, default=Path('daybook.json'))
    init.add_argument('--repos', type=Path, action='append', default=[])
    init.add_argument('--claude', type=Path, action='append', default=[])
    init.add_argument('--codex', type=Path, action='append', default=[])
    init.add_argument('--timezone', default='UTC')
    for command in ('doctor', 'brief'):
        p = sub.add_parser(command); p.add_argument('--config', type=Path, default=Path('daybook.json'))
        if command == 'brief':
            p.add_argument('--date', default='yesterday')
            p.add_argument('--now', help='Use a fixed ISO timestamp with timezone for synthetic timelines')
            p.add_argument('--model', choices=('none', 'claude', 'codex'))
            p.add_argument('--allow-network', action='store_true', help='Permit selected evidence to be sent by your CLI and network plugins for this invocation')
            p.add_argument('--refresh-local', action='store_true')
            p.add_argument('--retry-failed', action='store_true')
            p.add_argument('--debug-stages', action='store_true')
            p.add_argument('--json', action='store_true', help='Print a content-free run result instead of the brief')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.command == 'demo':
            from .demo import run
            result = run(args.out)
            print(Path(result['path']).read_text())
        elif args.command == 'init':
            path = args.config.expanduser().resolve()
            if path.exists():
                raise UserError('Configuration already exists; choose a new config file.')
            config = dayconfig.configure({'runtime_dir': str(path.parent / '.daybook-state'), 'timezone': args.timezone,
                'machines': [{'name': 'local', 'claude': [str(p.expanduser().resolve()) for p in args.claude],
                              'codex': [str(p.expanduser().resolve()) for p in args.codex]}] if args.claude or args.codex else [],
                'local': {'machine': 'local', 'repo_roots': [os.path.abspath(p.expanduser()) for p in args.repos]}})
            write(path, config)
            print(json.dumps({'status': 'initialized', 'network_default': False}))
        else:
            config = dayconfig.load(args.config)
            if args.command == 'doctor':
                result = doctor(config)
                print(json.dumps(result))
                return 0 if result['supported'] and result['tools']['git'] and not result['transcript_roots_missing'] and not result['repo_roots_missing'] else 1
            config['_allow_network'] = args.allow_network
            if args.model is not None:
                config['model'] = args.model
            config = dayconfig.configure(config)
            now = dayconfig.moment(args.now) if args.now else None
            if args.now and now is None:
                raise ValueError('now requires an ISO timestamp with timezone')
            boundary = Boundary(config.get('privacy', {}))
            model = None
            if config['model'] != 'none':
                from .model import CLIModel
                model = CLIModel(boundary, config)
            result = daybook.run(config, boundary, model, args.date, **({'now': now} if now else {}),
                                  no_model=config['model'] == 'none', refresh_sources=True,
                                  refresh_local=args.refresh_local, retry_failed=args.retry_failed,
                                  debug_stages=args.debug_stages)
            print(json.dumps(result) if args.json else Path(result['path']).read_text())
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({'status': 'failed', 'stage': getattr(exc, 'brief_stage', 'setup'), 'error_type': type(exc).__name__, 'message': explanation(exc)}))
        return 1
    return 0
