# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Read-only streaming transcript collection with private incremental caches."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from . import authorship, truthfulness
from .dayconfig import moment, period
from .storage import fingerprint, read, write

AUTHORSHIP_VERSION = 8
try:
    from .privacy import allowed
except ImportError:
    def allowed(boundary, value, *, identity=''):
        return True


def discover(config, lookback_start, now):
    found, coverage = {}, {}
    for machine in config['machines']:
        tools = coverage.setdefault(machine['name'], {})
        for tool in ('claude', 'codex'):
            cov = tools[tool] = {'status': 'ok', 'files_total': 0, 'files_in_lookback': 0,
                                'newest_mtime': None, 'sources_unavailable': 0, 'files_omitted': 0}
            for root_name in machine.get(tool, []):
                root = Path(root_name).expanduser().resolve()
                try:
                    if not root.is_dir():
                        raise OSError()
                    for path in root.rglob('*.jsonl'):
                        path = path.resolve()
                        stat = path.stat()
                        key = (machine['name'], tool, str(path))
                        file = {'path': path, 'root': root, 'machine': machine['name'], 'tool': tool,
                                'size': stat.st_size, 'mtime': stat.st_mtime,
                                'subagent': 'subagents' in path.parts}
                        if key not in found or stat.st_size > found[key]['size']:
                            found[key] = file
                except OSError:
                    cov['status'] = 'source_unavailable'
                    cov['sources_unavailable'] += 1
    kept = []
    for file in found.values():
        cov = coverage[file['machine']][file['tool']]
        cov['files_total'] += 1
        stamp = datetime.fromtimestamp(file['mtime'], timezone.utc).isoformat()
        cov['newest_mtime'] = max(cov['newest_mtime'] or stamp, stamp)
        if file['mtime'] >= lookback_start.timestamp():
            cov['files_in_lookback'] += 1
            kept.append(file)
    kept.sort(key=lambda f: (-f['mtime'], str(f['path'])))
    cap = config['max_transcript_files']
    for file in kept[cap:]:
        coverage[file['machine']][file['tool']]['files_omitted'] += 1
    return kept[:cap], coverage


def _tail(handle, offset):
    handle.seek(max(0, offset - 4096))
    return hashlib.sha256(handle.read(min(offset, 4096))).hexdigest()


def _stat_stamp(stat):
    return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino]


