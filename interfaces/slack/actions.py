"""Slack 버튼 — 사용자가 '승인'·'진행'을 글자로 쓰지 않고 버튼으로 의사를 확인합니다.

버튼이 붙는 곳 (Orchestrator 결과의 "actions", agent_system/orchestrator/graph.py pending_actions)
    approval   확정값 승인 대기(check_approval)        [승인] [반려] [승인 요청 취소]
               수정은 버튼 대신 채팅으로 (예: "카디건으로 수정")
    workflow   워크플로가 멈춤(blocked)                 [다시 시도] [진행 상황] [워크플로 취소]
               워크플로가 마케터 선택 대기(waiting_input) [진행 상황] [워크플로 취소]

안전 장치
    - 버튼 값에 대화 ID(thread_id)를 넣고, 누른 사람의 대화 ID와 같을 때만 처리 (요청한 사람만 누를 수 있음)
    - 승인 버튼에는 그 승인 요청의 ID(token), 워크플로 버튼에는 run_id를 넣어 지금 기다리는 요청과 같을 때만 처리
      → 지난 메시지의 버튼이나 두 번 누른 버튼은 처리하지 않음 (graph.run_turn)
    - 누르면 바로 버튼을 없애고 "선택: 승인 (@사용자)"로 바꿈
    - 워크플로 취소에는 확인 창(confirm)을 띄움
Slack 앱 설정: Interactivity & Shortcuts를 켜야 합니다 (Socket Mode는 Request URL 불필요).
"""

from __future__ import annotations

import json
import re

ACTION_PREFIX = "cartsignal_"
ACTION_ID_PATTERN = re.compile(rf"^{ACTION_PREFIX}")
VALUE_LIMIT = 1900                     # Slack 버튼 value 최대 2000자

_APPROVAL_BUTTONS = [
    ("approve", "승인", "primary"),
    ("reject", "반려", "danger"),
    ("cancel", "승인 요청 취소", None),
]
_WORKFLOW_BUTTONS = {
    "blocked": [("continue", "다시 시도", "primary"), ("status", "진행 상황", None), ("cancel", "워크플로 취소", "danger")],
    "waiting_input": [("status", "진행 상황", None), ("cancel", "워크플로 취소", "danger")],
}
LABELS = {("approval", d): label for d, label, _ in _APPROVAL_BUTTONS}
LABELS.update({("workflow", c): label for buttons in _WORKFLOW_BUTTONS.values() for c, label, _ in buttons})


def _button(action_id: str, text: str, value: dict, style: str | None = None, confirm: dict | None = None) -> dict:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(raw) > VALUE_LIMIT:
        raise ValueError("버튼 값이 너무 깁니다.")
    button = {"type": "button", "action_id": action_id, "text": {"type": "plain_text", "text": text}, "value": raw}
    if style:
        button["style"] = style
    if confirm:
        button["confirm"] = confirm
    return button


def build_action_blocks(actions: list[dict] | None, thread_id: str, requester_user_id: str = "") -> list[dict]:
    """결과의 actions → Slack Block Kit 블록. 보여 줄 버튼이 없으면 빈 목록."""
    blocks = []
    for item in actions or []:
        kind = item.get("type")
        if kind == "approval" and item.get("token"):
            title = item.get("title") or "승인 요청"
            # 승인 버튼도 내부 단계 번호 없이 표시합니다.
            from interfaces.slack.message_policy import hide_workflow_stage_labels
            title = hide_workflow_stage_labels(str(title))
            buttons = [_button(f"{ACTION_PREFIX}approval_{d}", label,
                               {"k": "approval", "d": d, "tok": item["token"], "t": thread_id, "owner": requester_user_id}, style)
                       for d, label, style in _APPROVAL_BUTTONS]
            blocks += [{"type": "section", "text": {"type": "mrkdwn",
                                                    "text": f"*{title}* — 버튼으로 선택해 주세요. 수정은 채팅으로 입력 (예: \"카디건으로 수정\")"}},
                       {"type": "actions", "block_id": f"{ACTION_PREFIX}approval", "elements": buttons}]
        elif kind == "category_confirm" and item.get("run_id") and item.get("category"):
            category = str(item["category"])
            buttons = [_button(f"{ACTION_PREFIX}category_confirm", f"{category} 승인",
                               {"k": "category_confirm", "run": item["run_id"], "category": category,
                                "t": thread_id, "owner": requester_user_id}, "primary")]
            blocks += [{"type": "section", "text": {"type": "mrkdwn",
                        "text": f"*대분류 최종 확인* — {category}로 확정하시겠습니까? 승인 시 세부 유형 순위와 색상별 구매 근거를 안내합니다."}},
                       {"type": "actions", "block_id": f"{ACTION_PREFIX}category", "elements": buttons}]
        elif kind == "subtype_suggestion" and item.get("run_id") and item.get("subtype") and item.get("colors"):
            subtype, colors = str(item["subtype"]), [str(c) for c in item["colors"]]
            buttons = [_button(f"{ACTION_PREFIX}subtype_suggestion", "추천 유형·상위 2색 선택",
                               {"k": "subtype_suggestion", "run": item["run_id"], "subtype": subtype,
                                "colors": colors, "t": thread_id, "owner": requester_user_id}, "primary")]
            blocks += [{"type": "section", "text": {"type": "mrkdwn",
                        "text": f"*세부 유형·색상 선택* — 추천: {subtype} / {', '.join(colors)}. 다른 조합은 채팅으로 입력하세요."}},
                       {"type": "actions", "block_id": f"{ACTION_PREFIX}subtype", "elements": buttons}]
        elif kind == "workflow" and item.get("status") in _WORKFLOW_BUTTONS and item.get("run_id"):
            confirm = {"title": {"type": "plain_text", "text": "워크플로 취소"},
                       "text": {"type": "mrkdwn", "text": "진행 중인 워크플로를 취소할까요? 확정값은 그대로 남습니다."},
                       "confirm": {"type": "plain_text", "text": "취소하기"},
                       "deny": {"type": "plain_text", "text": "돌아가기"}}
            buttons = [_button(f"{ACTION_PREFIX}workflow_{c}", label,
                               {"k": "workflow", "c": c, "run": item["run_id"], "t": thread_id, "owner": requester_user_id}, style,
                               confirm if c == "cancel" else None)
                       for c, label, style in _WORKFLOW_BUTTONS[item["status"]]]
            note = "멈춘 단계를 다시 시도하거나 취소할 수 있습니다." if item["status"] == "blocked" \
                else "선택할 값은 채팅으로 말씀해 주세요. 진행 상황 확인·취소는 버튼으로 할 수 있습니다."
            blocks += [{"type": "section", "text": {"type": "mrkdwn", "text": f"*워크플로* — {note}"}},
                       {"type": "actions", "block_id": f"{ACTION_PREFIX}workflow", "elements": buttons}]
    return blocks


