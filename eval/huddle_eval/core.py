"""Evaluation logic. Times are local monotonic seconds.

The server's configuration is not copied here: the message length limit comes
from the backend's config package (`huddle.config`), like the runner does.
"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import statistics
import yaml

from huddle.config import load_config

STEPS = {'say', 'stop', 'wait', 'reload', 'burst'}
# The conversation is settled when the server reports no active work, no agent
# is typing or streaming, and the last user message or stop is old enough.
IDLE_POLL_INTERVAL_S = 0.3
IDLE_MIN_AFTER_SEND_S = 1.5
IDLE_STREAK = 2
CHECKS = {'spoke', 'silent', 'same_message_continued', 'status', 'no_new_text_after_stop', 'reply_to', 'max_messages', 'file_exists', 'reload_identical', 'effort', 'latency', 'no_leak', 'ui_words_absent'}
# The message length the server accepts: `limits.message_chars` of the config
# when it is set, else the server's own default.
DEFAULT_MESSAGE_CHARS = 8000
# The words a user-facing agent message must not contain. A check may name its
# own list (`ui_words_absent: {words: [...]}`); this list is the documented
# default of the plain form (`ui_words_absent: true`).
DEFAULT_UI_WORDS = ('steer', 'redirect', 'interrupted', 'instruction', 'system prompt')


def configured_message_chars(cfg=None):
    """The message length limit of the server: the config's, else 8000."""
    if cfg is None:
        try:
            cfg = load_config()
        except (OSError, ValueError):
            return DEFAULT_MESSAGE_CHARS
    value = (cfg.get('limits') or {}).get('message_chars')
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return DEFAULT_MESSAGE_CHARS
    return value


def validate_check(name, value):
    if name == 'reload_identical':
        return value is True
    if name == 'ui_words_absent':
        # `true` means the default word list; a check may name its own words.
        if value is True:
            return True
        if not isinstance(value, dict) or set(value) != {'words'}:
            return False
        words = value['words']
        return isinstance(words, list) and all(isinstance(w, str) and w for w in words)
    if not isinstance(value, dict):
        return False
    specs = {
        'spoke': ({'turn'}, {'include', 'exclude', 'max_agents'}),
        'silent': ({'turn', 'agents'}, set()),
        'same_message_continued': ({'turn', 'agent'}, set()),
        'status': ({'turn', 'agent', 'is'}, set()),
        'no_new_text_after_stop': ({'within_ms'}, set()),
        'reply_to': ({'turn', 'agent', 'includes'}, set()),
        'max_messages': ({'turn', 'n'}, set()),
        'file_exists': ({'path'}, set()),
        'effort': ({'turn', 'agent', 'is'}, set()),
        'latency': ({'kind', 'max_ms'}, set()),
        'no_leak': ({'agents', 'phrases'}, set()),
    }
    required, optional = specs[name]
    if not required <= value.keys() or value.keys() - required - optional:
        return False
    for key in ('include', 'exclude', 'includes', 'agents', 'phrases'):
        if key in value and (not isinstance(value[key], list) or not all(isinstance(x, str) and x for x in value[key])):
            return False
    for key in ('agent', 'path'):
        if key in value and (not isinstance(value[key], str) or not value[key]):
            return False
    for key in ('turn', 'n', 'max_agents'):
        if key in value and (type(value[key]) is not int or value[key] < (1 if key == 'turn' else 0)):
            return False
    for key in ('within_ms', 'max_ms'):
        if key in value and (type(value[key]) not in (int, float) or value[key] < 0):
            return False
    if name == 'status' and value['is'] not in ('done', 'stopped'):
        return False
    if name == 'effort' and value['is'] not in ('light', 'normal', 'deep'):
        return False
    if name == 'latency' and value['kind'] not in ('first_delta', 'steer_first_delta'):
        return False
    return True


def idle_poll(snapshot, last_send, now, streak=0):
    """One idle poll over a snapshot. Returns (settled, streak).

    Settled means the server reports no active work, no agent is typing or
    streaming, and the last user message or stop is at least 1.5 s old - for
    two consecutive polls.
    """
    active = bool(snapshot.get('active'))
    busy = (any(a.get('typing') for a in snapshot.get('agents') or [])
            or any(m.get('status') == 'streaming' for m in snapshot.get('messages') or []))
    if active or busy or now - last_send < IDLE_MIN_AFTER_SEND_S:
        return False, 0
    streak += 1
    return streak >= IDLE_STREAK, streak


