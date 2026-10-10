# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Conservative comparisons of reported handoffs with bounded local evidence."""
import re


def test_summary(row, tool):
    """Read native tool output only; retain pytest counts, never raw output."""
    if tool == 'claude':
        blocks = row.get('message', {}).get('content', [])
        texts = [b.get('content', '') for b in blocks if isinstance(b, dict) and b.get('type') == 'tool_result'] if isinstance(blocks, list) else []
    else:
        payload = row.get('payload', {})
        texts = [payload.get('output', '')] if row.get('type') == 'response_item' and payload.get('type') in ('function_call_output', 'custom_tool_call_output') else []
    result = None
    for text in texts:
        if isinstance(text, list):
            text = '\n'.join(b.get('text', '') for b in text if isinstance(b, dict) and isinstance(b.get('text'), str))
        if not isinstance(text, str):
            continue
        for line in text.splitlines():
            # Require a summary-shaped line, not a quoted request or source excerpt.
            summary = line.strip().strip('=').strip()
            if not re.fullmatch(r'\d+ (?:failed|passed|errors?|skipped|deselected|xfailed|xpassed)(?:, \d+ (?:failed|passed|errors?|skipped|deselected|xfailed|xpassed))*(?: in [\d.]+s(?: \([^\n]*\))?)?', summary):
                continue
            counts = {key: int(number) for number, key in re.findall(r'(\d+) (failed|passed|errors?)\b', summary)}
            result = {'failed': counts.get('failed', 0), 'passed': counts.get('passed', 0),
                      'errors': counts.get('error', 0) + counts.get('errors', 0)}
    return result


def claims(text):
    """Retain rule matches for every handoff without retaining all assistant prose."""
    passing = bool(re.search(r'\ball (?:\d+ )?tests (?:pass(?:ed)?|are passing)\b|\btests? (?:all pass(?:ed)?|pass(?:ed)?|are passing)\b|\ball (?:checks )?(?:green|passing)\b', text, re.I))
    completed = re.search(r'\b(?:I |we )?(?:finished|completed|implemented) (?:the |a |an )?([^.!\n]+)', text, re.I)
    words = sorted(set(re.findall(r'[a-z0-9]+', completed.group(1).lower())) -
                   {'the', 'a', 'an', 'and', 'all', 'work', 'change', 'changes', 'synthetic'})[:40] if completed else []
    return {'passing': passing, 'completed_words': words} if passing or words else {}


def assess(facts, evidence):
    ordered = sorted(evidence, key=lambda e: (e['event_at'], e.get('sequence', 0), e['id']))
    handoffs = [e for e in ordered if e['kind'] in ('closing_handoff', 'handoff_claim')]
    owners = [e for e in ordered if e['kind'] == 'owner_message']
    results = [e for e in ordered if e['kind'] == 'test_result']
    latest = {}
    for result in results:
        latest[result['session']] = result
    failures = [r for r in latest.values() if r['failed'] or r['errors']]
    facts['latest_test_failure'] = failures[-1] if failures else None
    facts['last_owner_request'] = owners[-1] if owners else None
    contradictions = []
    for handoff in handoffs:
        claim = handoff.get('claims', claims(handoff.get('text', '')))
        result = latest.get(handoff.get('session'))
        if claim.get('passing') and result and (result['failed'] or result['errors']):
            contradictions.append('Assistant claims all tests pass but the last test run in this transcript reports '
                                  + str(result['failed']) + ' failed and ' + str(result['errors']) + ' errors (reported tool output).')
        words = set(claim.get('completed_words', []))
        if words and facts['standing'].get('repo_available'):
            commits = [e for e in evidence if e['kind'] in ('commit', 'local_commit')]
            if not any(words & set(re.findall(r'[a-z0-9]+', e.get('subject', e.get('text', '')).lower())) for e in commits):
                contradictions.append('Assistant claims a completed change but the repo does not show a matching commit in the collected window; uncommitted or omitted work may still exist.')
    facts['contradictions'] = list(dict.fromkeys(contradictions))
    return facts


def preferred_start(facts, default):
    request, failure = facts.get('last_owner_request'), facts.get('latest_test_failure')
    if not request and not failure:
        return default
    start = {**default, 'where': {}, 'sources': [], 'source': 'rule'}
    if request:
        start['step'] = 'Revisit your last request: ' + ' '.join(request['text'].split())[:210]
        start['why'] = ('The last reported test run is failing; verify the request against that evidence.' if failure else
                        'The collected evidence does not establish that this human request was completed.')
    else:
        start['step'] = 'Investigate the last reported failing test run and rerun its tests.'
        start['why'] = 'Tool output reports failures; an assistant handoff does not override that evidence.'
    return start
