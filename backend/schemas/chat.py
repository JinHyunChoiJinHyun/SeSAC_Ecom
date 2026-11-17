"""채팅 API 요청·응답 형식."""

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="사용자 메시지")
    user_id: str = Field("api-user", max_length=200, description="사용자 ID (대화 ID를 만들 때 사용)")
    thread_id: str | None = Field(None, max_length=300,
                                  description="대화 ID. 비우면 api:<user_id>. Slack과 같은 대화를 이어 가려면 dm:<Slack 사용자 ID>")


class TablePayload(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    total_rows: int
    truncated: bool


class FilePayload(BaseModel):
    kind: str                    # png / csv
    file_name: str
    url: str


class ChatResponse(BaseModel):
    thread_id: str
    turn_type: str               # chitchat / analysis / approval_request(승인 질문) / approval(승인 처리 결과)
    answer: str
    route: str = ""              # 처리한 특화 Agent (일상 대화면 빈 값)
    standalone_question: str = ""
    is_followup: bool = False
    conversation_reset: bool = False
    sql: str = ""
    data_sources: list[str] = []
    table: TablePayload | None = None
    files: list[FilePayload] = []
    error: str = ""
    awaiting_approval: bool = Field(False, description="승인 대기 중이면 True (다음 메시지로 승인/반려/수정/취소)")
    campaign: dict[str, Any] = Field(default_factory=dict, description="확정된 기획전 정보 (승인 처리 턴에서 반환)")
    elapsed_sec: float = 0.0


class ConversationMessage(BaseModel):
    role: str
    name: str = ""
    content: str


class ConversationDetail(BaseModel):
    thread_id: str
    last_activity_at: str = ""
    messages: list[ConversationMessage]
    last_analysis: dict[str, Any] = {}