def load_cases(directory, max_chars=None):
    """The cases of a directory. `max_chars` overrides the message length limit."""
    limit = max_chars if max_chars is not None else configured_message_chars()
    cases, ids = [], set()
    for path in sorted(Path(directory).glob('*.yaml')):
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, list):
            raise ValueError(f'{path}: expected a list')
        for case in data:
            if not isinstance(case, dict) or not isinstance(case.get('id'), str) or not case['id'] or case['id'] in ids or not isinstance(case.get('category'), str):
                raise ValueError(f'{path}: invalid or duplicate case id/category')
            ids.add(case['id'])
            if not isinstance(case.get('steps'), list) or not case['steps'] or not isinstance(case.get('checks', []), list) or not isinstance(case.get('judge', []), list) or not all(isinstance(s, str) for s in case.get('judge', [])):
                raise ValueError(f'{path}: invalid steps/checks/judge in {case["id"]}')
            for collection, allowed in ((case['steps'], STEPS), (case.get('checks', []), CHECKS)):
                for item in collection:
                    if not isinstance(item, dict) or len(item) != 1 or next(iter(item)) not in allowed:
                        raise ValueError(f'{path}: invalid item {item!r}')
                    name, value = next(iter(item.items()))
                    if name == 'say' and (not isinstance(value, str) or not 1 <= len(value.strip()) <= limit):
                        raise ValueError(f'{path}: invalid say')
                    if name in ('stop', 'reload', 'reload_identical') and value is not True:
                        raise ValueError(f'{path}: {name} must be true')
                    if name == 'wait' and not (value == 'idle' or isinstance(value, dict) and (set(value) == {'ms'} and isinstance(value['ms'], (int, float)) and value['ms'] >= 0 or set(value) in ({'agent', 'chars'}, {'agent', 'message_started'}) and isinstance(value.get('agent'), str) and (isinstance(value.get('chars'), int) and value['chars'] >= 0 or value.get('message_started') is True))):
                        raise ValueError(f'{path}: invalid wait {value!r}')
                    if name == 'burst' and (not isinstance(value, dict) or not isinstance(value.get('texts'), list) or not value['texts'] or not all(isinstance(t, str) and 1 <= len(t.strip()) <= limit for t in value['texts']) or not isinstance(value.get('interval_ms'), (int, float)) or value['interval_ms'] < 0):
                        raise ValueError(f'{path}: invalid burst')
                    if name in CHECKS and not validate_check(name, value):
                        raise ValueError(f'{path}: invalid check {name}')
            cases.append(case)
    return cases


def replay(events):
    messages = {}
    for event in sorted(events, key=lambda e: e['seq']):
        typ, data = event['type'], event['data']
        if typ == 'message.created':
            m = deepcopy(data['message']); messages[m['id']] = m
        elif typ == 'message.delta':
            messages[data['id']]['text'] += data['text']
        elif typ == 'message.done':
            messages[data['id']]['status'] = data['status']
    return sorted(messages.values(), key=lambda m: m['pos'])


def comparable(messages):
    return [(m['text'], m['status']) for m in messages]


def delta_events(events, message_id):
    return [e for e in events if e['type'] == 'message.delta' and e['data']['id'] == message_id and e['data']['text']]


# A continuation starts after a model round trip; while a model streams, deltas come far closer together.
CONTINUATION_GAP_S = 0.25
# Text written before a steer is flushed within milliseconds of it.
FLUSH_WINDOW_S = 0.1


def _server_time(event):
    ts = event.get('ts')
    return datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp() if ts else None


