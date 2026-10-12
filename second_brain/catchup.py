# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Deterministic durable reminders. Missing observations never mean completion."""
from copy import deepcopy
from datetime import datetime, timedelta
import re
from .dayconfig import moment
from .storage import fingerprint, read


def history(root, delivery):
    """Import older briefs too: shortening a horizon must not erase reminders."""
    result, failures = [], []
    for path in sorted((root / 'briefs').glob('????-??-??.json')):
        if path.stem >= delivery:
            continue
        try:
            datetime.strptime(path.stem, '%Y-%m-%d')
            value = read(path)
            if not isinstance(value, dict) or not isinstance(value.get('states', {}), dict):
                raise ValueError('Invalid saved brief')
            result.append({**value, 'date': path.stem})
        except (OSError, ValueError, TypeError):
            failures.append({'stage': 'catchup.history', 'error_type': 'UnreadableBrief'})
    return result, failures


def widen(win, date, briefs):
    win = dict(win)
    if date == 'yesterday' and win['end'].weekday() == 0:
        previous = [b['date'] for b in briefs if not b.get('partial') and b.get('date', '') < win['delivery']]
        start_day = datetime.strptime(max(previous), '%Y-%m-%d').date() if previous else win['end'].date() - timedelta(days=3)
        if start_day < win['start'].date():
            win['start'] = datetime.combine(start_day, datetime.min.time(), win['zone'])
            win['lookback_start'] = min(win['lookback_start'], win['start'])
    return win


def normalized(text):
    return ' '.join(str(text).split()).casefold().rstrip('.!?')


def item_id(project, kind, key):
    return 'cu-' + fingerprint([project, kind, key]).replace('.', '')[:16]


def _date(at, win):
    value = moment(at)
    return value.astimezone(win['zone']).date().isoformat() if value else win['day'].isoformat()


def _within(at, win, config):
    value = moment(at)
    return bool(value and min(win['start'], win['end'] - timedelta(days=config['catchup_days'])) <= value < win['now'])


def _candidates(states, local, remote, evidence, win, config):
    result = []
    def add(project, kind, key, text, at, **details):
        result.append({'id': item_id(project, kind, key), 'project': project, 'kind': kind, 'key': key,
                       'text': text, 'first_seen': at, 'last_touched': at, **details})
    for project, entry in states.items():
        state = entry.get('state', {})
        at = entry.get('evidence_date') or win['day'].isoformat()
        # Model omission is not a resolution. All three state fields are retained.
        for kind in ('open', 'deferred', 'start_here'):
            values = [state.get(kind, {}).get('step', '')] if kind == 'start_here' else state.get(kind, [])
            for text in values:
                if not isinstance(text, str) or not text.strip():
                    continue
                if text == 'Re-read the last session handoff and choose the next task.':
                    continue
                add(project, 'state', normalized(text), text, at, state_kind=kind)
    for project, standing in local.items():
        for tree in standing.get('worktrees', []):
            if tree.get('modified') or tree.get('untracked'):
                add(project, 'worktree', tree['label'],
                    'Uncommitted changes in ' + tree['label'] + ' (' + (tree.get('branch') or 'branch unknown') + ').',
                    win['now'].date().isoformat(), head=tree.get('head', ''), branch=tree.get('branch', ''))
        prs = remote.get(project, {}).get('open_pull_requests', [])
        merged = {e.get('head_label') for e in evidence if e['project'] == project and e['kind'] == 'pull_request' and e.get('event') == 'merged'}
        pr_heads = {p.get('head_label') for p in prs} | merged
        for branch in standing.get('branches', []):
            name = branch['name']
            at = _date(branch.get('committed_at'), win)
            if branch.get('upstream') and branch.get('ahead', 0) > 0:
                add(project, 'ahead', name, name + ' has ' + str(branch['ahead']) + ' commits ahead of its upstream (as of last fetch).', at, head=branch.get('head', ''))
            if not _within(branch.get('committed_at'), win, config):
                continue
            if not branch.get('upstream'):
                add(project, 'no_upstream', name, name + ' has no upstream configured (publication unknown).', at, head=branch.get('head', ''))
            if name not in ('main', 'master', 'trunk') and name not in pr_heads and not branch.get('merged_into_primary'):
                add(project, 'branch', name, name + ' has commits but no collected PR or merge evidence (coverage may be incomplete).', at, head=branch.get('head', ''))
    latest = {}
    for event in evidence:
        if event['kind'] not in ('owner_message', 'closing_handoff') or not _within(event['event_at'], win, config):
            continue
        if event.get('verification') == 'conflict':
            continue
        key = (event['project'], event.get('session', event['id']))
        previous = latest.get(key)
        if previous is None or (event['event_at'], event.get('sequence', 0), event['id']) > (previous['event_at'], previous.get('sequence', 0), previous['id']):
            latest[key] = event
    for (project, session), event in latest.items():
        text = event.get('text', '').strip()
        if text.endswith('?') or re.search(r'(?:^|\n|[.!]\s+)(?:next(?: step)?|todo|follow[- ]up)\s*:', text, re.I):
            add(project, 'session', session + ':' + normalized(text), 'Unanswered question or next step: ' + text,
                _date(event['event_at'], win), session=session, cue=text, event_at=event['event_at'])
    return result