def _source_stamp(path, stat=None):
    """Never trust timestamps alone; bound the probe for large transcripts."""
    stat = stat or path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        size = stat.st_size
        if size <= 1024 * 1024:
            digest.update(handle.read(size))
        else:
            # Head, tail and evenly spaced interior samples (at most 320 KiB).
            width = 64 * 1024
            for offset in (0, size // 4, size // 2, 3 * size // 4, size - width):
                handle.seek(offset)
                digest.update(handle.read(width))
    if _stat_stamp(path.stat()) != _stat_stamp(stat):
        raise OSError('Transcript changed while probing')
    return [*_stat_stamp(stat), digest.hexdigest()]


def _content_hash(handle, size, previous_size):
    """Bound memory, hash every byte, and verify the entire old append prefix."""
    handle.seek(0)
    digest = hashlib.sha256()
    prefix = None
    position = 0
    for end in (min(size, previous_size), size):
        while position < end:
            chunk = handle.read(min(1024 * 1024, end - position))
            if not chunk:
                raise OSError('Transcript changed while hashing')
            digest.update(chunk)
            position += len(chunk)
        if prefix is None:
            prefix = digest.hexdigest()
    return digest.hexdigest(), prefix


def _empty(subagent):
    return {'authorship_version': AUTHORSHIP_VERSION, 'attributions': [],
            'size': 0, 'offset': 0, 'line': 0, 'meta': {'cwd': '', 'branches': [],
            'session_id': '', 'subagent': subagent}, 'days': {}, 'messages': [], 'test_results': [],
            'skipped_lines': 0, 'oversized_lines': 0, 'privacy_dropped': 0}


def _row(row, tool, meta, boundary, config):
    payload = row.get('payload', {}) if tool == 'codex' else row
    if tool == 'codex' and row.get('type') == 'session_meta' and authorship.subagent_source(payload.get('source')):
        meta['subagent'] = True
    cwd = payload.get('cwd')
    if cwd:
        if config.get('_local_source') and cwd.startswith('/'):
            cwd = str(Path(cwd).expanduser().resolve())
        # Resolve before scrubbing; only the safe label and safe cwd persist.
        resolver = config.get('_resolve')
        safe_cwd = boundary.clean(cwd)
        if safe_cwd != meta['cwd']:
            meta['branches'] = []
            meta.pop('project', None)
        if resolver:
            meta['project'] = resolver(cwd)
        meta['cwd'] = safe_cwd
    branch = payload.get('git', {}).get('branch') if tool == 'codex' else row.get('gitBranch')
    if branch:
        branch = boundary.clean(branch)
        if branch not in meta['branches']:
            meta['branches'].append(branch)
    sid = payload.get('id') if row.get('type') == 'session_meta' else row.get('sessionId')
    if sid:
        meta['session_id'] = fingerprint(sid)[:16]
    if tool == 'claude':
        role = row.get('type')
        if role not in ('user', 'assistant'):
            return None
        content = row.get('message', {}).get('content', '')
        types = ('text',)
    else:
        role = payload.get('role')
        if row.get('type') != 'response_item' or payload.get('type') != 'message' or role not in ('user', 'assistant'):
            return None
        content = payload.get('content', [])
        types = ('input_text',) if role == 'user' else ('output_text',)
    text = content if isinstance(content, str) else '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') in types)
    handoff = role == 'assistant' and payload.get('channel') not in ('analysis', 'commentary')
    return role, text, moment(row.get('timestamp')), handoff


def _prune(entry, win, config):
    cutoff = win['now'] - timedelta(days=config['evidence_retention_days'])
    entry['test_results'] = [r for r in entry.get('test_results', []) if moment(r['at']) >= cutoff]
    latest = {_stream_key(r): r['line'] for r in entry['attributions'] if r.get('message_key')}
    rows = [m for m in entry['messages'] if moment(m['at']) >= cutoff and
            (not m.get('stream_key') or latest.get(tuple(m['stream_key'])) == m['line'])]
    assistants = {}
    for row in rows:
        if row['role'] == 'assistant':
            day = (moment(row['at']).astimezone(win['zone']).date().isoformat(), row.get('cwd'))
            assistants.setdefault(day, []).append(row)
    last = {row['line'] for values in assistants.values()
            for row in sorted(values, key=lambda r: (moment(r['at']), r['line']))[-2:]}
    entry['messages'] = [r for r in rows if r['role'] == 'user' or r['line'] in last]
    oldest = (win['now'] - timedelta(days=30)).date().isoformat()
    entry['days'] = {d: v for d, v in entry['days'].items() if d >= oldest}
    cutoff = min(win['lookback_start'], win['now'] - timedelta(days=max(30, config['evidence_retention_days'])))
    entry['attributions'] = [r for r in entry['attributions'] if moment(r['at']) >= cutoff]
    return entry


def _stream_key(row):
    return (row.get('project'), row.get('cwd'), row['message_key'], row['role'])


def _predecessors(entry):
    """Within one file the last native occurrence is the stream's current text."""
    result = {}
    for row in (entry or {}).get('attributions', []):
        if row.get('message_key') and row.get('content_key'):
            result[_stream_key(row)] = row
    return result


