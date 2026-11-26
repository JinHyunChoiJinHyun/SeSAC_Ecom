"""구매 예측 모델 학습·저장 CLI

python -m modeling.purchase_model.train --as-of 2020-09-23 [--complete-through 2020-09-22] [--parquet-dir 폴더] [--out 폴더]
종료 코드: 0 학습·저장 완료, 1 실패
λ·종료 기준은 코드 상수(model.LAMBDA, DEC_TOL, MAX_ITER)로 고정하며 CLI에서 바꾸지 않는다.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from modeling.purchase_model.data import connect_hm, connect_parquet
from modeling.purchase_model.errors import TargetingError
from modeling.purchase_model.model import MODELS_DIR, save_model, train_model

EXIT_OK, EXIT_FAILED = 0, 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m modeling.purchase_model.train", description="기준일별 구매 예측 모델 학습·저장")
    parser.add_argument("--as-of", required=True, help="학습 as_of YYYY-MM-DD (예측 시작일, 학습은 전날까지의 거래)")
    parser.add_argument("--complete-through", default=None, help="구매 기록이 완전한 마지막 날 (기본: 마지막 거래일)")
    parser.add_argument("--parquet-dir", default=None, help="지정하면 팀 연결 대신 이 폴더의 Parquet을 읽음")
    parser.add_argument("--out", default=str(MODELS_DIR), help=f"구매 예측 모델 저장 루트 (기본: {MODELS_DIR})")
    parser.add_argument("--report", default=None, help="실행 결과 JSON을 저장할 경로")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.perf_counter()
    code = EXIT_FAILED
    try:
        data = connect_parquet(args.parquet_dir) if args.parquet_dir else connect_hm()
        with data:
            model = train_model(data, args.as_of, args.complete_through)
        path = save_model(model, args.out)
        result = {"status": "ok", "path": str(path)}
        code = EXIT_OK
        result.update(as_of=model.meta["as_of"], run_id=model.meta.get("run_id"), combos=model.meta["counts"]["combos"],
                      agg_rows=model.meta["counts"]["agg_rows"], fit=model.meta["fit"], complete_through=model.meta["complete_through"])
    except (TargetingError, ValueError) as exc:
        result = {"status": "failed", "error_type": type(exc).__name__, "reason": str(exc)}
    result["seconds"] = round(time.perf_counter() - t0, 1)
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(text, encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