def _completion(item, events, local, remote, win, fresh_local):
    """Only specific evidence resolves a specific reminder; prose is never fuzzy-matched."""
    standing = local.get(item['project'], {})
    if fresh_local and standing.get('repo_available'):
        if item['kind'] == 'worktree':
            tree = next((t for t in standing.get('worktrees', []) if t['label'] == item['key']), None)
            if tree and tree.get('status_checked') and not tree.get('modified') and not tree.get('untracked') and tree.get('head') and tree['head'] != item.get('head'):
                return 'Clean checked worktree with a new commit head.'
        if item['kind'] in ('ahead', 'no_upstream'):
            branch = next((b for b in standing.get('branches', []) if b['name'] == item['key']), None)
            if branch and branch.get('upstream') and not branch.get('gone') and branch.get('ahead', 0) == 0 and branch.get('behind', 0) == 0:
                return 'Local upstream tracking shows this branch caught up (as of last fetch).'
        if item['kind'] == 'branch':
            branch = next((b for b in standing.get('branches', []) if b['name'] == item['key']), None)
            if branch and branch.get('merged_into_primary'):
                return 'Commit ancestry shows the branch merged into the primary branch.'
    for event in events:
        if event['project'] != item['project'] or event.get('verification') == 'conflict':
            continue
        if _date(event['event_at'], win) < item['last_touched']:
            continue
        if item['kind'] in ('branch', 'ahead', 'no_upstream') and event['kind'] == 'pull_request' and event.get('event') == 'merged' and event.get('head_label') == item['key']:
            return 'Merged PR #' + str(event['number']) + '.'
        if event['kind'] not in ('owner_message', 'closing_handoff', 'commit', 'local_commit'):
            continue
        text = event.get('subject', event.get('text', ''))
        matches = re.findall(r'(?:^|\n)\s*(?:done|completed|resolved|finished)\s*:\s*(.+)', text, re.I)
        target = item.get('cue', item['text'])
        if any(normalized(m) in (normalized(target), normalized(item['id'])) for m in matches):
            if event['kind'] == 'closing_handoff':
                failing = any(e['project'] == item['project'] and e['kind'] == 'test_result' and
                              (e.get('failed') or e.get('errors')) and e['event_at'] >= event['event_at'] for e in events)
                if failing:
                    continue
                return 'Session explicitly reports this item done (unverified).'
            return 'Explicit matching completion in ' + ('commit evidence.' if event['kind'] in ('commit', 'local_commit') else 'a human message.')
    return None