def scan(path, tool, subagent, entry, config, boundary, win):
    stat = path.stat()
    if entry and (entry.get('authorship_version') != AUTHORSHIP_VERSION or
                  entry.get('injected_patterns') != config['injected_patterns']):
        entry = None
    stamp = _source_stamp(path, stat)
    if entry and entry.get('content_sha') and entry.get('source_stamp') == stamp:
        return _prune(deepcopy(entry), win, config)
    with path.open('rb') as handle:
        content_sha, prefix_sha = _content_hash(handle, stat.st_size, entry['size'] if entry else 0)
        if entry and entry['size'] == stat.st_size and entry.get('content_sha') == content_sha:
            if _source_stamp(path) != stamp:
                raise OSError('Transcript changed while hashing')
            entry = deepcopy(entry)
            entry.update(source_stamp=stamp, mtime_ns=stat.st_mtime_ns)
            return _prune(entry, win, config)
        previous_rows = _predecessors(entry)
        if (not entry or stat.st_size <= entry['size'] or prefix_sha != entry.get('content_sha') or
                _tail(handle, entry['offset']) != entry.get('tail_sha')):
            entry = _empty(subagent)
        else:
            entry = deepcopy(entry)
        stream_rows = _predecessors(entry)
        handle.seek(entry['offset'])
        while True:
            offset = handle.tell()
            line = handle.readline(config['max_line_bytes'] + 1)
            if not line:
                break
            oversized = len(line) > config['max_line_bytes']
            if oversized:
                while line and not line.endswith(b'\n'):
                    line = handle.readline(config['max_line_bytes'] + 1)
            if not line.endswith(b'\n'):
                handle.seek(offset)
                break
            entry['offset'] = handle.tell()
            entry['line'] += 1
            if oversized:
                entry['oversized_lines'] += 1
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                entry['skipped_lines'] += 1
                continue
            try:
                parsed = _row(row, tool, entry['meta'], boundary, config)
                result = truthfulness.test_summary(row, tool)
                at = moment(row.get('timestamp'))
                if result is not None and at is not None:
                    entry['test_results'].append({'line': entry['line'], 'at': at.isoformat(),
                        'project': entry['meta'].get('project'), 'cwd': entry['meta']['cwd'], **result})
            except Exception:
                entry['privacy_dropped'] += 1
                continue
            if not parsed:
                continue
            role, text, at, handoff = parsed
            structural, reason = authorship.provenance(row, tool, entry['meta']['subagent'])
            if at is None or (not text and structural is None):
                continue
            clean_text, author = authorship.classify(text, role, extra_injected=config['injected_patterns'],
                                                    explicit_owner=structural == 'owner')
            if structural and structural != 'owner':
                author = structural
            elif author not in ('owner', 'unattributed'):
                reason = 'text_rule'
            # Equality is used only for deduplication, within an authorship class.
            # A lossy scrubber placeholder must not collapse distinct requests.
            try:
                cleaned = boundary.clean(clean_text if author == 'owner' else text)
                if not isinstance(cleaned, str):
                    raise ValueError()
            except Exception:
                entry['privacy_dropped'] += 1
                continue
            entry['attributions'].append({'line': entry['line'], 'at': at.isoformat(),
                                          'role': role, 'author': author,
                                          'reason': reason,
                                          'project': entry['meta'].get('project'), 'cwd': entry['meta']['cwd'],
                                          'branches': list(entry['meta']['branches']),
                                          'message_key': fingerprint(row.get('uuid') or row.get('payload', {}).get('id'))
                                              if row.get('uuid') or row.get('payload', {}).get('id') else None,
                                          'content_key': fingerprint(clean_text if author == 'owner' else text),
                                          'text_key': authorship.text_key(clean_text if author == 'owner' else text)})
            attribution = entry['attributions'][-1]
            attribution['sequence'] = attribution['line']
            same_stream = stream_rows.get(_stream_key(attribution)) if attribution['message_key'] else None
            if same_stream and same_stream['content_key'] == attribution['content_key']:
                attribution['sequence'] = same_stream.get('sequence', same_stream['line'])
            if handoff and author == 'agent':
                claim = truthfulness.claims(cleaned)
                if claim:
                    attribution['claims'] = claim
            prior = previous_rows.get(_stream_key(attribution)) if attribution['message_key'] else None
            if prior:
                attribution['supersedes'] = sorted((set(prior.get('supersedes', [])) |
                    {prior['content_key']}) - {attribution['content_key']})
            if attribution['message_key']:
                previous_rows[_stream_key(attribution)] = attribution
                stream_rows[_stream_key(attribution)] = attribution
            day = at.astimezone(win['zone']).date().isoformat()
            counts = entry['days'].setdefault(day, {'owner_messages': 0, 'agent_user_messages': 0,
                                                   'unattributed_messages': 0, 'assistant_messages': 0,
                                                   'first_at': at.isoformat(), 'last_at': at.isoformat()})
            key = ('assistant_messages' if role == 'assistant' else 'owner_messages' if author == 'owner'
                   else 'unattributed_messages' if author == 'unattributed' else 'agent_user_messages')
            counts[key] += 1
            counts['first_at'] = min(counts['first_at'], at.isoformat())
            counts['last_at'] = max(counts['last_at'], at.isoformat())
            if at >= win['now'] - timedelta(days=config['evidence_retention_days']) and (author == 'owner' or
                    (handoff and author == 'agent')):
                entry['messages'].append({'line': entry['line'], 'at': at.isoformat(),
                                          'role': role, 'author': author, 'text': cleaned, 'attribution': reason,
                                          'cwd': attribution['cwd'], 'sequence': attribution['sequence'],
                                          'stream_key': list(_stream_key(attribution)) if attribution['message_key'] else None})
        if _source_stamp(path) != stamp:
            raise OSError('Transcript changed while scanning')
        entry.update(size=stat.st_size, mtime_ns=stat.st_mtime_ns, source_stamp=stamp, content_sha=content_sha,
                     tail_sha=_tail(handle, entry['offset']), injected_patterns=config['injected_patterns'])
    return _prune(entry, win, config)


