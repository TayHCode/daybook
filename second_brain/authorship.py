# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Conservative separation of owner messages from harness and agent messages."""
import re
import hashlib

TAGS = ('environment_context', 'user_instructions', 'system-reminder', 'task-notification',
        't3_context', 't3-context', 't3_code_context', 't3_code_orchestration',
        't3_code_runtime', 't3_code_tools', 'INSTRUCTIONS')
DELEGATED = re.compile(r'^\s*(act as (the |an? )?[\w-]+ sub-?agent|you are (the |an? )?([\w-]+ )?sub-?agent|(delegated|sub-?agent) task\s*:)', re.I)
RULE = ('Authorship: message roles and explicit author fields are checked with tool-result, '
        'sidechain/subagent, injected-context, command-expansion and replay markers. '
        'Non-owner structure takes precedence over a user role or owner label. '
        'Ordinary user turns without an explicit human/owner author are unattributed, '
        'excluded from owner totals, activity and prompts; SDK/T3 origin proves neither owner nor agent. '
        'Unmarked relays are indistinguishable from genuine requests in these inputs. '
        'Agent text equality never changes authorship. No transcript index or T3 database is read. '
        'Configured and legacy harness/delegated-prefix exclusions remain heuristic.')


def text_key(text):
    return hashlib.sha256(' '.join(text.split()).casefold().encode()).hexdigest()


def classify(text, role='user', *, extra_injected=(), explicit_owner=False):
    if role != 'user':
        return text, 'agent'
    text = text.strip()
    prefixes = ('# AGENTS.md instructions', '<command-', '<local-command', '<turn_aborted',
                'Caveat:', '[Request interrupted')
    if not explicit_owner and DELEGATED.match(text):
        return text, 'agent'
    if (text.startswith(prefixes) and not explicit_owner) or any(re.search(p, text) for p in extra_injected):
        return '', 'injected'
    if not explicit_owner and text.startswith('This session is being continued from a previous conversation'):
        return '', 'injected'
    for tag in TAGS:
        text = re.sub(r'<' + re.escape(tag) + r'\b[^>]*>.*?</' + re.escape(tag) + r'\s*>', '', text, flags=re.S)
    text = text.strip()
    if not text or (not explicit_owner and (re.match(r'<[\w!/?-]', text) or any(re.search(r'<' + re.escape(t) + r'\b', text) for t in TAGS))):
        return '', 'injected'
    return text, 'owner' if explicit_owner else 'unattributed'


def subagent_source(source):
    # Codex SessionSource is a string or an externally tagged subagent object.
    return (isinstance(source, str) and source.casefold() in ('subagent', 'sub_agent', 'sub-agent')) or (
        isinstance(source, dict) and any(k in source for k in ('subagent', 'sub_agent')))


def provenance(row, tool, in_subagent_file):
    """Return (author override, fixed reason); never infer authorship from equality."""
    message = row.get('message' if tool == 'claude' else 'payload', {})
    fields = [row, message] if isinstance(message, dict) else [row]
    if in_subagent_file or any(f.get('isSidechain') or f.get('sourceToolAssistantUUID') or
                              subagent_source(f.get('source')) for f in fields):
        return 'agent', 'subagent'
    if any(f.get('isReplay') or f.get('is_replay') or f.get('isResumeReplay') or
           f.get('isCompactSummary') for f in fields):
        return 'injected', 'replay'
    if any(f.get('isMeta') or f.get('toolUseResult') is not None for f in fields):
        return 'injected', 'injected'
    for f in fields:
        content = f.get('content', [])
        if isinstance(content, list) and any(isinstance(b, dict) and b.get('type') in
                ('tool_result', 'function_call_output', 'custom_tool_call_output') for b in content):
            return 'injected', 'tool_result'
    roles = [f.get('role') for f in fields if f.get('role')]
    if tool == 'claude':
        roles.append(row.get('type'))
    if any(r in ('system', 'developer', 'tool') for r in roles):
        return 'injected', 'role'
    if any(r != 'user' for r in roles):
        return 'agent', 'role'
    authors = []
    for f in fields:
        for key in ('author', 'authored_by'):
            author = f.get(key)
            if isinstance(author, dict):
                author = author.get('role') or author.get('type')
            if isinstance(author, str):
                authors.append(author.casefold())
    if any(a in ('agent', 'assistant', 'subagent', 'sub-agent', 'model') for a in authors):
        return 'agent', 'explicit_author'
    if any(a in ('system', 'developer', 'tool', 'harness') for a in authors):
        return 'injected', 'explicit_author'
    if any(a in ('unknown', 'unresolved', 'ambiguous', 'unattributed') for a in authors):
        return 'unattributed', 'explicit_author'
    if any(a in ('owner', 'human') for a in authors):
        return 'owner', 'explicit_author'
    # An author.role=user has the same uncertainty as the ordinary user role.
    return None, 'user_turn'


def row_author(row, tool, in_subagent_file):
    return provenance(row, tool, in_subagent_file)[0]
