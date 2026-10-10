# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Deterministic, whole-record prompt compaction after the privacy boundary."""
from collections import defaultdict, deque
from copy import deepcopy
import json

VERSION = 1


def size(value):
    return len(json.dumps(value, ensure_ascii=False))


def compact_facts(facts, limit):
    result, lists = {}, []
    total = 0
    for section, value in facts.items():
        if not isinstance(value, dict):
            result[section] = deepcopy(value)
            continue
        result[section] = {}
        for key, item in value.items():
            if isinstance(item, list):
                result[section][key] = []
                total += value.get(key + '_total', len(item))
                # Facts arrive newest first. Include failing checks ahead of other PRs.
                rows = sorted(item, key=lambda r: not bool(r.get('checks', {}).get('failure')))
                lists.append((section, key, deque(rows)))
            else:
                result[section][key] = deepcopy(item)
    # Counts and day aggregates take priority over individual detail rows.
    if size(result) > limit:
        for section in ('yesterday', 'today', 'standing'):
            result[section] = {k: v for k, v in result.get(section, {}).items()
                               if isinstance(v, (int, float, bool)) or k == 'days'}
    kept = 0
    while any(rows for _, _, rows in lists):
        for section, key, rows in lists:
            if not rows:
                continue
            item = rows.popleft()
            target = result[section].setdefault(key, [])
            target.append(item)
            if size(result) <= limit:
                kept += 1
            else:
                target.pop()
    return result, {'fact_rows_total': total, 'fact_rows_selected': kept,
                    'fact_rows_omitted': total - kept}


def _source(e):
    # Session line numbers must not allow a busy session to escape its cap.
    return ('session', e['session']) if e.get('session') else (e['kind'], e['event_at'][:10])


def representative(evidence, per_source=8):
    """Round-robin kinds and sources, newest first within each source."""
    grouped = defaultdict(lambda: defaultdict(deque))
    for e in sorted(evidence, key=lambda e: (e['event_at'], e['id']), reverse=True):
        grouped[(e['period'], e['kind'])][_source(e)].append(e)
    periods = {'yesterday': 0, 'today': 1, 'context': 2}
    kinds = {'closing_handoff': 0, 'owner_message': 1}
    buckets = []
    for key in sorted(grouped, key=lambda k: (periods.get(k[0], 3), kinds.get(k[1], 2), k[1])):
        sources = grouped[key]
        queue = deque()
        ordered = sorted(sources, key=lambda s: (sources[s][0]['event_at'], s), reverse=True)
        while any(sources.values()):
            for source in ordered:
                if sources[source]:
                    queue.append(sources[source].popleft())
        buckets.append(queue)
    counts, ordered = defaultdict(int), []
    while any(buckets):
        for bucket in buckets:
            if not bucket:
                continue
            row = bucket.popleft()
            source = _source(row)
            if counts[source] < per_source:
                ordered.append(row)
                counts[source] += 1
    return ordered


def evidence_counts(evidence):
    groups = {}
    for e in evidence:
        key = (e['period'], e['event_at'][:10], e['kind'])
        group = groups.setdefault(key, {'period': key[0], 'day': key[1], 'kind': key[2],
                                        'records': 0, 'latest_at': e['event_at']})
        group['records'] += 1
        group['latest_at'] = max(group['latest_at'], e['event_at'])
    return [groups[k] for k in sorted(groups)]