def _row_identity(project, meta, row):
    if row['message_key']:
        # Unmarked mirrors cannot hide the stronger provenance of the same turn.
        author = 'user' if row['author'] in ('owner', 'unattributed') else row['author']
        return fingerprint([project or row.get('cwd', meta['cwd']), row['message_key'], row['role'], author])
    return fingerprint([project or row.get('cwd', meta['cwd']), row['at'], row['role'], row['author'], row['text_key']])


def _file_identity(file):
    return fingerprint([file['machine'], file['tool'], str(file['path'].resolve())])


def _reconcile(collected, registry, win, old_origins, old_versions):
    """Resolve native turns before counting; content fallback never joins distinct IDs.

    Physical line order establishes stream revisions within one file; an observed
    edit establishes revisions across runs. Sync arrival, mtimes and path order
    cannot establish version order between different files.
    Conflicts with no such order are one unattributed turn, never two owner turns.
    """
    groups = {}
    for file, entry in collected:
        meta = entry['meta']
        session = _file_identity(file)[:16]
        messages = {r['line']: r for r in entry['messages']}
        for row in entry['attributions']:
            if win['lookback_start'] <= moment(row['at']) < win['now']:
                project = row.get('project') or registry.resolve(row.get('cwd', meta['cwd']), file['machine'])
                identity = _row_identity(project, meta, row)
                groups.setdefault(identity, []).append({'origin': _file_identity(file), 'row': row,
                    'source': 'session:' + session + ':' + str(row['line']),
                    'message': messages.get(row['line']), 'project': project or row.get('cwd', meta['cwd'])})
    # Hash fallback also covers an ID-less mirror of a single identified turn.
    native = {}
    for identity, copies in groups.items():
        for copy in copies:
            r = copy['row']
            if r['message_key']:
                key = (copy['project'], r['at'], r['role'], r['author'], r['text_key'])
                native.setdefault(key, set()).add(identity)
    aliases = {}
    for identity, copies in list(groups.items()):
        r = copies[0]['row']
        key = (copies[0]['project'], r['at'], r['role'], r['author'], r['text_key'])
        matches = native.get(key, set())
        if not r['message_key'] and len(matches) == 1:
            target = next(iter(matches))
            groups[target].extend(groups.pop(identity))
            aliases[identity] = target
    selected, origins, versions = {}, {}, {}
    for identity, copies in groups.items():
        prior_origin = old_origins.get(identity)
        if prior_origin is None:
            prior_origin = next((old_origins[a] for a, target in aliases.items()
                                 if target == identity and a in old_origins), None)
        anchor = next((c for c in copies if c['origin'] == prior_origin), copies[0])
        # Keep the stable source pointer, but only the last native occurrence
        # per physical file can compete with other files' final versions.
        if any(c['row']['message_key'] for c in copies):
            last = {}
            for copy in copies:
                previous = last.get(copy['origin'])
                if previous is None or (bool(copy['row']['message_key']), copy['row']['line']) > \
                        (bool(previous['row']['message_key']), previous['row']['line']):
                    last[copy['origin']] = copy
            copies = list(last.values())
        values = {c['row']['content_key'] for c in copies}
        prior = old_versions.get(identity, {})
        current = prior.get('current')
        retired = set(prior.get('superseded', []))
        successors = {c['row']['content_key'] for c in copies
                      if values - {c['row']['content_key']} <= set(c['row'].get('supersedes', []))}
        if not any(c['row']['message_key'] for c in copies):
            chosen, conflict = anchor['row']['content_key'], False
        elif len(values) == 1:
            chosen = next(iter(values))
            conflict = bool(current and chosen != current and chosen in retired and
                            not any(current in c['row'].get('supersedes', []) for c in copies))
        elif len(successors) == 1 and any(c['row']['content_key'] in successors and
                (c['row']['content_key'] not in retired or current in c['row'].get('supersedes', []))
                for c in copies):
            chosen = next(iter(successors))
            conflict = False
        elif current in values and values - {current} <= retired:
            chosen, conflict = current, False
        else:
            # Timestamps choose which reported handoff to display; they never
            # settle revision order between disagreeing files.
            candidate = min(copies, key=lambda c: (c['message'] is None,
                            -moment(c['row']['at']).timestamp(), c['row']['content_key'], c['origin']))
            chosen, conflict = candidate['row']['content_key'], True
        choices = [c for c in copies if c['row']['content_key'] == chosen]
        winner = min(choices, key=lambda c: (c['row']['author'] != 'owner', c['message'] is None, c is not anchor))
        row = {**winner['row'], 'line': anchor['row']['line']}
        message = deepcopy(winner['message'])
        if conflict:
            row.update(author='unattributed' if row['role'] == 'user' else row['author'], reason='version_conflict')
            if row['role'] == 'user':
                message = None
        elif row['message_key']:
            retired.update(values - {chosen})
            retired.update(row.get('supersedes', []))
            retired.discard(chosen)
        versions[identity] = {'current': current if conflict and current else chosen,
                              'superseded': sorted(retired)}
        if message:
            message['line'] = row['line']
        competing = {}
        if conflict and row['role'] == 'assistant':
            for copy in sorted(copies, key=lambda c: (c['row']['content_key'], c['origin'])):
                if copy['message'] and copy['row']['content_key'] != chosen:
                    competing.setdefault(copy['row']['content_key'],
                                         {'source': copy['source'], 'text': copy['message']['text']})
        selected[identity] = {**anchor, 'row': row, 'message': message, 'conflict': conflict,
                              'competing_handoffs': list(competing.values())}
        origins[identity] = anchor['origin']
    return selected, origins, versions, aliases


