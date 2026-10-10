# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Bounded read-only git snapshots. No fetch or working-tree mutation."""
import os
from pathlib import Path
import re
import subprocess
from .dayconfig import moment, period
from .executables import resolve, search_path
from .storage import read, write
try:
    from .privacy import allowed
except ImportError:
    def allowed(boundary, value, *, identity=''):
        return True

ALLOWED = {'config', 'worktree', 'status', 'for-each-ref', 'log', 'rev-parse', 'merge-base'}


def git(args, cwd, config, run=None):
    run = run or subprocess.run
    assert args and args[0] in ALLOWED
    assert args[0] != 'config' or args[1] == '--get'
    assert args[0] != 'worktree' or args[1] == 'list'
    assert args[0] != 'merge-base' or (len(args) == 4 and args[1] == '--is-ancestor')
    assert args[0] != 'rev-parse' or args[1:] == ['--git-common-dir']
    result = run([resolve('git', config), '--no-optional-locks', '-C', str(cwd), *args],
                 capture_output=True, text=True, timeout=config['git_timeout'],
                 env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0', 'PATH': search_path(config)})
    if result.returncode:
        raise RuntimeError('Local git read failed')
    return result.stdout


def remote_name(url):
    match = re.search(r'(?:github\.com[/:])([^/\s]+/[^/\s]+?)(?:\.git)?/?$', url.strip())
    return match.group(1) if match else None


def discover(config, run=None):
    result = {}
    for name in config['local']['repo_roots']:
        root = Path(name).expanduser().resolve()
        if not root.is_dir():
            continue
        candidates = [root] if (root / '.git').exists() else sorted(root.iterdir())
        for path in candidates:
            path = path.resolve()
            if not (path / '.git').is_dir() and not (path / '.git').is_file():
                continue
            try:
                common = git(['rev-parse', '--git-common-dir'], path, config, run).strip()
            except RuntimeError:
                common = ''
            common_dir = (path / (common or '.git')).resolve()
            if str(common_dir) in result:
                continue
            # Git lists the main checkout first, including from linked worktrees
            # and repositories whose git directory is stored separately.
            try:
                trees = git(['worktree', 'list', '--porcelain'], path, config, run)
                first = next((line[9:] for line in trees.splitlines() if line.startswith('worktree ')), None)
                main = Path(first).resolve() if first else path
            except RuntimeError:
                main = path
            try:
                remote = git(['config', '--get', 'remote.origin.url'], main, config, run).strip()
            except RuntimeError:
                remote = ''
            result[str(common_dir)] = {'path': str(main), 'folder': main.name,
                                       'common_dir': str(common_dir),
                                       'remote': remote_name(remote), 'has_remote': bool(remote)}
    return sorted(result.values(), key=lambda repo: repo['path'])


def _has_remote(path, config, run=None):
    try:
        return bool(git(['config', '--get', 'remote.origin.url'], path, config, run).strip())
    except RuntimeError:
        return False


def worktrees(repo, config, run=None):
    output = git(['worktree', 'list', '--porcelain'], repo['path'], config, run)
    trees = []
    for block in output.strip().split('\n\n'):
        fields = dict(line.split(' ', 1) if ' ' in line else (line, True) for line in block.splitlines())
        if 'worktree' not in fields:
            continue
        trees.append({'path': str(Path(fields['worktree']).resolve()), 'head': fields.get('HEAD', '')[:12],
                      'branch': str(fields['branch']).removeprefix('refs/heads/') if 'branch' in fields else None,
                      'detached': 'detached' in fields, 'prunable': 'prunable' in fields})
    trees.sort(key=lambda t: t['path'] != repo['path'])
    repo['worktrees_total'] = len(trees)
    return trees[:config['local']['max_worktrees_per_repo']]


def branches(repo, config, run=None):
    output = git(['for-each-ref', 'refs/heads', '--format=%(refname:short)%1f%(objectname:short=12)%1f%(committerdate:iso-strict)%1f%(upstream:short)%1f%(upstream:track)'], repo['path'], config, run)
    result = []
    for line in output.splitlines():
        fields = line.split('\x1f')
        if len(fields) != 5:
            continue
        name, head, at, upstream, track = fields
        counts = {k: int(m.group(1)) if (m := re.search(k + r' (\d+)', track)) else 0 for k in ('ahead', 'behind')}
        result.append({'name': name, 'head': head[:12], 'committed_at': at,
                       'upstream': upstream, 'gone': '[gone]' in track, **counts})
    return result


def status(worktree_path, config, run=None):
    output = git(['status', '--porcelain=v2', '--branch', '--untracked-files=normal'], worktree_path, config, run)
    result = {'modified': 0, 'untracked': 0, 'paths': []}
    for line in output.splitlines():
        if line.startswith('? '):
            category, path = 'untracked', line[2:]
        elif line.startswith(('1 ', '2 ', 'u ')):
            category = 'modified'
            path = line.split(' ', 10 if line[0] == 'u' else 9 if line[0] == '2' else 8)[-1].split('\t')[0]
        else:
            continue
        result[category] += 1
        if len(result['paths']) < 10:
            result['paths'].append(path)
    return result


def commits(repo, win, config, run=None, detached_heads=()):
    output = git(['log', '--branches', *detached_heads, '--since=' + win['lookback_start'].isoformat(),
                  '--format=%H%x1f%cI%x1f%an%x1f%s%x1e', '--shortstat', '-n', '500'], repo['path'], config, run)
    pattern = re.compile(r'([a-fA-F0-9]{40})\x1f([^\x1f]+)\x1f([^\x1f]*)\x1f([^\x1e]*)\x1e([^\x1e]*?)(?=[a-fA-F0-9]{40}\x1f|\Z)', re.S)
    result = []
    for sha, at, author, subject, stat in pattern.findall(output):
        counts = {}
        for key, regex in [('files', r'(\d+) files? changed'), ('additions', r'(\d+) insertions?'), ('deletions', r'(\d+) deletions?')]:
            match = re.search(regex, stat)
            counts[key] = int(match.group(1)) if match else 0
        result.append({'sha': sha[:12], 'at': at, 'author': author, 'subject': subject, **counts})
    return result


def _branch_tracking(standing):
    for state in standing.values():
        refs = state.get('branches', [])
        state['unpushed_branches'] = [b for b in refs if b.get('upstream') and b.get('ahead', 0) > 0]
        state['branches_without_upstream'] = [b for b in refs if not b.get('upstream')]
    return standing


def collect(config, registry, win, boundary, cache_file, refresh=False, run=None):
    cached = read(cache_file)
    if cached and not refresh:
        return cached['evidence'], _branch_tracking(cached['standing']), cached['coverage']
    evidence, standing = [], {}
    coverage = {'repos': 0, 'worktrees': 0, 'failures': [], 'limits': [],
                'note': 'Ahead/behind as of the last fetch; daybook never fetches.'}
    routing = {}
    for label in registry.labels():
        p = registry.routing(label)
        if not p['local_path'] or p['ignored']:
            continue
        repo = {'path': p['local_path']}
        coverage['repos'] += 1
        standing[label] = {'worktrees': [], 'unpushed_branches': [], 'branches': [],
                           'has_remote': _has_remote(repo['path'], config, run), 'repo_available': False}
        try:
            trees = worktrees(repo, config, run)
            refs = branches(repo, config, run)
            routing[label] = {'worktrees': trees, 'branches': refs}
            coverage['worktrees'] += len(trees)
            if repo['worktrees_total'] > len(trees):
                coverage['limits'].append('local.worktrees:' + label)
            by_branch = {b['name']: b for b in refs}
            safe_refs = boundary.tree(refs)
            standing[label]['branches'] = safe_refs
            cwd_labels = set(config.get('_session_cwds', []))
            for tree in trees:
                ref = by_branch.get(tree['branch'], {})
                at = moment(ref.get('committed_at'))
                safe_path = boundary.clean(tree['path'])
                check = tree['path'] == repo['path'] or (at and at >= win['lookback_start']) or any(c == safe_path or c.startswith(safe_path + '/') for c in cwd_labels)
                state = status(tree['path'], config, run) if check and not tree['prunable'] else {'modified': 0, 'untracked': 0, 'paths': []}
                item = boundary.tree({'label': safe_path, 'branch': tree['branch'] or ('detached HEAD' if tree['detached'] else ''),
                                      'head': tree['head'], 'detached': tree['detached'],
                                      'machine': config['local']['machine'], 'source': 'local_git',
                                      'ahead': ref.get('ahead', 0), 'behind': ref.get('behind', 0),
                                      'status_checked': bool(check), **state})
                standing[label]['worktrees'].append(item)
            detached_heads = [t['head'] for t in trees if t['detached'] and t['head']]
            rows = commits(repo, win, config, run, detached_heads)
            standing[label]['repo_available'] = True
            if len(rows) == 500:
                coverage['limits'].append('local.commits:' + label)
            for row in rows:
                at = moment(row['at'])
                if at is None or not win['lookback_start'] <= at < win['now']:
                    continue
                try:
                    containing = git(['for-each-ref', '--contains=' + row['sha'], 'refs/heads', '--format=%(refname:short)'], repo['path'], config, run).splitlines()
                    detached = []
                    for tree in trees:
                        if tree['detached'] and tree['head']:
                            try:
                                git(['merge-base', '--is-ancestor', row['sha'], tree['head']], repo['path'], config, run)
                            except RuntimeError:
                                continue
                            detached.append(tree['path'])
                    if detached:
                        containing.append('detached HEAD')
                    event = boundary.tree({'id': 'lc.' + row['sha'], 'project': label,
                                           'source': 'local:' + label + ':' + row['sha'], 'event_at': row['at'],
                                           'kind': 'local_commit', 'verification': 'local_git', 'period': period(at, win),
                                           'text': row['subject'], 'branches': containing,
                                           'detached_worktrees': detached, **row})
                    if allowed(boundary, event):
                        evidence.append(event)
                except Exception:
                    coverage['failures'].append({'stage': 'collect.localgit', 'repo': label, 'error_type': 'PrivacyDropped'})
        except Exception as exc:
            coverage['failures'].append({'stage': 'collect.localgit', 'repo': label, 'error_type': type(exc).__name__})
    _branch_tracking(standing)
    write(cache_file, {'evidence': evidence, 'standing': standing, 'coverage': coverage, 'routing': routing})
    return evidence, standing, coverage
