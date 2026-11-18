"""보고서 만들기 공통 (PDF + Word 2부).

보고서 종류 (두 가지 모두 같은 문서 구조·같은 출력기를 씁니다)
    analysis   일반 분석 보고서        대화 중 여러 분석 결과를 묶음 (지금 동작)
    campaign   기획전 실행 계획 보고서  확정값·워크플로 단계 결과를 묶음
               지금: 대화에 기록된 값으로 만든 '초안' (백엔드 보고서 기능 연결 전임을 표시)
               연결 후: ⑥ 결과 정리 Agent의 generate_campaign_report(백엔드 /reports/campaign) 결과를
                        같은 문서 구조(document.py)로 받아 같은 출력기로 PDF·Word를 만들면 됩니다.

    document.py      문서 구조 (ReportDocument · Section · 블록)
    analysis_log.py  분석 결과 기록 (질문·답변·표·그래프·기준) — 보고서 재료, runtime/report_data/에 표 저장
    builders.py      기록 → 문서 구조 (build_analysis_report, build_campaign_report)
    writers.py       문서 구조 → PDF(reportlab) · Word(python-docx)

원칙: 보고서는 실제 분석 결과만 옮깁니다. LLM이 새로 쓰거나 숫자를 만들지 않습니다.
"""