def reconcile(saved, briefs, states, local, remote, evidence, win, config, *, fresh_local=False):
    ledger = deepcopy(saved or {'version': 1, 'items': {}})
    items = ledger.setdefault('items', {})
    # Imported briefs cannot reopen a tombstone or count repetition as new activity.
    for brief in briefs:
        candidates = list(brief.get('catchup', {}).get('carried', [])) + list(brief.get('catchup', {}).get('stale', []))
        candidates += _candidates(brief.get('states', {}), {}, {}, [], win, config)
        for row in candidates:
            if row['id'] not in items:
                items[row['id']] = deepcopy(row)
            else:
                items[row['id']]['first_seen'] = min(items[row['id']]['first_seen'], row['first_seen'])
    for row in _candidates(states, local, remote, evidence, win, config):
        base = row['id']
        episode = next((i for i in items.values() if i.get('base_id') == base and not i.get('resolved_on')), None)
        if episode:
            row['id'] = episode['id']
        elif items.get(base, {}).get('resolved_on') and row['kind'] in ('worktree', 'ahead', 'no_upstream', 'branch') and fresh_local:
            previous = next((i for i in reversed(list(items.values())) if i.get('base_id', i['id']) == base), items[base])
            if row['kind'] == 'worktree' or row.get('head') != previous.get('resolved_head', previous.get('head')):
                row.update(base_id=base, id=item_id(row['project'], row['kind'], [base, previous['id'], previous['resolved_on'], row.get('head')]),
                           first_seen=win['now'].date().isoformat(), last_touched=win['now'].date().isoformat())
        if row['id'] not in items:
            items[row['id']] = row
        elif not items[row['id']].get('resolved_on'):
            old = items[row['id']]
            # Identical observations and stale model state do not reset the clock.
            changed = row.get('head') and row.get('head') != old.get('observed_head', old.get('head'))
            if changed:
                old['last_touched'] = max(old['last_touched'], row['last_touched'])
                old['observed_head'] = row['head']
            old['text'] = row['text']
    for item in items.values():
        if not item.get('resolved_on'):
            reason = _completion(item, evidence, local, remote, win, fresh_local)
            if reason:
                item.update(resolved_on=win['delivery'], resolution=reason)
                standing = local.get(item['project'], {})
                rows = standing.get('worktrees' if item['kind'] == 'worktree' else 'branches', [])
                field = 'label' if item['kind'] == 'worktree' else 'name'
                matched = next((r for r in rows if r.get(field) == item['key']), {})
                item['resolved_head'] = matched.get('head', item.get('head', ''))
    carried, stale, resolved = [], [], []
    delivery_day = win['end'].date()
    for item in sorted(items.values(), key=lambda i: (i['first_seen'], i['project'], i['id'])):
        row = deepcopy(item)
        row['carried_days'] = max(0, (delivery_day - datetime.strptime(row['first_seen'], '%Y-%m-%d').date()).days)
        row['untouched_days'] = max(0, (delivery_day - datetime.strptime(row['last_touched'], '%Y-%m-%d').date()).days)
        if row.get('resolved_on'):
            if row['resolved_on'] == win['delivery']:
                resolved.append(row)
        else:
            (stale if row['untouched_days'] > config['catchup_days'] else carried).append(row)
    first = win['end'].date() - timedelta(days=config['catchup_days'])
    days = [(first + timedelta(days=n)).isoformat() for n in range(config['catchup_days'])]
    available = {b['date'] for b in briefs if b.get('date') and not b.get('partial')}
    previous = max((d for d in available if d < win['delivery']), default=None)
    gap = ''
    missing = [d for d in days if d not in available]
    if missing:
        if previous:
            gap = 'no brief since ' + datetime.strptime(previous, '%Y-%m-%d').strftime('%a') + ' ' + previous
        else:
            gap = 'no earlier complete brief recorded'
        gap += '; this covers ' + win['start'].strftime('%a %Y-%m-%d') + '–' + (win['end'] - timedelta(days=1)).strftime('%a %Y-%m-%d')
    return ledger, {'carried': carried, 'stale': stale, 'resolved': resolved, 'missing_days': missing, 'gap_note': gap}


def markdown(report):
    def line(row):
        text = row['text'].replace('\n', ' ').replace('\r', ' ')
        weekday = datetime.strptime(row['first_seen'], '%Y-%m-%d').strftime('%a')
        return '- [' + row['id'] + '] **' + row['project'] + '** — ' + text + '; first seen ' + weekday + ', carried ' + str(row['carried_days']) + ' days'
    lines = ["## Check you didn't drop anything"]
    if report['gap_note']:
        lines.append(report['gap_note'] + '.')
        lines.append('Missing complete briefs in reminder lookback: ' + ', '.join(report['missing_days']) + '.')
    lines += [line(row) for row in report['carried']] or ['No recent unresolved reminders recorded.']
    if report['stale']:
        for days in sorted({r['untouched_days'] for r in report['stale']}):
            lines += ['', '### Stale (not touched for ' + str(days) + ' days)']
            lines += [line(row) for row in report['stale'] if row['untouched_days'] == days]
    if report['resolved']:
        lines += ['', '### Resolved since']
        lines += [line(row) + ' — ' + row['resolution'] for row in report['resolved']]
    lines.append('Missing or sampled evidence never proves a reminder finished. Deferred reminders stay deferred; they are not instructions to resume work.')
    return '\n'.join(lines)
