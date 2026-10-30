from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import threading
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*(\w+)\s*\|\s*run_id=([^|]+)\|\s?(.*)$")
STAGES = {
    "Supervisor Agent": "Supervisor", "Data Agent": "Data", "SQL Executor": "SQL 실행",
    "Prep Agent": "전처리", "Model Agent": "모델", "Experiment Agent": "실험",
    "Visualization Agent": "시각화", "Final Response": "최종 응답",
}
ERROR_SIGNAL = re.compile(r"Traceback|\b(?:FileNotFoundError|ModuleNotFoundError|RuntimeError|ValueError|PermissionError)\b|\[(?:DuckDB 연결 오류|SQL 실행 오류)\]")
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".csv", ".json", ".pdf"}
OUTPUT_FOLDERS = ("visualization_output", "delivery_output")


def redact(value: Any) -> str:
    text = str(value)
    text = re.sub(r"\b(?:xox[baprs]-|xapp-|sk-)[A-Za-z0-9_\-]{8,}", "[인증정보 가림]", text)
    text = re.sub(r"(?i)((?:api[_ -]?key|access[_ -]?token|authorization|password|secret)\s*[=:]\s*)[^\s,;]+", r"\1[가림]", text)
    return text


def empty_run(run_id: str, source: str = "log") -> dict:
    return {"id": run_id, "question": "질문 기록 없음", "started_at": None, "ended_at": None,
            "duration": None, "status": "unknown", "handler_status": "unknown",
            "delivery_status": "unknown", "analysis_status": "unknown",
            "outcome_recorded_at": None, "failure_notice_status": "unknown",
            "channel": "미기록", "answer": None,
            "stages": [], "decisions": [], "sql": "", "errors": [], "logs": [],
            "files": [], "origin": source, "warnings": []}


