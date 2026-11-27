"""대시보드의 구매 예측 모델 재학습 작업을 관리합니다.

흐름: 학습(별도 프로세스) → 자동 점검(별도 프로세스) → 관리자 [적용] 또는 [버리기]
- 학습 결과는 models/.staging/<job_id>/as_of=…/<run_id>/ 에 저장됩니다. 모델 선택은 models/as_of=* 만 훑으므로
  적용 전에는 고객 점수에 쓰이지 않습니다.
- [적용]은 대기 폴더를 models/as_of=…/<run_id>/ 로 옮기기만 합니다. 모델 코드는 바꾸지 않습니다.
- 한 번에 작업 하나만 진행합니다(학습 중·점검 중·적용 대기 상태에서는 새 학습을 받지 않습니다).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

ACTIVE = {"training", "checking", "ready"}
TRAIN_TIMEOUT = 30 * 60
CHECK_TIMEOUT = 15 * 60
HISTORY_LIMIT = 50
# 에이전트(고객 타깃 선정)가 점수를 계산하는 기준일. 이 날짜 이하 as_of 중 최신 모델만 실제로 쓰입니다.
# agent_system/agents/dynamic_targeting.py MODEL_DATE 등과 같은 값이어야 합니다.
SERVING_AS_OF = "2020-09-23"
# 재시작 때 정리할 수 있는 프로세스(명령줄에 이 모듈 이름이 있는 것만 종료합니다)
JOB_MODULES = ("modeling.purchase_model.train", "admin_dashboard.retrain_check")


class RetrainError(Exception):
    """HTTP 상태 코드와 화면에 보여 줄 메시지를 함께 담습니다."""

    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso_date(value, name: str) -> str:
    try:
        text = str(value)
        if date.fromisoformat(text).isoformat() != text:
            raise ValueError()
        return text
    except (TypeError, ValueError):
        raise RetrainError(422, f"{name}은(는) YYYY-MM-DD 형식의 날짜여야 합니다.") from None


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _process_command_line(pid: int) -> str:
    """Windows 프로세스의 명령줄. 없거나 읽지 못하면 빈 문자열."""
    if os.name != "nt":
        return ""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
            capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


def _kill_orphan(pid) -> bool:
    """대시보드 재시작 전에 띄운 학습·점검 프로세스가 남아 있으면 종료합니다.
    PID가 다른 프로그램에 재사용됐을 수 있으므로 명령줄이 학습·점검 모듈일 때만 종료합니다."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    command = _process_command_line(pid)
    if not any(module in command for module in JOB_MODULES):
        return False
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _tail(path: Path, lines: int = 20) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16384))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


