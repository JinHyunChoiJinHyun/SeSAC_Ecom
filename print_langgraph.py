from pathlib import Path
import sys

print("[1] print_langgraph.py 실행 시작")

# --------------------------------------------------
# WORKS 경로 잡기
# --------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
print(f"[2] WORKS 경로: {BASE_DIR}")

# --------------------------------------------------
# import 경로 추가
# --------------------------------------------------
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# --------------------------------------------------
# 출력 폴더 생성
# --------------------------------------------------
OUTPUT_DIR = BASE_DIR / "langgraph_output"
OUTPUT_DIR.mkdir(exist_ok=True)
print(f"[3] 출력 폴더 생성: {OUTPUT_DIR}")

# --------------------------------------------------
# LangGraph import
# --------------------------------------------------
try:
    from agent_system.common_tools.graph import graph
    print("[4] LangGraph 구조 가져오기 성공")
except Exception as e:
    print("[ERROR] graph import 실패")
    print(repr(e))
    sys.exit(1)

# --------------------------------------------------
# Mermaid 코드 생성
# --------------------------------------------------
try:
    graph_obj = graph.get_graph()
    mermaid_text = graph_obj.draw_mermaid()
    print("[5] Mermaid 코드 생성 성공")
except Exception as e:
    print("[ERROR] Mermaid 코드 생성 실패")
    print(repr(e))
    sys.exit(1)

# --------------------------------------------------
# Mermaid 콘솔 출력
# --------------------------------------------------
print("\n" + "=" * 80)
print("CART SIGNAL - Shared Analysis LangGraph")
print("=" * 80)
print(mermaid_text)
print("=" * 80 + "\n")

# --------------------------------------------------
# Mermaid 파일 저장
# --------------------------------------------------
mmd_path = OUTPUT_DIR / "shared_analysis_graph.mmd"
mmd_path.write_text(mermaid_text, encoding="utf-8")
print("[6] Mermaid 파일 저장 완료")
print(mmd_path)

# --------------------------------------------------
# PNG 저장
# --------------------------------------------------
png_path = OUTPUT_DIR / "shared_analysis_graph.png"

try:
    png_bytes = graph_obj.draw_mermaid_png()
    with open(png_path, "wb") as f:
        f.write(png_bytes)

    print("[7] PNG 파일 저장 완료")
    print(png_path)

except Exception as e:
    print("[WARN] PNG 생성 실패")
    print("Mermaid 텍스트 파일(.mmd)는 저장되었지만 PNG 변환은 실패했습니다.")
    print("오류 내용:", repr(e))

print("\n✅ LangGraph 구조 출력 완료")

# --------------------------------------------------
# 멀티턴 Orchestrator 그래프 (Orchestrator → 특화 Agent)
# --------------------------------------------------
try:
    from agent_system.orchestrator.graph import build_graph as build_conversation_graph

    conversation_obj = build_conversation_graph().get_graph()
    conversation_mermaid = conversation_obj.draw_mermaid()

    print("\n" + "=" * 80)
    print("CART SIGNAL - Orchestrator LangGraph (멀티턴)")
    print("=" * 80)
    print(conversation_mermaid)
    print("=" * 80 + "\n")

    conversation_mmd = OUTPUT_DIR / "orchestrator_graph.mmd"
    conversation_mmd.write_text(conversation_mermaid, encoding="utf-8")
    print("[8] 대화 그래프 Mermaid 저장 완료")
    print(conversation_mmd)

    try:
        conversation_png = OUTPUT_DIR / "orchestrator_graph.png"
        conversation_png.write_bytes(conversation_obj.draw_mermaid_png())
        print("[9] 대화 그래프 PNG 저장 완료")
        print(conversation_png)
    except Exception as e:
        print("[WARN] 대화 그래프 PNG 생성 실패:", repr(e))

except Exception as e:
    print("[WARN] 대화 그래프 출력 실패:", repr(e))