def continuation_arrival(events, message_id, after_seq, steer_time=None):
    """When the first word written after a steer reached the client.

    Text the model produced before the steer is still flushed right after it, so the continuation is the
    first delta (stored after the steer) that comes a model round trip after both the steer and the delta
    before it, on the server's clock. This is an estimate: a stream that pauses that long on its own is read
    as the continuation. Without server times, the first delta stored after the steer.
    """
    previous, fallback = None, None
    for e in delta_events(events, message_id):
        server = _server_time(e)
        if e['seq'] > after_seq:
            base = max((x for x in (previous, steer_time) if x is not None), default=None)
            if server is None or base is None or server - base >= CONTINUATION_GAP_S:
                return e['received_at']
            if fallback is None and steer_time is not None and server - steer_time >= FLUSH_WINDOW_S:
                fallback = e['received_at']  # a continuation too fast to show a pause
        previous = server
    return fallback


def metrics(events, sends):
    messages = replay(events)
    first, steer = [], []
    for send in sends:
        if send['kind'] != 'say':
            continue
        t, turn = send.get('returned_at', send['time']), send['turn']
        created_events = [e for e in events if e['type'] == 'message.created']
        arrivals = []
        for m in messages:
            if m['author'] == 'user' or m['turn'] != turn:
                continue
            created = next((e for e in created_events if e['data']['message']['id'] == m['id']), None)
            if created and created['received_at'] >= t and created['data']['message'].get('text'):
                arrivals.append(created['received_at'])
            arrivals.extend(e['received_at'] for e in delta_events(events, m['id']) if e['received_at'] >= t)
        first.append({'turn': turn, 'ms': (min(arrivals)-t)*1000 if arrivals else None})
        sent = send['time']  # steer latency and its targets count from the POST, not from its response
        active = []
        for m in messages:
            if m['author'] == 'user' or m['turn'] >= turn:
                continue
            created = next((e for e in created_events if e['data']['message']['id'] == m['id']), None)
            done = next((e for e in events if e['type'] == 'message.done' and e['data']['id'] == m['id']), None)
            if created and created['received_at'] <= sent and (not done or done['received_at'] > sent):
                active.append(m)
        if active:
            text = send.get('text', '').casefold()
            # A named target restricts steering latency to that agent; otherwise all active streamers.
            targets = [m for m in active if m['author'].casefold() in text]
            if not targets:
                targets = active
            steer_event = next((e for e in created_events if e['data']['message']['author'] == 'user'
                                and e['data']['message']['turn'] == turn), None)
            if steer_event is None:
                ds = [e['received_at'] for m in targets for e in delta_events(events, m['id']) if e['received_at'] >= t + .001]
            else:
                ds = [a for m in targets if (a := continuation_arrival(events, m['id'], steer_event['seq'],
                                                                       _server_time(steer_event))) is not None]
            ds = [d for d in ds if d >= sent]
            steer.append({'turn': turn, 'message_ids': [m['id'] for m in targets], 'ms': (min(ds)-sent)*1000 if ds else None})
    return {'first_delta_ms': first, 'steer_first_delta_ms': steer,
            'median_steer_first_delta_ms': statistics.median([x['ms'] for x in steer if x['ms'] is not None]) if any(x['ms'] is not None for x in steer) else None}


