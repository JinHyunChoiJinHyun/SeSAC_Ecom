"""백엔드 ↔ 에이전트(Orchestrator) 연결 테스트 (pytest, LLM·OpenAI 키·DuckDB·PostgreSQL 불필요).

    pytest tests/test_backend -v
"""

import dataclasses
from contextlib import contextmanager

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver


class _Config:
    def public(self):
        return {"provider": "stub", "model": "stub"}


@contextmanager
def _fake_model_request():
    yield _Config()


class _IntentStub:
    def __init__(self):
        self.plan = None
        self.inputs = []

    def invoke(self, payload, *args, **kwargs):
        self.inputs.append(payload["messages"][-1].content)
        return {"structured_response": self.plan}


CALLS = []


def _general_stub(request):
    CALLS.append(request)
    return {
        "final_answer": f"분석 결과: {request.question}",
        "dataframe": pd.DataFrame({"product_type_name": ["Sweater", "Trousers"], "purchase_count": [10, 5]}),
        "sql_query": "SELECT 1",
        "data_sources": ["hm"],
    }


@pytest.fixture()
def client(monkeypatch):
    import env.model_settings as model_settings
    from agent_system.orchestrator import registry
    from agent_system.orchestrator.graph import build_graph
    from agent_system.orchestrator.tools import intent
    from backend.api import dependencies
    from backend.main import app
    from backend.services import chat_service

    monkeypatch.setattr(model_settings, "model_request", _fake_model_request)
    stub = _IntentStub()
    monkeypatch.setattr(intent, "get_intent_agent", lambda cfg=None: stub)
    agents = list(registry.AGENTS)
    agents[0] = dataclasses.replace(agents[0], runner=f"{__name__}:_general_stub")
    monkeypatch.setattr(registry, "AGENTS", agents)

    service = chat_service.ChatService(graph=build_graph(InMemorySaver()))
    app.dependency_overrides[dependencies.get_chat_service] = lambda: service
    CALLS.clear()
    yield TestClient(app), stub
    app.dependency_overrides.clear()


def _plan(stub, **kwargs):
    from agent_system.orchestrator.tools.intent import IntentDecision

    stub.plan = IntentDecision(reason="stub", **kwargs)


def test_health_api():
    from backend.main import app

    assert TestClient(app).get("/health").json() == {"api": "ok"}


def test_chitchat(client):
    api, stub = client
    _plan(stub, intent="chitchat", reply="안녕하세요")
    data = api.post("/chat", json={"message": "안녕", "user_id": "u1"}).json()
    assert data["turn_type"] == "chitchat" and data["answer"] == "안녕하세요"
    assert data["thread_id"] == "api:u1" and data["files"] == []


def test_analysis_and_multiturn(client):
    api, stub = client
    _plan(stub, intent="analysis", standalone_question="H&M 여성 상품 유형 Top 10")
    first = api.post("/chat", json={"message": "여성 상품 유형 Top 10", "user_id": "u2"}).json()
    assert first["turn_type"] == "analysis" and first["route"] == "general_analysis"
    assert first["table"]["columns"] == ["product_type_name", "purchase_count"]
    assert any(f["kind"] == "csv" for f in first["files"])

    # 후속 질문: 같은 user_id → 같은 대화. 직전 분석 요약이 Orchestrator 입력에 들어감
    _plan(stub, intent="analysis", standalone_question="H&M 남성 상품 유형 Top 10", is_followup=True)
    second = api.post("/chat", json={"message": "남성은?", "user_id": "u2"}).json()
    assert second["is_followup"] and "남성" in second["standalone_question"]
    assert "여성 상품 유형 Top 10" in stub.inputs[-1]
    assert CALLS[-1].last_analysis.get("sql") == "SELECT 1"

    # 다른 사용자에게는 기억이 섞이지 않음
    _plan(stub, intent="chitchat", reply="무엇을 볼까요?")
    api.post("/chat", json={"message": "남성은?", "user_id": "u3"})
    assert "(이전 대화 없음)" in stub.inputs[-1]


def test_csv_download_and_path_guard(client):
    api, stub = client
    _plan(stub, intent="analysis", standalone_question="Top 10")
    files = api.post("/chat", json={"message": "Top 10", "user_id": "u4"}).json()["files"]
    csv = next(f for f in files if f["kind"] == "csv")
    assert api.get(csv["url"]).status_code == 200
    assert api.get("/files/csv/..%2Fsecret.csv").status_code in (400, 404)
    assert api.get("/files/exe/a.exe").status_code == 400


def test_conversation_view_and_delete(client):
    api, stub = client
    _plan(stub, intent="chitchat", reply="안녕하세요")
    api.post("/chat", json={"message": "안녕", "thread_id": "dm:U9"})
    detail = api.get("/conversations/dm:U9").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert api.delete("/conversations/dm:U9").status_code == 200
    assert api.get("/conversations/dm:U9").status_code == 404


def test_agent_health(client, monkeypatch):
    from backend.services import chat_service

    api, _ = client
    monkeypatch.setattr(chat_service.ChatService, "agent_status",
                        staticmethod(lambda: {"agent": "ok", "data_sources": {"hm": True}, "agents": ["general_analysis"],
                                              "memory_db": "x"}))
    assert api.get("/health/agent").json()["agents"] == ["general_analysis"]


def test_approval_flow(client):
    """확정값 결정 → 승인 질문(interrupt) → 다른 질문 → 승인 → 확정값 저장"""
    from agent_system.orchestrator.tools.intent import StateUpdate

    api, stub = client
    _plan(stub, intent="decision", updates=[StateUpdate(key="category", value="니트")])
    asked = api.post("/chat", json={"message": "카테고리는 니트로 확정", "user_id": "u5"}).json()
    assert asked["turn_type"] == "approval_request" and asked["awaiting_approval"]
    _plan(stub, intent="chitchat", reply="1위는 Sweater입니다.")
    side = api.post("/chat", json={"message": "1위가 뭐였지?", "user_id": "u5"}).json()
    assert "[승인 요청]" in side["answer"] and side["awaiting_approval"]
    done = api.post("/chat", json={"message": "승인", "user_id": "u5"}).json()
    assert done["turn_type"] == "approval" and done["campaign"]["values"]["category"] == "니트"
    assert not done["awaiting_approval"]


def test_validation_error(client):
    api, _ = client
    assert api.post("/chat", json={"message": ""}).status_code == 422