def parse_logs(paths: list[Path], max_bytes: int = 4_000_000) -> tuple[list[dict], list[str]]:
    runs: dict[str, dict] = {}
    notices = []
    # 날짜별 보관 로그를 먼저 읽고 현재 로그를 마지막에 읽습니다.
    for path in sorted(paths, key=lambda p: (p.name == "app.log", p.name)):
        try:
            with path.open("rb") as handle:
                size = path.stat().st_size
                if size > max_bytes:
                    handle.seek(-max_bytes, 2)
                    handle.readline()
                    notices.append(f"{path.name}: 최근 약 {max_bytes // 1_000_000}MB만 조회했습니다.")
                content = handle.read(max_bytes).decode("utf-8-sig", errors="replace")
        except OSError:
            notices.append(f"{path.name}: 읽을 수 없습니다.")
            continue
        for line in content.splitlines():
            match = LOG_LINE.match(line)
            if not match:
                continue
            stamp, level, run_id, message = match.groups()
            run_id = run_id.strip()
            if run_id == "-":
                continue  # 요청 ID 없는 Delivery 로그를 특정 요청에 추측해서 붙이지 않습니다.
            run = runs.setdefault(run_id, empty_run(run_id))
            # 구조화된 결과는 가림 처리 전 JSON으로 해석하고 표시할 때 가립니다.
            raw_message = message
            message = redact(message)
            run["logs"].append({"time": stamp, "level": level, "message": message})
            if message.startswith("REQUEST_START |"):
                run["started_at"] = stamp
                question = message.partition("question=")[2]
                if question:
                    run["question"] = question
            if message.startswith("REQUEST_END |"):
                run["ended_at"] = stamp
                status = re.search(r"status=(\w+)", message)
                duration = re.search(r"elapsed_sec=([\d.]+)", message)
                run["handler_status"] = status.group(1) if status else "unknown"
                run["duration"] = float(duration.group(1)) if duration else None
            for label, title in STAGES.items():
                if f"[{label}]" in message and (not run["stages"] or run["stages"][-1]["name"] != title):
                    run["stages"].append({"name": title, "time": stamp})
            if message.startswith("reason ="):
                run["decisions"].append(message.split("=", 1)[1].strip())
            if level in {"ERROR", "CRITICAL"} or ERROR_SIGNAL.search(message):
                run["errors"].append(message)
            if "[생성된 SQL]" in message:
                run["_sql"] = True
                continue
            if run.get("_sql"):
                if message.startswith("[") or message.startswith(("REQUEST_END |", "ADMIN_RESULT ")):
                    run["_sql"] = False
                else:
                    run["sql"] += message + "\n"
            if raw_message.startswith("ADMIN_RESULT "):
                try:
                    data = json.loads(raw_message.removeprefix("ADMIN_RESULT "))
                    # 같은 요청의 재시도는 시간순으로 가장 나중의 명시적 결과를 사용합니다.
                    if data.get("analysis_status") not in {"completed", "failed"}:
                        continue
                    if run["outcome_recorded_at"] and stamp < run["outcome_recorded_at"]:
                        continue
                    run["outcome_recorded_at"] = stamp
                    run["analysis_status"] = data["analysis_status"]
                    delivery = data.get("delivery_status", "unknown")
                    run["delivery_status"] = delivery if delivery in {
                        "success", "failed", "partial_success", "not_attempted"
                    } else "unknown"
                    notice = data.get("failure_notice_status", "unknown")
                    run["failure_notice_status"] = notice if notice in {"success", "failed"} else "unknown"
                    if isinstance(data.get("channel"), str):
                        run["channel"] = redact(data["channel"])
                    if isinstance(data.get("answer"), str):
                        run["answer"] = redact(data["answer"])
                except (ValueError, TypeError, AttributeError):
                    pass

    for run in runs.values():
        run.pop("_sql", None)
        run["sql"] = run["sql"].strip()
        # 로그 구간의 정상 종료와 사용자 요청의 처리 결과는 별개입니다.
        if run["analysis_status"] in {"completed", "failed"}:
            run["status"] = run["analysis_status"]
            if run["status"] == "completed" and run["errors"]:
                run["warnings"].append("중간 오류 기록은 있지만 명시적인 최종 결과에 따라 요청 처리는 성공으로 분류합니다.")
            elif run["status"] == "failed":
                run["warnings"].append("명시적인 결과 기록으로 요청 처리 실패를 확인했습니다. 처리 로그의 success와는 별개입니다.")
        elif run["errors"] or run["handler_status"] == "error":
            run["status"] = "failed"
        elif run["ended_at"]:
            run["status"] = "unverified"
        elif run["started_at"]:
            run["status"] = "unfinished"
        if run["handler_status"] == "success" and run["errors"] and run["status"] != "completed":
            run["warnings"].append("처리 로그는 success이지만 오류 신호가 있습니다. 분석 성공으로 집계하지 않습니다.")
        if run["delivery_status"] in {"failed", "partial_success"}:
            run["warnings"].append("요청 처리 결과와 별개로 Slack 결과 전송에 실패 또는 일부 실패가 기록되었습니다.")
        if run["delivery_status"] == "not_attempted":
            run["warnings"].append("요청 처리에 실패하여 결과 전송은 시도하지 않았습니다. 실패 안내 전송과는 별개입니다.")
        if run["status"] == "unfinished":
            run["warnings"].append("종료 기록이 없습니다. 실제 실행 중인지 중단되었는지는 로그만으로 확정할 수 없습니다.")
        run["logs"] = run["logs"][-2000:]
        run["errors"] = list(dict.fromkeys(run["errors"]))[:30]
    return list(runs.values()), list(dict.fromkeys(notices))


@dataclass
class Settings:
    project: Path
    db: Path | None = None


