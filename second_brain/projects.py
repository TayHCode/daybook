# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Safe project identities, segment-based routing, and deterministic activity."""
import re
from pathlib import Path
from .storage import fingerprint


def normalise(name):
    return re.sub('[^a-z0-9]', '', name.lower())


def _path(value):
    return re.sub(r'^[A-Za-z]:', '', value.replace('\\', '/')).rstrip('/').lower()


def _local_path(value):
    return str(Path(value).expanduser().resolve()).rstrip('/')


def _pattern(pattern):
    parts = _path(pattern).split('/')
    if parts[-1] == '*':
        parts.pop()
    pieces = []
    for part in parts:
        if part == '**':
            pieces.append('(?:[^/]+/)*')
        elif part == '{project}':
            pieces.append('(?P<project>[^/]+)')
            pieces.append('/')
        else:
            pieces.append(re.escape(part).replace(r'\*', '[^/]*') + '/')
    return re.compile('^/?' + ''.join(pieces).rstrip('/') + '(?:/.*)?$')


class Registry:
    def __init__(self, config, repos, local_repos, boundary):
        self.config, self.boundary = config, boundary
        self.entries, self.local_map = {}, {}
        self.collisions = 0
        self.generated = 0
        self.aliases = []
        overrides = config.get('projects', [])
        by_repo = {p.get('repo', '').casefold(): p for p in overrides if p.get('repo')}
        ignored = set(config['catalog']['ignore'])
        self.excluded = {r['full_name'].casefold() for r in repos if r.get('archived') or
                         (r.get('fork') and not config['catalog']['include_forks']) or
                         r['full_name'] in ignored or r['name'] in ignored}
        for repo in repos:
            if repo.get('archived') or (repo.get('fork') and not config['catalog']['include_forks']):
                continue
            if repo['full_name'] in ignored or repo['name'] in ignored:
                continue
            override = by_repo.get(repo['full_name'].casefold(), {})
            self._add(repo['name'], repo['full_name'], override, repo.get('default_branch', 'main'))
        for override in overrides:
            if not any(p.get('override_id') == override['id'] for p in self.entries.values()):
                self._add(override['id'], override.get('repo'), override)
        local_projects = {}
        for repo in sorted(local_repos, key=lambda repo: repo['path']):
            if (repo.get('remote') or '').casefold() in self.excluded:
                continue
            matching = [p for p in self.entries.values() if repo.get('remote') and p['repo'] and p['repo'].casefold() == repo['remote'].casefold()]
            identity = _local_path(repo.get('common_dir') or repo['path'])
            project = local_projects.get(identity)
            if project is None:
                project = matching[0] if matching else self._add(repo['folder'], repo.get('remote'))
                local_projects[identity] = project
                project['local_path'] = str(Path(repo['path']).resolve())
            project['norm'].add(normalise(repo['folder']))
            self.local_map[_local_path(repo['path'])] = project['label']
            for tree in repo.get('worktrees', []):
                self.local_map[_local_path(tree['path'])] = project['label']
        norms = {}
        for p in self.entries.values():
            for name in p['norm']:
                norms.setdefault(name, []).append(p['label'])
        self.collisions = sum(len(set(labels)) > 1 for labels in norms.values())

    def _add(self, display, repo=None, override=None, default_branch='main'):
        override = override or {}
        cleaned = self.boundary.clean(display)
        label = override.get('id') or (display if cleaned == display else 'project-' + fingerprint(repo or display)[:8])
        if not re.fullmatch(r'[A-Za-z0-9_-]+', label):
            label = 'project-' + fingerprint(repo or display)[:8]
        label = self.boundary.clean(label)
        if not re.fullmatch(r'[A-Za-z0-9_-]+', label):
            label = 'project-' + fingerprint(repo or display)[:8]
        base, suffix = label, 2
        while label in self.entries:
            label = base + '-' + str(suffix)
            suffix += 1
        self.generated += int(cleaned != display and not override.get('id'))
        p = {'label': label, 'repo': repo, 'display': display, 'default_branch': default_branch,
             'pinned': bool(override.get('pin') or repo in self.config['catalog']['pin'] or display in self.config['catalog']['pin']),
             'ignored': bool(override.get('ignore') or repo in self.config['catalog']['ignore'] or display in self.config['catalog']['ignore']), 'local_path': None,
             'norm': {normalise(display)}, 'override_id': override.get('id')}
        for alias in override.get('aliases', []):
            self.aliases.append((_path(alias).strip('/'), label))
            if '/' not in alias and '\\' not in alias:
                p['norm'].add(normalise(alias))
        self.entries[label] = p
        return p

    def resolve(self, cwd, machine):
        path = _path(cwd)
        if machine == self.config['local']['machine']:
            local_path = _local_path(cwd) if cwd else ''
            for prefix, label in sorted(self.local_map.items(), key=lambda pair: -len(pair[0])):
                if local_path == prefix or local_path.startswith(prefix + '/'):
                    return label
        for alias, label in sorted(self.aliases, key=lambda pair: -len(pair[0])):
            # A single folder alias identifies the project root, never an arbitrary
            # nested runtime folder. Explicit multi-segment aliases remain exact runs.
            if '/' + alias + '/' in path + '/':
                prefix = path.split('/' + alias, 1)[0]
                if '/' in alias or prefix.rsplit('/', 1)[-1] in ('projects', 'repos', 'worktrees') or path == '/' + alias:
                    return label
        for pattern in self.config['workspace_patterns']:
            match = _pattern(pattern).match(path)
            if not match:
                continue
            name = match.group('project')
            matches = [p['label'] for p in self.entries.values() if normalise(name) in p['norm']]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return None
            cleaned = self.boundary.clean(name).lower()
            return self._add(name, override={'id': cleaned})['label']
        return None

    def routing(self, label):
        return self.entries[label]

    def labels(self):
        return list(self.entries)


def active_set(registry, facts, win, config):
    active, worked, parked = [], [], []
    for label in registry.labels():
        project = registry.routing(label)
        if project['ignored']:
            continue
        f = facts.get(label, {})
        standing = f.get('standing', {})
        days = standing.get('days', {})
        def activity(counts):
            return any(counts.get(k, 0) for k in ('commits', 'prs', 'owner_messages', 'activity'))
        has_activity = any(win['lookback_start'].date().isoformat() <= day <= win['now'].date().isoformat() and activity(counts) for day, counts in days.items())
        if project['pinned'] or has_activity:
            active.append(label)
            if activity(days.get(win['day'].isoformat(), {})):
                worked.append(label)
        elif standing.get('unpushed_branches') or any(w.get('modified', 0) or w.get('untracked', 0) for w in standing.get('worktrees', [])):
            parked.append(label)
    eligible = sum(not p['ignored'] for p in registry.entries.values())
    active.sort(key=lambda label: facts.get(label, {}).get('standing', {}).get('last_activity_at') or '', reverse=True)
    return active, worked, parked, eligible - len(active) - len(parked)
