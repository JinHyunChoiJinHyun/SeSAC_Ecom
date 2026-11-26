"""기존 로컬 업무 함수에 대한 제한된 실행 어댑터. LLM Tool 바인딩 대상이 아니다."""
from importlib import import_module
from types import MappingProxyType


def freeze_tools(mapping):
    """역할별 허용 함수 목록을 고정한다."""
    return MappingProxyType(dict(mapping))


def execute_tool(tools, action, /, *args, **kwargs):
    """기존 함수의 인자·반환값·예외를 그대로 전달한다."""
    if not isinstance(action, str) or action not in tools:
        raise ValueError("허용되지 않은 워크플로 Tool입니다.")
    module, function = tools[action]
    return getattr(import_module(module), function)(*args, **kwargs)


def workflow_tool(agent_name, action):
    """기존 제어 흐름에서만 호출하는 명시적 Agent 실행 진입점."""
    def call(*args, **kwargs):
        from agent_system.orchestrator.registry import invoke_workflow_tool
        return invoke_workflow_tool(agent_name, action, *args, **kwargs)
    call.__name__ = action
    call.__qualname__ = action
    return call