class Dashboard:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.cache_key = None
        self.cached = None

    def snapshot(self) -> dict:
        log_dir = self.settings.project / "logs"
        candidates = sorted(log_dir.glob("app.log*"), key=lambda p: (p.name == "app.log", p.name)) if log_dir.is_dir() else []
        paths = [p for p in candidates if p.is_file() and not p.is_symlink() and p.suffix != ".gz"][-15:]
        key = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths if p.exists())
        db = self.settings.db or self.settings.project / "admin_data" / "requests.sqlite3"
        if db.exists():
            key += ((str(db), db.stat().st_mtime_ns, db.stat().st_size),)
            wal = Path(str(db) + "-wal")
            if wal.exists():
                key += ((str(wal), wal.stat().st_mtime_ns, wal.stat().st_size),)
        with self.lock:
            if self.cache_key == key and self.cached is not None:
                return self.cached
            runs, notices = parse_logs(paths)
            if len(candidates) > 15:
                notices.append("최근 로그 파일 15개만 조회합니다. 전체 기간 통계가 아닐 수 있습니다.")
            if db.exists():
                try:
                    with closing(sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=3)) as con:
                        observed = [json.loads(row[0]) for row in con.execute("SELECT payload FROM requests ORDER BY rowid DESC LIMIT 5000")]
                    indexed = {r["id"]: r for r in runs}
                    for record in observed:
                        old = indexed.get(record["id"], {})
                        record["logs"] = old.get("logs", record.get("logs", []))
                        record["stages"] = old.get("stages") or record.get("stages", [])
                        record["decisions"] = old.get("decisions", [])
                        record["sql"] = old.get("sql") or record.get("sql", "")
                        if old.get("outcome_recorded_at") and old["outcome_recorded_at"] >= (record.get("ended_at") or ""):
                            for field in ("status", "analysis_status", "delivery_status", "failure_notice_status", "outcome_recorded_at", "warnings", "errors"):
                                record[field] = old.get(field)
                        indexed[record["id"]] = record
                    runs = list(indexed.values())
                except (sqlite3.Error, ValueError, KeyError):
                    notices.append("요청 기록 DB를 읽지 못했습니다. 로그 기록만 표시합니다.")
            if not paths and not runs:
                notices.append("읽을 요청 기록이 없습니다. 프로젝트 경로와 logs/app.log를 확인해 주세요.")
            result = {"runs": sorted(runs, key=lambda r: r.get("started_at") or "", reverse=True), "notices": notices, "log_files": len(paths), "mode": "live"}
            self.cache_key, self.cached = key, result
            return result

    def file_ref(self, raw: str) -> dict | None:
        project = self.settings.project.resolve()
        path = Path(raw)
        path = path if path.is_absolute() else project / path
        path = path.resolve()
        if not path.is_relative_to(project):
            return None
        if path.suffix.lower() not in ALLOWED_SUFFIXES or not path.is_file():
            return None
        if not any(path.is_relative_to((project / folder).resolve()) for folder in OUTPUT_FOLDERS):
            return None
        relative = path.relative_to(project).as_posix()
        return {"id": hashlib.sha256(relative.encode()).hexdigest()[:24], "name": path.name,
                "size": path.stat().st_size, "relative": relative, "kind": path.suffix.lower()[1:]}

    def public_run(self, run: dict, detail: bool = False) -> dict:
        fields = ("id", "question", "started_at", "ended_at", "duration", "status", "handler_status", "delivery_status", "analysis_status", "failure_notice_status", "outcome_recorded_at", "channel", "origin")
        out = {key: redact(run[key]) if isinstance(run.get(key), str) else run.get(key) for key in fields}
        out["error_count"] = len(run.get("errors", []))
        out["stages"] = run.get("stages", [])
        out["files"] = [ref for raw in run.get("files", []) if (ref := self.file_ref(raw))]
        if detail:
            for key in ("answer", "sql", "decisions", "errors", "warnings", "logs"):
                out[key] = run.get(key)
            def clean(value):
                if isinstance(value, str):
                    return redact(value)
                if isinstance(value, list):
                    return [clean(item) for item in value]
                if isinstance(value, dict):
                    return {key: clean(item) for key, item in value.items()}
                return value
            out = clean(out)
        return out


def select_runs(runs: list[dict], query: str = "", status: str = "all", start: str = "", end: str = "") -> list[dict]:
    query = query.casefold().strip()
    return [r for r in runs if (not query or query in (r["question"] + " " + r["id"]).casefold())
            and (status == "all" or r["status"] == status)
            and (not start or (r.get("started_at") or "")[:10] >= start)
            and (not end or (r.get("started_at") or "9999")[:10] <= end)]


def summary(runs: list[dict]) -> dict:
    counts = Counter(r["status"] for r in runs)
    durations = [r["duration"] for r in runs if isinstance(r.get("duration"), (float, int))]
    timeline: dict[str, dict] = {}
    agents = Counter()
    for run in runs:
        day = (run.get("started_at") or "")[:10]
        if day:
            bucket = timeline.setdefault(day, {"date": day, "total": 0, "failed": 0})
            bucket["total"] += 1
            bucket["failed"] += int(run["status"] == "failed")
        agents.update(s["name"] for s in run.get("stages", []))
    return {"total": len(runs), "counts": dict(counts), "average_duration": round(sum(durations) / len(durations), 2) if durations else None,
            "duration_count": len(durations), "delivery_failed": sum(r["delivery_status"] in {"failed", "partial_success"} for r in runs),
            "timeline": sorted(timeline.values(), key=lambda r: r["date"]), "agents": dict(agents)}


def export_csv(runs: list[dict]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["요청 ID", "질문", "시작 시간", "종료 시간", "소요 시간(초)", "요청 처리 상태", "처리 로그 상태", "Slack 전달"])
    for run in runs:
        values = [run.get(k, "") for k in ("id", "question", "started_at", "ended_at", "duration", "status", "handler_status", "delivery_status")]
        writer.writerow([("'" + redact(v)) if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else redact(v) if v is not None else "" for v in values])
    return "\ufeff" + stream.getvalue()