def parse_action(body: dict) -> dict | None:
    """버튼 클릭 payload → 처리에 필요한 값. 우리 버튼이 아니거나 값이 이상하면 None."""
    actions = body.get("actions") or []
    if not actions or not ACTION_ID_PATTERN.match(str(actions[0].get("action_id", ""))):
        return None
    try:
        value = json.loads(actions[0].get("value") or "{}")
    except (TypeError, ValueError):
        return None
    channel = (body.get("channel") or {}).get("id") or (body.get("container") or {}).get("channel_id") or ""
    message = body.get("message") or {}
    user = (body.get("user") or {}).get("id") or ""
    if not channel or not user or not isinstance(value, dict):
        return None
    if value.get("k") == "approval" and value.get("d") in {d for d, _, _ in _APPROVAL_BUTTONS} and value.get("tok"):
        action = {"type": "approval", "decision": value["d"], "token": str(value["tok"])}
        label = LABELS[("approval", value["d"])]
    elif value.get("k") == "category_confirm" and value.get("run") and value.get("category"):
        action = {"type": "category_confirm", "run_id": str(value["run"]), "category": str(value["category"])}
        label = f"{action['category']} 확정"
    elif (value.get("k") == "subtype_suggestion" and value.get("run") and value.get("subtype")
          and isinstance(value.get("colors"), list) and value["colors"]):
        action = {"type": "subtype_suggestion", "run_id": str(value["run"]),
                  "subtype": str(value["subtype"]), "colors": [str(c) for c in value["colors"]]}
        label = "추천 유형·상위 2색 선택"
    elif value.get("k") == "workflow" and ("workflow", value.get("c")) in LABELS and value.get("run"):
        action = {"type": "workflow", "command": value["c"], "run_id": str(value["run"])}
        label = LABELS[("workflow", value["c"])]
    else:
        return None
    message_ts = message.get("ts") or (body.get("container") or {}).get("message_ts") or ""
    event = {
        "channel": channel,
        "user": user,
        # DM 채널 ID는 D로 시작 (build_slack_thread_id가 DM은 사용자별 대화로 묶음)
        "channel_type": "im" if channel.startswith("D") else "channel",
        "ts": message_ts,
    }
    if message.get("thread_ts"):
        event["thread_ts"] = message["thread_ts"]
    return {
        "action": action,
        "label": label,
        "thread_id": str(value.get("t") or ""),
        "requester_user_id": str(value.get("owner") or ""),
        "event": event,
        "team_id": (body.get("team") or {}).get("id") or "",
        "message_ts": message_ts,
        "blocks": message.get("blocks") or [],
    }


def resolved_blocks(blocks: list[dict], label: str, user: str) -> list[dict]:
    """누른 뒤: 우리 버튼 줄을 없애고 선택 결과 한 줄로 바꿈 (다른 블록은 그대로)."""
    out = []
    for block in blocks or []:
        if block.get("type") == "actions" and str(block.get("block_id", "")).startswith(ACTION_PREFIX):
            out.append({"type": "context", "elements": [
                {"type": "mrkdwn", "text": f"선택: *{label}* (<@{user}>) · 처리 중"}]})
        else:
            out.append(block)
    return out