def collect(config, registry, win, boundary, cache_dir):
    files, coverage = discover(config, win['lookback_start'], win['now'])
    sessions, evidence = [], []
    collected = []
    for machine, tools in coverage.items():
        for tool, cov in tools.items():
            cov.update(files_scanned=0, files_unchanged=0, oversized_lines=0, skipped_lines=0,
                       privacy_dropped=0, sessions_yesterday=0, owner_messages_yesterday=0,
                       owner_explicit_yesterday=0, owner_inferred_yesterday=0,
                       excluded_user_yesterday=0, unresolved_user_yesterday=0, unattributed_user_yesterday=0,
                       attribution_reasons_yesterday={}, duplicate_rows_yesterday=0, version_conflicts_yesterday=0,
                       unassigned={'count': 0, 'cwd_labels': []})
            cache_path = Path(cache_dir) / (machine + '.' + tool + '.json')
            old = read(cache_path, {})
            cache = deepcopy(old)
            seen = set()
            for file in (f for f in files if f['machine'] == machine and f['tool'] == tool):
                # Canonical opaque keys survive symlink and overlapping-root forms.
                key = fingerprint(str(file['path'].resolve()))
                seen.add(key)
                previous = cache.get(key)
                try:
                    entry = scan(file['path'], tool, file['subagent'], previous,
                                 {**config, '_resolve': lambda cwd: registry.resolve(cwd, machine),
                                  '_local_source': machine == config['local']['machine']}, boundary, win)
                except OSError:
                    cov['status'] = 'source_unavailable'
                    continue
                cache[key] = entry
                unchanged = (previous and previous.get('authorship_version') == AUTHORSHIP_VERSION
                             and previous.get('injected_patterns') == config['injected_patterns']
                             and previous.get('content_sha') == entry['content_sha'])
                cov['files_unchanged' if unchanged else 'files_scanned'] += 1
                for counter in ('oversized_lines', 'skipped_lines', 'privacy_dropped'):
                    cov[counter] += entry[counter]
                collected.append((file, entry))
            cutoff = (win['now'] - timedelta(days=30)).timestamp() * 1e9
            cache = {k: v for k, v in cache.items() if k in seen or v['mtime_ns'] >= cutoff}
            if cache != old:
                write(cache_path, cache)
    collected.sort(key=lambda pair: (pair[0]['machine'], pair[0]['tool'], str(pair[0]['path'])))
    origins_path = Path(cache_dir) / 'dedup-origins.json'
    old_origins = read(origins_path, {})
    # Keep the previous contributing copy while it is available. A newly synced
    # mirror sorting earlier must not change machine credit or source pointers.
    # Only opaque IDs for current-window rows persist, never new source text.
    versions_path = Path(cache_dir) / 'dedup-versions.json'
    old_versions = read(versions_path, {})
    selected, origins, versions, aliases = _reconcile(collected, registry, win, old_origins, old_versions)
    seen_rows = set()
    for file, entry in collected:
        machine, tool = file['machine'], file['tool']
        origin = _file_identity(file)
        cov = coverage[machine][tool]
        meta = entry['meta']
        session = fingerprint([machine, tool, str(file['path'].resolve())])[:16]
        records = {}
        for original_row in entry['attributions']:
            at = moment(original_row['at'])
            if not win['lookback_start'] <= at < win['now']:
                continue
            project = original_row.get('project') or registry.resolve(original_row.get('cwd', meta['cwd']), machine)
            identity = _row_identity(project, meta, original_row)
            identity = aliases.get(identity, identity)
            chosen = selected[identity]
            if origins[identity] != origin or chosen['row']['line'] != original_row['line'] or identity in seen_rows:
                if at.astimezone(win['zone']).date() == win['day']:
                    cov['duplicate_rows_yesterday'] += 1
                continue
            seen_rows.add(identity)
            row = chosen['row']
            project = chosen['project'] if project else None
            cwd = row.get('cwd', meta['cwd'])
            if project and hasattr(registry, 'labels') and project not in registry.labels():
                registry._add(project, override={'id': project})
            record = records.setdefault((project, cwd), {
                'session': session, 'machine': machine, 'tool': tool,
                'subagent': meta['subagent'], 'cwd_label': cwd,
                'project': project, 'branches': [], 'days': {}})
            for branch in row.get('branches', meta['branches']):
                if branch not in record['branches']:
                    record['branches'].append(branch)
            at = moment(row['at'])
            day = at.astimezone(win['zone']).date().isoformat()
            if chosen['conflict'] and day == win['day'].isoformat():
                cov['version_conflicts_yesterday'] += 1
            owner = row['author'] == 'owner'
            if row['role'] == 'user' and day == win['day'].isoformat():
                if owner:
                    cov['owner_explicit_yesterday' if row['reason'] == 'explicit_author' else 'owner_inferred_yesterday'] += 1
                elif row['author'] == 'unattributed':
                    cov['unattributed_user_yesterday'] += 1
                    cov['unresolved_user_yesterday'] += 1
                else:
                    cov['excluded_user_yesterday'] += 1
                    reasons = cov['attribution_reasons_yesterday']
                    reasons[row['reason']] = reasons.get(row['reason'], 0) + 1
            counts = record['days'].setdefault(day, {'owner_messages': 0, 'agent_user_messages': 0,
                'unattributed_messages': 0, 'assistant_messages': 0, 'first_at': row['at'], 'last_at': row['at']})
            key = ('assistant_messages' if row['role'] == 'assistant' else 'owner_messages' if owner
                   else 'unattributed_messages' if row['author'] == 'unattributed' else 'agent_user_messages')
            counts[key] += 1
            counts['first_at'] = min(counts['first_at'], row['at'])
            counts['last_at'] = max(counts['last_at'], row['at'])
            if owner:
                counts['first_owner_at'] = min(counts.get('first_owner_at', row['at']), row['at'])
                counts['last_owner_at'] = max(counts.get('last_owner_at', ''), row['at'])
            if not project or not win['start'] - timedelta(days=config['context_days']) <= at < win['now']:
                continue
            source = 'session:' + session + ':' + str(row['line'])
            if row.get('claims') and not chosen['conflict']:
                event = {'id': fingerprint([source, row['content_key'], 'claim'])[:20], 'source': source,
                         'project': project, 'session': session, 'event_at': row['at'], 'sequence': row.get('sequence', row['line']),
                         'kind': 'handoff_claim', 'verification': 'reported', 'text': '',
                         'claims': row['claims'], 'period': period(at, win)}
                if allowed(boundary, event):
                    evidence.append(event)
            message = chosen['message']
            if not message or (row['role'] == 'user' and not owner):
                continue
            event = {'id': fingerprint([source, message['text']])[:20], 'source': source,
                     'project': project, 'event_at': row['at'], 'text': message['text'],
                     'kind': 'owner_message' if owner else 'closing_handoff',
                     'verification': 'conflict' if chosen['conflict'] else 'reported', 'machine': machine, 'tool': tool,
                     'session': session, 'sequence': row.get('sequence', row['line']), 'period': period(at, win),
                     'attribution': message['attribution']}
            if chosen['competing_handoffs']:
                event['competing_handoffs'] = chosen['competing_handoffs']
            if allowed(boundary, event):
                evidence.append(event)
        for result in entry.get('test_results', []):
            at = moment(result['at'])
            project = result.get('project') or registry.resolve(result.get('cwd', meta['cwd']), machine)
            if project and win['start'] - timedelta(days=config['context_days']) <= at < win['now']:
                event = {'id': fingerprint([session, result['line'], result])[:20],
                         'source': 'session:' + session + ':' + str(result['line']),
                         'project': project, 'session': session, 'event_at': result['at'],
                         'sequence': result['line'], 'kind': 'test_result', 'verification': 'reported_tool',
                         'text': str(result['failed']) + ' failed, ' + str(result['passed']) + ' passed',
                         'period': period(at, win), **{k: result[k] for k in ('failed', 'passed', 'errors')}}
                if allowed(boundary, event):
                    evidence.append(event)
        yesterday_records = []
        for record in records.values():
            if not allowed(boundary, record):
                continue
            sessions.append(record)
            yesterday = record['days'].get(win['day'].isoformat(), {})
            if yesterday:
                yesterday_records.append(record)
                cov['owner_messages_yesterday'] += yesterday['owner_messages']
            if not record['project'] and yesterday.get('owner_messages'):
                cov['unassigned']['count'] += 1
                cov['unassigned']['cwd_labels'] = list(dict.fromkeys(
                    cov['unassigned']['cwd_labels'] + [record['cwd_label']]))[:10]
        if yesterday_records:
            cov['sessions_yesterday'] += 1
    if origins != old_origins:
        write(origins_path, origins)
    if versions != old_versions:
        write(versions_path, versions)
    diagnostics_path = Path(cache_dir) / 'diagnostics.json'
    diagnostics = boundary.tree(coverage)
    if diagnostics != read(diagnostics_path):
        write(diagnostics_path, diagnostics)
    return sessions, evidence, coverage
