"""모델링 파이프라인 테스트 (가짜 데이터, 임시 폴더에서 실행).

프로젝트의 modeling/·database/sql·common/·run_modeling.py를 임시 폴더로 복사해서
setup → build → train → predict 를 실행합니다.
실제 database/synerise.duckdb, modeling/models, modeling/reports는 건드리지 않습니다.
lightgbm·scikit-learn이 없거나 Python 3.12 미만이면 건너뜁니다.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

pytest.importorskip("lightgbm")
pytest.importorskip("sklearn")
pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="modeling/config.py는 Python 3.12 이상 필요")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from synthetic_synerise import write_synthetic_parquet  # noqa: E402


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    work = tmp_path_factory.mktemp("modeling_sandbox")
    for name in ("modeling", "common", "agent_system"):
        shutil.copytree(ROOT / name, work / name,
                        ignore=shutil.ignore_patterns("__pycache__", "models", "*.joblib"))
    shutil.copytree(ROOT / "database" / "sql", work / "database" / "sql")
    shutil.copy(ROOT / "run_modeling.py", work / "run_modeling.py")
    shutil.rmtree(work / "modeling" / "reports", ignore_errors=True)
    write_synthetic_parquet(work / "synerise_dataset")
    env = {**os.environ,
           "CART_SIGNAL_MODELING_DB": str(work / "database" / "synerise.duckdb"),
           "CART_SIGNAL_SYNERISE_DATA_DIR": str(work / "synerise_dataset")}
    return work, env


def run(work, env, *args):
    proc = subprocess.run([sys.executable, "run_modeling.py", *args], cwd=work, env=env,
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    return proc


def test_pipeline_end_to_end(sandbox):
    work, env = sandbox
    run(work, env, "setup")
    run(work, env, "all")

    con = duckdb.connect(env["CART_SIGNAL_MODELING_DB"], read_only=True)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT table_schema||'.'||table_name FROM information_schema.tables").fetchall()}
        assert {"raw.add_to_cart", "clean.add_to_cart", "modeling.build_params",
                "modeling.purchase_training_data", "mart.purchase_predictions"} <= tables
        labels = dict(con.execute(
            "SELECT coalesce(label::VARCHAR,'null'), COUNT(*) FROM modeling.purchase_training_data GROUP BY 1").fetchall())
        assert labels.get("1") and labels.get("0") and labels.get("null")
        segments = dict(con.execute(
            "SELECT promotion_segment, COUNT(*) FROM mart.purchase_predictions GROUP BY 1").fetchall())
        assert set(segments) == {"low", "target", "high"}
    finally:
        con.close()

    models = sorted(p.name for p in (work / "modeling" / "models").glob("*.joblib"))
    assert models == ["baseline_category_60m_deadline_48h.joblib", "baseline_remove_60m_deadline_48h.joblib",
                      "lightgbm_60m_deadline_48h.joblib", "logistic_regression_60m_deadline_48h.joblib"]
    assert (work / "modeling" / "reports" / "model_comparison_deadline_48h.csv").exists()

    status = run(work, env, "status").stdout
    assert "mart.purchase_predictions" in status


def test_model_tools_read_only(sandbox):
    work, env = sandbox
    code = (
        "import json\n"
        "from agent_system.common_tools.execution_tools import model_tools as m\n"
        "s = m.get_modeling_status()\n"
        "r = m.load_purchase_predictions('target', limit=5)\n"
        "bad = m.load_purchase_predictions('xx')\n"
        "print(json.dumps({'has': s['has_predictions'], 'seg': s['segment_counts'], 'n': len(r['dataframe']),"
        " 'err': r['error'], 'seg_ok': set(r['dataframe']['promotion_segment']) == {'target'},"
        " 'bad': bool(bad['error'])}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=work, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-3000:]
    import json
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["has"] and out["n"] == 5 and out["seg_ok"] and not out["err"] and out["bad"]
    assert set(out["seg"]) == {"low", "target", "high"}


def test_read_only_connection_requires_file(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT))
    from common import db_client

    monkeypatch.setenv("CART_SIGNAL_MODELING_DB", str(tmp_path / "none.duckdb"))
    with pytest.raises(FileNotFoundError):
        db_client.get_duckdb_connection(read_only=True)
    assert not (tmp_path / "none.duckdb").exists()
