"""공통 Tool output_tools (결과 CSV) 테스트.  pytest tests/test_tools -v"""

import pandas as pd

from agent_system.common_tools.execution_tools import output_tools


def _use_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(output_tools, "CSV_OUTPUT_DIR", tmp_path)


def test_processed_first_and_saved_once(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    result = {"dataframe": pd.DataFrame({"a": [1]}), "processed_dataframe": pd.DataFrame({"b": [1, 2]})}
    first = output_tools.prepare_result_csv(result)
    assert first["result_type"] == "processed" and first["path"].parent == tmp_path
    assert pd.read_csv(first["path"], encoding="utf-8-sig").columns.tolist() == ["b"]
    second = output_tools.prepare_result_csv(result)          # Slack·API가 같이 불러도 한 번만 생성
    assert second["path"] == first["path"] and len(list(tmp_path.iterdir())) == 1


def test_zero_rows_and_too_large(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    empty = output_tools.prepare_result_csv({"dataframe": pd.DataFrame({"a": []})})
    assert empty["path"] is None and "0행" in empty["skipped_reason"]
    monkeypatch.setattr(output_tools, "MAX_AUTO_CSV_ROWS", 2)
    big = {"dataframe": pd.DataFrame({"a": [1, 2, 3]})}
    status = output_tools.prepare_result_csv(big)
    assert status["path"] is None and "생략" in status["skipped_reason"] and big["csv_skipped_reason"]


def test_no_table():
    status = output_tools.prepare_result_csv({"final_answer": "안녕하세요"})
    assert status["path"] is None and status["result_type"] is None and not status["skipped_reason"]