class RetrainManager:
    def __init__(self, project, models_dir=None, runtime_dir=None, python=None, parquet_dir=None, check_top_k=None):
        self.project = Path(project).resolve()
        self.models_dir = Path(models_dir or self.project / "modeling" / "purchase_model" / "models").resolve()
        self.staging = self.models_dir / ".staging"
        self.runtime = Path(runtime_dir or self.project / "admin_dashboard" / "runtime" / "retrain").resolve()
        self.python = python or sys.executable
        self.parquet_dir = parquet_dir    # 테스트용. 화면에서는 지정하지 않음(기본: 팀 연결)
        self.check_top_k = check_top_k
        self.available = (self.project / "modeling" / "purchase_model" / "train.py").is_file()
        self._lock = threading.Lock()
        self._thread = None
        self._job = _read_json(self.runtime / "state.json")
        # 서버가 학습·점검 중에 꺼졌다면 그 작업은 이어 갈 수 없으므로 실패로 정리합니다.
        # 남아 있는 학습·점검 프로세스를 먼저 종료해야 대기 폴더를 지운 뒤 다시 만들지 않습니다.
        if self._job and self._job.get("status") in {"training", "checking"}:
            killed = _kill_orphan(self._job.get("pid"))
            note = " 남아 있던 학습·점검 프로세스를 종료했습니다." if killed else ""
            self._finish("failed", error="대시보드 서버가 다시 시작돼 작업이 중단됐습니다." + note, cleanup=True)

    # ---------------- 조회 ----------------
    def installed_models(self) -> list[dict]:
        out = []
        if not self.models_dir.is_dir():
            return out
        for d in sorted(p for p in self.models_dir.iterdir() if p.is_dir() and p.name.startswith("as_of=")):
            for r in sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")):
                out.append({"as_of": d.name[len("as_of="):], "run_id": r.name, "path": r})
        return out

    def _summary(self, best: dict | None, count: int) -> dict | None:
        if not best:
            return None
        meta = _read_json(best["path"] / "metadata.json") or {}
        return {"as_of": best["as_of"], "run_id": best["run_id"], "combos": meta.get("counts", {}).get("combos"),
                "complete_through": meta.get("complete_through"), "data_max": meta.get("data_range", {}).get("max_t_dat"),
                "created_at": meta.get("created_at_utc"), "count": count}

    def current_model(self) -> dict | None:
        """가장 늦은 기준일에 선택되는 모델(as_of 최신, 같으면 run_id 최신)의 요약"""
        models = self.installed_models()
        best = max(models, key=lambda m: (m["as_of"], m["run_id"]), default=None)
        return self._summary(best, len(models))

    def _serving(self, models: list[dict] | None = None) -> dict | None:
        """에이전트가 실제로 쓰는 모델: SERVING_AS_OF 이하 as_of 중 최신(같으면 run_id 최신)"""
        models = self.installed_models() if models is None else models
        usable = [m for m in models if m["as_of"] <= SERVING_AS_OF]
        return max(usable, key=lambda m: (m["as_of"], m["run_id"]), default=None)

    def serving_model(self) -> dict | None:
        models = self.installed_models()
        serving = self._serving(models)
        out = self._summary(serving, len(models))
        if out:
            # 되돌리기는 지금 모델을 빼도 쓸 수 있는 다른 모델이 남을 때만 가능합니다.
            out["can_rollback"] = self._serving([m for m in models if m is not serving]) is not None
        return out

    def status(self) -> dict:
        with self._lock:
            job = dict(self._job) if self._job else None
        if job:
            if job.get("status") in {"training", "checking"}:
                job["elapsed_seconds"] = round(time.time() - job["started_ts"], 1)
            job["log_tail"] = _tail(self.runtime / "jobs" / job["job_id"] / "log.txt")
            job.pop("pid", None)
            if job.get("status") == "ready":
                # 적용해도 에이전트가 쓰지 않는 모델이면 화면에서 경고합니다.
                serving = self._serving()
                cand = (job["as_of"], job.get("run_id") or "")
                job["serves_agents"] = job["as_of"] <= SERVING_AS_OF and (
                    serving is None or cand > (serving["as_of"], serving["run_id"]))
        return {"available": self.available, "current_model": self.current_model(),
                "serving_model": self.serving_model(), "serving_as_of": SERVING_AS_OF, "job": job}

    def history(self) -> list[dict]:
        try:
            lines = (self.runtime / "history.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in reversed(lines[-HISTORY_LIMIT:]):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    # ---------------- 시작 ----------------
    def start(self, body: dict) -> dict:
        if not self.available:
            raise RetrainError(503, "이 프로젝트에는 구매 예측 모델 코드(modeling/purchase_model)가 없습니다.")
        if not isinstance(body, dict):
            raise RetrainError(400, "학습 요청을 확인해 주세요.")
        as_of = _iso_date(body.get("as_of"), "기준일")
        complete = body.get("complete_through") or None
        if complete is not None:
            complete = _iso_date(complete, "데이터 완료일")
            if complete >= as_of:
                raise RetrainError(422, "데이터 완료일은 기준일보다 앞선 날짜여야 합니다.")
        with self._lock:
            if self._job and self._job.get("status") in ACTIVE:
                raise RetrainError(409, "이미 진행 중이거나 적용을 기다리는 재학습이 있습니다. 먼저 끝내거나 적용·버리기를 해 주세요.")
            same = [m for m in self.installed_models() if m["as_of"] == as_of]
            if same and body.get("confirm_duplicate") is not True:
                raise RetrainError(409, f"기준일 {as_of} 모델이 이미 {len(same)}개 있습니다. 학습하고 적용하면 새 모델이 대신 쓰입니다.",
                                   duplicate=True, existing=[m["run_id"] for m in same])
            job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
            self._job = {"job_id": job_id, "status": "training", "as_of": as_of, "complete_through": complete,
                         "duplicate": bool(same), "started_at": _now(), "started_ts": time.time(), "finished_at": None,
                         "train": None, "check": None, "candidate": None, "error": None}
            self._save()
            self._thread = threading.Thread(target=self._run, args=(job_id,), name=f"retrain-{job_id}", daemon=True)
            self._thread.start()
            return dict(self._job)

    # ---------------- 실행(백그라운드 스레드) ----------------
    def _job_dir(self, job_id: str) -> Path:
        return self.runtime / "jobs" / job_id

    def _spawn(self, args: list[str], log: Path, timeout: int) -> int:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with log.open("ab") as out:
            out.write(f"\n$ {' '.join(args[1:])}\n".encode("utf-8"))
            out.flush()
            proc = subprocess.Popen([self.python, *args[1:]], cwd=self.project, stdout=out, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env, creationflags=flags)
            with self._lock:
                if self._job:
                    # 재시작 후 남은 프로세스를 정리할 수 있도록 상태 파일에도 남깁니다.
                    self._job["pid"] = proc.pid
                    self._save()
            try:
                return proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                out.write(f"\n제한 시간 {timeout}초를 넘어 중단했습니다.\n".encode("utf-8"))
                return -1

    def _run(self, job_id: str) -> None:
        try:
            self._run_steps(job_id)
        except Exception as exc:  # 예상하지 못한 오류도 작업 상태에 남깁니다.
            self._finish("failed", error=f"{type(exc).__name__}: {exc}", cleanup=True)

    def _run_steps(self, job_id: str) -> None:
        job = self._job
        jdir = self._job_dir(job_id)
        jdir.mkdir(parents=True, exist_ok=True)
        log = jdir / "log.txt"
        out_root = self.staging / job_id
        report = jdir / "train_report.json"
        cmd = ["python", "-m", "modeling.purchase_model.train", "--as-of", job["as_of"], "--out", str(out_root), "--report", str(report)]
        if job["complete_through"]:
            cmd += ["--complete-through", job["complete_through"]]
        if self.parquet_dir:
            cmd += ["--parquet-dir", str(self.parquet_dir)]
        code = self._spawn(cmd, log, TRAIN_TIMEOUT)
        train = _read_json(report)
        with self._lock:
            self._job["train"] = train
            self._save()
        if code != 0 or not train or train.get("status") != "ok":
            reason = (train or {}).get("reason") or f"학습 명령이 종료 코드 {code}로 끝났습니다. 로그를 확인해 주세요."
            return self._finish("failed", error=f"학습 실패: {(train or {}).get('error_type', '')} {reason}".strip(), cleanup=True)

        candidate = Path(train["path"]).resolve()
        if not candidate.is_relative_to(out_root.resolve()):
            return self._finish("failed", error="학습 결과가 예상한 대기 폴더 밖에 저장됐습니다.", cleanup=True)
        with self._lock:
            self._job.update(status="checking", candidate=str(candidate), run_id=train.get("run_id"))
            self._save()

        check_out = jdir / "check.json"
        cmd = ["python", "-m", "admin_dashboard.retrain_check", "--candidate", str(candidate),
               "--models-dir", str(self.models_dir), "--out", str(check_out)]
        if self.parquet_dir:
            cmd += ["--parquet-dir", str(self.parquet_dir)]
        if self.check_top_k:
            cmd += ["--top-k", str(self.check_top_k)]
        code = self._spawn(cmd, log, CHECK_TIMEOUT)
        check = _read_json(check_out) or {"can_apply": False, "items": [
            {"key": "check", "title": "자동 점검 실행", "level": "block", "detail": f"점검 결과가 없습니다(종료 코드 {code}). 로그를 확인해 주세요."}]}
        if code != 0:
            check["can_apply"] = False
        with self._lock:
            self._job.update(status="ready", check=check, checked_at=_now())
            self._job.pop("pid", None)
            self._save()

    # ---------------- 적용·버리기 ----------------
    def _require(self, body, statuses: set[str]) -> dict:
        if not isinstance(body, dict) or not self._job or body.get("job_id") != self._job.get("job_id"):
            raise RetrainError(409, "화면의 작업 정보가 최신이 아닙니다. 새로고침 후 다시 시도해 주세요.")
        if self._job.get("status") not in statuses:
            raise RetrainError(409, "지금 상태에서는 할 수 없는 작업입니다.")
        return self._job

    def apply(self, body) -> dict:
        with self._lock:
            job = self._require(body, {"ready"})
            if not (job.get("check") or {}).get("can_apply"):
                raise RetrainError(409, "자동 점검에서 적용 불가 항목이 있어 적용할 수 없습니다. 버리기만 할 수 있습니다.")
            src = Path(job["candidate"]).resolve()
            if not src.is_relative_to((self.staging / job["job_id"]).resolve()) or not src.is_dir():
                raise RetrainError(409, "대기 모델 폴더를 찾지 못했습니다.")
            dest = self.models_dir / src.parent.name / src.name
            if dest.exists():
                raise RetrainError(409, f"같은 이름의 모델 폴더가 이미 있습니다: {dest.parent.name}/{dest.name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.rename(src, dest)
            job["applied_path"] = str(dest)
            self._finish("applied", cleanup=True, locked=True)
            return dict(self._job)

    def discard(self, body) -> dict:
        with self._lock:
            self._require(body, {"ready"})
            self._finish("discarded", cleanup=True, locked=True)
            return dict(self._job)

    def rollback(self, body) -> dict:
        """에이전트가 쓰는 최신 모델을 보관 폴더(models/.retired)로 옮겨 이전 모델로 되돌립니다. 지우지 않습니다."""
        with self._lock:
            if self._job and self._job.get("status") in ACTIVE:
                raise RetrainError(409, "재학습 작업이 진행 중이거나 적용을 기다리고 있어 되돌릴 수 없습니다.")
            models = self.installed_models()
            serving = self._serving(models)
            if not isinstance(body, dict) or not serving or body.get("run_id") != serving["run_id"]:
                raise RetrainError(409, "화면의 모델 정보가 최신이 아닙니다. 새로고침 후 다시 시도해 주세요.")
            previous = self._serving([m for m in models if m is not serving])
            if previous is None:
                raise RetrainError(409, "되돌릴 이전 모델이 없습니다.")
            dest = self.models_dir / ".retired" / f"as_of={serving['as_of']}" / serving["run_id"]
            if dest.exists():
                raise RetrainError(409, "보관 폴더에 같은 이름의 모델이 이미 있습니다.")
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(serving["path"], dest)
            except OSError as exc:
                raise RetrainError(503, f"모델 폴더를 옮기지 못했습니다({type(exc).__name__}). 파일을 연 프로그램을 닫고 다시 시도해 주세요.") from None
            try:
                serving["path"].parent.rmdir()  # 기준일 폴더가 비었을 때만 지워집니다.
            except OSError:
                pass
            now = _now()
            record = {"job_id": "rollback_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), "status": "rolled_back",
                      "as_of": serving["as_of"], "run_id": serving["run_id"], "started_at": now, "finished_at": now,
                      "error": None, "restored": f"{previous['as_of']} · {previous['run_id']}", "retired_path": str(dest)}
            path = self.runtime / "history.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return {"retired": f"{serving['as_of']} · {serving['run_id']}", "restored": record["restored"]}

    def dismiss(self, body) -> dict:
        """실패한 작업 표시를 닫습니다(상태만 정리, 이력은 이미 남아 있음)."""
        with self._lock:
            self._require(body, {"failed", "applied", "discarded"})
            self._job = None
            self._save()
            return {}

    # ---------------- 내부 ----------------
    def _save(self) -> None:
        path = self.runtime / "state.json"
        if self._job is None:
            path.unlink(missing_ok=True)
        else:
            _write_json(path, self._job)

    def _finish(self, status: str, error: str | None = None, cleanup: bool = False, locked: bool = False) -> None:
        if not locked:
            with self._lock:
                return self._finish(status, error, cleanup, locked=True)
        job = self._job
        job.update(status=status, finished_at=_now())
        job.pop("pid", None)
        if error:
            job["error"] = error
        if cleanup:
            root = (self.staging / job["job_id"]).resolve()
            if root.is_relative_to(self.staging.resolve()) and root != self.staging.resolve():
                shutil.rmtree(root, ignore_errors=True)
            try:
                self.staging.rmdir()  # 비어 있을 때만 지워집니다(다른 작업 폴더가 있으면 그대로 둠).
            except OSError:
                pass
        self._save()
        self._append_history(job)

    def _append_history(self, job: dict) -> None:
        check = job.get("check") or {}
        warn = sum(i.get("level") == "warn" for i in check.get("items", []))
        record = {"job_id": job["job_id"], "status": job["status"], "as_of": job["as_of"],
                  "complete_through": job.get("complete_through"), "run_id": job.get("run_id"),
                  "started_at": job["started_at"], "finished_at": job["finished_at"],
                  "train_seconds": (job.get("train") or {}).get("seconds"),
                  "combos": (job.get("train") or {}).get("combos"), "warnings": warn, "error": job.get("error")}
        path = self.runtime / "history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