def check(name, args, events, sends, snapshot=None, files=None, reloads=None):
    messages = replay(events)
    agents = [m for m in messages if m['author'] != 'user']
    selected = [m for m in agents if m['turn'] == args.get('turn') and (not args.get('agent') or m['author'] == args['agent'])] if isinstance(args, dict) else []
    ok, detail = False, ''
    if name == 'spoke':
        authors = {m['author'] for m in selected}
        ok = set(args.get('include', [])) <= authors and not (set(args.get('exclude', [])) & authors) and len(authors) <= args.get('max_agents', float('inf'))
        detail = f'authors: {sorted(authors)}'
    elif name == 'silent':
        authors = {m['author'] for m in selected}; ok = not (authors & set(args['agents'])); detail = f'authors: {sorted(authors)}'
    elif name == 'same_message_continued':
        # Ordered by the server's log, not by client clocks: the message streamed text before the
        # later user message was stored and kept streaming after it. Whether the new text follows
        # that message is a judge statement.
        later = {e['data']['message']['turn']: e['seq'] for e in events if e['type'] == 'message.created'
                 and e['data']['message']['author'] == 'user' and e['data']['message']['turn'] > args['turn']}
        matches = []
        for m in selected:
            seqs = [e['seq'] for e in delta_events(events, m['id'])]
            matches += [(m['id'], turn) for turn, seq in sorted(later.items()) if any(x < seq for x in seqs) and any(x > seq for x in seqs)]
        ok = bool(matches); detail = f'message/later user message matches: {matches}'
    elif name in ('status', 'effort'):
        ok = bool(selected) and all(m.get(name) == args['is'] for m in selected); detail = f'{name} values: {[m.get(name) for m in selected]}'
    elif name == 'no_new_text_after_stop':
        stops = [s for s in sends if s['kind'] == 'stop']
        violations = []
        for s in stops:
            old_ids = {e['data']['message']['id'] for e in events if e['type'] == 'message.created' and e['received_at'] <= s['time'] and e['data']['message']['author'] != 'user'}
            violations += [e['seq'] for e in events if e['type'] == 'message.delta' and e['data']['id'] in old_ids and e['data']['text'] and e['received_at'] > s['time'] + args['within_ms']/1000]
        ok = bool(stops) and not violations; detail = f'late delta seqs: {violations}; stops: {len(stops)}'
    elif name == 'reply_to':
        ok = any(set(args['includes']) <= set(m.get('reply_to') or []) for m in selected); detail = f'reply_to: {[m.get("reply_to") for m in selected]}'
    elif name == 'max_messages':
        ok = len(selected) <= args['n']; detail = f'{len(selected)} messages, maximum {args["n"]}'
    elif name == 'file_exists':
        ok = files is not None and any(f['path'] == args['path'] for f in files); detail = f'path {args["path"]!r}; files: {files!r}'
    elif name == 'reload_identical':
        comparisons = list(reloads or [])
        if snapshot is not None:
            comparisons.append({'snapshot': snapshot, 'events': [e for e in events if e['seq'] <= snapshot['last_seq']]})
        failures = [i for i, r in enumerate(comparisons) if comparable(r['snapshot']['messages']) != comparable(replay(r['events']))]
        ok = bool(comparisons) and not failures; detail = f'{len(comparisons)} comparisons; mismatches: {failures}'
    elif name == 'latency':
        values = metrics(events, sends)[args['kind'] + '_ms']; ok = bool(values) and all(v['ms'] is not None and v['ms'] <= args['max_ms'] for v in values); detail = f'{values}; limit {args["max_ms"]} ms'
    elif name == 'no_leak':
        violations = [(m['author'], p) for m in agents if m['author'] in args['agents'] for p in args['phrases'] if p.casefold() in m['text'].casefold()]; ok = not violations; detail = f'matches: {violations}'
    elif name == 'ui_words_absent':
        # The check names its own words; the plain form uses the default list.
        words = args['words'] if isinstance(args, dict) and 'words' in args else DEFAULT_UI_WORDS
        violations = [(m['author'], p) for m in agents for p in words if p.casefold() in m['text'].casefold()]; ok = not violations; detail = f'matches: {violations}'
    else:
        raise ValueError(f'unknown check {name}')
    return {'check': name, 'args': args, 'pass': ok, 'detail': detail}


def transcript(events, sends, agent_info):
    """Insert send-time markers into each independently streamed agent message."""
    messages = replay(events); lines = []
    info = {a['key']: a for a in agent_info}
    for m in messages:
        if m['author'] == 'user':
            lines.append(f'User (turn {m["turn"]}): {m["text"]}')
            continue
        author = info.get(m['author'], {}); text = m['text']
        created = next(e for e in events if e['type'] == 'message.created' and e['data']['message']['id'] == m['id'])
        initial = created['data']['message'].get('text', '')
        deltas = delta_events(events, m['id'])
        done = next((e for e in events if e['type'] == 'message.done' and e['data']['id'] == m['id']), None)
        markers = []
        for s in sends:
            if s['kind'] == 'say' and s['time'] >= created['received_at'] and (done is None or s['time'] < done['received_at']):
                offset = len(initial) + sum(len(e['data']['text']) for e in deltas if e['received_at'] < s['time'])
                markers.append((offset, s['turn']))
        for offset, turn in reversed(markers):
            text = text[:offset] + f'⟪user message {turn} arrives⟫' + text[offset:]
        lines.append(f'{author.get("name", m["author"])} ({author.get("role", m["author"])}, turn {m["turn"]}, {m["status"]}): {text}')
    return '\n'.join(lines)
