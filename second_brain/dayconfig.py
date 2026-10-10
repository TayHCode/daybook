# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Generic explicit local inputs and calendar windows. Modified for the core split."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

DEFAULTS = {
    'timezone': 'UTC', 'active_days': 7, 'context_days': 1,
    'display_names': 'safe', 'projects': [], 'machines': [], 'injected_patterns': [],
    'catalog': {'include_forks': False, 'ignore': [], 'pin': []},
    'local': {'machine': 'local', 'repo_roots': [], 'max_worktrees_per_repo': 80},
    'workspace_patterns': ['**/worktrees/{project}/*', '**/projects/{project}', '**/repos/{project}', '**/REPOS/{project}', '**/.t3/worktrees/{project}/*', '**/REPOS/.worktrees/{project}/*'],
    'plugins': [], 'model': 'none', 'model_timeout': 120, 'worker_seconds': 900,
    'max_project_calls': 12, 'project_max_chars': 60000, 'overview_max_chars': 60000,
    'max_transcript_files': 4000, 'max_line_bytes': 2000000,
    'evidence_retention_days': 10, 'facts_list_cap': 30, 'git_timeout': 20,
}


def configure(value):
    if any(k in value for k in ('x', 'github', 'posting_angles', 'topics')):
        raise ValueError('Integration configuration belongs in an explicitly selected plugin')
    config = deepcopy(DEFAULTS)
    config.update(value)
    for key in ('catalog', 'local'):
        config[key] = {**DEFAULTS[key], **value.get(key, {})}
    if not config.get('runtime_dir'):
        raise ValueError('runtime_dir')
    for key, field in [('machines', 'name'), ('projects', 'id')]:
        names = [row.get(field) for row in config[key]]
        if any(not isinstance(n, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', n) for n in names) or len(names) != len(set(names)):
            raise ValueError(key + '.' + field)
    for key in ('active_days', 'context_days', 'max_project_calls', 'max_transcript_files', 'max_line_bytes',
                'evidence_retention_days', 'facts_list_cap', 'project_max_chars', 'overview_max_chars',
                'worker_seconds', 'git_timeout', 'model_timeout'):
        minimum = 0 if key in ('context_days', 'max_project_calls') else 1
        if type(config[key]) is not int or config[key] < minimum:
            raise ValueError(key)
    try:
        ZoneInfo(config['timezone'])
        for pattern in config['injected_patterns']:
            re.compile(pattern)
    except Exception:
        raise ValueError('timezone/injected_patterns') from None
    if config['display_names'] not in ('safe', 'real') or config['model'] not in ('none', 'claude', 'codex'):
        raise ValueError('display_names/model')
    if not isinstance(config['plugins'], list):
        raise ValueError('plugins')
    for item in config['plugins']:
        if not isinstance(item, dict) or not isinstance(item.get('module'), str) or ':' not in item['module']:
            raise ValueError('plugins.module')
        if not isinstance(item.get('capabilities', []), list) or set(item.get('capabilities', [])) - {'network'}:
            raise ValueError('plugins.capabilities')
        if not isinstance(item.get('options', {}), dict):
            raise ValueError('plugins.options')
    return config


def load(path):
    return configure(json.loads(Path(path).expanduser().read_text()))


def moment(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def window(config, date, now):
    zone = ZoneInfo(config['timezone'])
    now = (now or datetime.now(zone)).astimezone(zone)
    day = now.date() - timedelta(days=1) if date == 'yesterday' else datetime.strptime(date, '%Y-%m-%d').date()
    start = datetime.combine(day, datetime.min.time(), zone)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone)
    return {'zone': zone, 'day': day, 'start': start, 'end': end,
            'lookback_start': start - timedelta(days=config['active_days'] - 1),
            'delivery': end.date().isoformat(), 'now': now}


def period(at, win):
    return 'context' if at < win['start'] else 'yesterday' if at < win['end'] else 'today'
