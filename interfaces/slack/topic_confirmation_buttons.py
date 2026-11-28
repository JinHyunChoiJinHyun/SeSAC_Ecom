"""Slack topic confirmation button helpers (independent of approval buttons)."""
import json
import sqlite3
import threading
import uuid
from agent_system.orchestrator.tools.analysis_topic_guard import DB

LOCK = threading.RLock()
PREFIX = 'carttopic_'


def _connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.execute('CREATE TABLE IF NOT EXISTS button_requests (base_thread_id TEXT PRIMARY KEY, token TEXT NOT NULL)')
    return conn


def build_blocks(base_thread_id):
    """Create the buttons only if the original pending request still exists."""
    with LOCK, _connect() as db:
        pending = db.execute('SELECT question FROM pending WHERE base_thread_id=?', (base_thread_id,)).fetchone()
        if not pending:
            return []
        token = uuid.uuid4().hex
        db.execute('INSERT INTO button_requests (base_thread_id, token) VALUES (?, ?) '
                   'ON CONFLICT(base_thread_id) DO UPDATE SET token=excluded.token', (base_thread_id, token))
    def button(decision, label, style=None):
        value = json.dumps({'b': base_thread_id, 'tok': token, 'd': decision}, separators=(',', ':'))
        item = {'type': 'button', 'action_id': PREFIX + decision,
                'text': {'type': 'plain_text', 'text': label}, 'value': value}
        if style:
            item['style'] = style
        return item
    return [
        {'type': 'section', 'text': {'type': 'mrkdwn',
            'text': '*새 분석 주제로 전환할까요?*\n새 세션을 선택하면 이전 보고서와 분석 기록은 보존됩니다.'}},
        {'type': 'actions', 'block_id': 'carttopic_confirmation', 'elements': [
            button('switch', '새 세션에서 분석', 'primary'),
            button('stay', '기존 세션 유지'),
        ]},
    ]


def consume(base_thread_id, token):
    """Atomic one-click claim; prevents stale/duplicate button events."""
    with LOCK, _connect() as db:
        exists = db.execute('SELECT 1 FROM pending WHERE base_thread_id=?', (base_thread_id,)).fetchone()
        if not exists:
            return False
        cur = db.execute('DELETE FROM button_requests WHERE base_thread_id=? AND token=?',
                         (base_thread_id, token))
        return cur.rowcount == 1


def handle_click(bridge, body, client):
    from agent_system.orchestrator.tools.memory import build_slack_thread_id
    actions = body.get('actions') or []
    if not actions:
        return
    item = actions[0]
    if item.get('action_id') not in {PREFIX + 'switch', PREFIX + 'stay'}:
        return
    try:
        payload = json.loads(item.get('value') or '')
    except (ValueError, TypeError):
        return
    decision = payload.get('d')
    if decision not in {'switch', 'stay'} or item['action_id'] != PREFIX + decision:
        return
    user = (body.get('user') or {}).get('id')
    channel = (body.get('channel') or {}).get('id') or (body.get('container') or {}).get('channel_id')
    if not channel or not user:
        return
    message = body.get('message') or {}
    ts = message.get('ts') or (body.get('container') or {}).get('message_ts')
    event = {'user': user, 'channel': channel, 'channel_type': 'im' if channel.startswith('D') else 'channel',
             'ts': ts or str(uuid.uuid4())}
    if message.get('thread_ts'):
        event['thread_ts'] = message['thread_ts']
    expected_base = str(payload.get('b') or '')
    if build_slack_thread_id(event) != expected_base:
        client.chat_postEphemeral(channel=channel, user=user,
                                  text='이 분석 세션 확인 버튼은 요청한 사용자만 사용할 수 있습니다.')
        return
    if not consume(expected_base, str(payload.get('tok') or '')):
        client.chat_postEphemeral(channel=channel, user=user,
                                  text='이미 처리됐거나 만료된 확인 버튼입니다.')
        return
    label = '새 세션에서 분석' if decision == 'switch' else '기존 세션 유지'
    try:
        client.chat_update(channel=channel, ts=ts, text=f'선택 완료: {label}',
                           blocks=[{'type': 'section', 'text': {'type': 'mrkdwn',
                               'text': f'선택 완료: *{label}* (<@{user}>)'}}])
    except Exception:
        # 중복 클릭은 DB에서 차단하므로 메시지 변경 실패 시에도 진행 가능.
        pass
    bridge._enqueue(event=event,
                    body={'team_id': (body.get('team') or {}).get('id') or '',
                          'event_id': 'carttopic:' + str(payload.get('tok'))},
                    client=client, question='네' if decision == 'switch' else '아니요')
