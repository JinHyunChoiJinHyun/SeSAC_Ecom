"""대시보드 '외부 데이터' 화면: 수집기 실행 상태, 데이터 최신 현황, 미리보기, 수집기별 수동 실행.

- 수집 코드는 프로젝트의 external_data/ 에 있고, 결과는 external_data/data/ 에 CSV로 쌓입니다.
- 예약 실행(Windows 작업 스케줄러)의 기록은 logs/external_data/daily_*.log, weekly_*.log 에서 읽습니다.
- 수동 실행은 수집기마다 따로 하나씩만 돌고, 로그는 logs/external_data/manual_<수집기>_<시각>.log 에 남습니다.
- 데이터 파일은 읽기만 합니다. 수집기를 실행할 때만 파일이 바뀝니다.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
HISTORY_LIMIT = 100
LOG_FILES_TO_SCAN = 40          # 종류(daily·weekly)별로 읽을 최근 예약 로그 파일 수
NEXT_RUN_CACHE_SECONDS = 300    # 작업 스케줄러 조회 결과를 다시 쓰는 시간

COLLECTORS = {
    "naver": {"title": "네이버 쇼핑 키워드 추이", "script": "naver_keyword_trend.py", "schedule": "매주 월 10:00",
              "task": "external_weekly", "stale_days": 8, "timeout": 15 * 60, "minutes": "약 1분",
              "aliases": ["naver_shopping_daily.py"]},
    "youtube_candidates": {"title": "유튜브 키워드 후보", "script": "youtube_keyword_candidates.py", "schedule": "매주 월 10:00",
                           "task": "external_weekly", "stale_days": 8, "timeout": 15 * 60, "minutes": "약 1분",
                           "aliases": ["youtube_keywords.py"]},
    "youtube_views": {"title": "유튜브 키워드 총조회수", "script": "youtube_keyword_views.py", "schedule": "매일 09:00",
                      "task": "youtube_views_daily", "stale_days": 1, "timeout": 15 * 60, "minutes": "약 30초",
                      "aliases": []},
    "musinsa": {"title": "무신사 랭킹·리뷰", "script": "musinsa_crawler.py", "schedule": "매주 월 10:00",
                "task": "external_weekly", "stale_days": 8, "timeout": 4 * 3600, "minutes": "1시간 이상",
                "aliases": ["crawling.py", "run_crawling_timed.py"]},
}
SCRIPT_KEY = {name: key for key, c in COLLECTORS.items() for name in [c["script"], *c["aliases"]]}
TASKS = ["youtube_views_daily", "external_weekly"]
DOWNLOADS = {"naver", "youtube_views", "youtube_candidates", "musinsa_ranking", "musinsa_product_info", "musinsa_review"}
LINE = re.compile(r"^===== \[(.*?)\]\s*(.*?)\s*$")
STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})\D+?(\d{1,2}):(\d{2}):(\d{2})")


class ExternalError(Exception):
    """HTTP 상태 코드와 화면에 보여 줄 메시지를 함께 담습니다."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _now() -> datetime:
    return datetime.now(KST)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def _stamp(text: str) -> datetime | None:
    """'2026-11-24 18:38:19.87' 같은 bat 로그 시각(%date% %time%)을 한국 시간으로 읽습니다."""
    m = STAMP.search(text)
    if not m:
        return None
    y, mo, d, h, mi, s = map(int, m.groups())
    return datetime(y, mo, d, h, mi, s, tzinfo=KST)


def _tail(path: Path, lines: int = 20) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16384))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def _num(value, cast=float):
    """CSV 숫자 칸을 읽습니다. 비었거나 숫자가 아니면 None(그래프에서는 빈 점, 표에서는 —)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number) if cast is int else number


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


class ExternalDataManager:
    def __init__(self, project, python=None):
        self.project = Path(project).resolve()
        self.root = self.project / "external_data"
        self.data = self.root / "data"
        self.logs = self.project / "logs" / "external_data"
        self.state_file = Path(__file__).resolve().parent / "runtime" / "external" / "manual_runs.json"
        self.python = python or sys.executable
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._history: list[dict] = self._load_history()
        self._cache: dict = {}
        self._next = (0.0, {})

    # =========================================================
    # 수동 실행 기록 저장
    # =========================================================
    def _load_history(self) -> list[dict]:
        try:
            items = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        for item in items:
            # 대시보드가 실행 중에 꺼졌다면 결과를 알 수 없습니다.
            if item.get("status") == "running":
                item.update(status="interrupted", error="대시보드가 실행 중에 종료되어 결과를 확인하지 못했습니다.")
        return items[-HISTORY_LIMIT:]

    def _save_history(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_name(self.state_file.name + ".tmp")
        tmp.write_text(json.dumps(self._history[-HISTORY_LIMIT:], ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_file)

    # =========================================================
    # 예약 실행 로그(daily_*.log, weekly_*.log) 해석
    # =========================================================
    def scheduled_runs(self) -> list[dict]:
        """예약 로그에서 수집기별 실행(시작·종료·종료 코드)을 꺼냅니다. 진행 중이면 end가 없습니다."""
        # 종류별로 따로 자릅니다. 한꺼번에 이름순으로 자르면 weekly_*가 쌓일수록 daily_*가 밀려납니다.
        files = [p for kind in ("daily", "weekly") for p in sorted(self.logs.glob(f"{kind}_*.log"), key=lambda p: p.name)[-LOG_FILES_TO_SCAN:]]
        runs = []
        for path in files:
            current = None
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                m = LINE.match(line)
                if not m:
                    continue
                when, rest = _stamp(m.group(1)), m.group(2)
                code = re.search(r"exit code (-?\d+)", rest)
                if code:
                    if current:
                        current.update(end=when, exit_code=int(code.group(1)))
                        current = None
                    continue
                key = SCRIPT_KEY.get(rest.split()[0]) if rest else None
                if key:
                    current = {"collector": key, "source": "예약", "start": when, "end": None, "exit_code": None, "log": path.name}
                    runs.append(current)
        # 끝나지 않은 실행이 너무 오래됐으면(창을 닫는 등) 진행 중으로 보지 않습니다.
        now = _now()
        for run in runs:
            if run["end"] is None and run["start"]:
                if now - run["start"] > timedelta(seconds=COLLECTORS[run["collector"]]["timeout"]):
                    run["stale"] = True
        return runs

    # =========================================================
    # 작업 스케줄러의 다음 실행 시각 (Windows 전용, 1분 캐시)
    # =========================================================
    def next_runs(self) -> dict:
        if time.monotonic() - self._next[0] < NEXT_RUN_CACHE_SECONDS:
            return self._next[1]
        result = {}
        if os.name == "nt":
            names = ",".join(f"'{t}'" for t in TASKS)
            script = ("$r=@{}; foreach($t in @(" + names + ")){ try { $i=Get-ScheduledTaskInfo -TaskPath '\\SeSAC_Ecom\\' -TaskName $t -ErrorAction Stop;"
                      " $n=$null; if($i.NextRunTime){ $n=$i.NextRunTime.ToString('o') }; $r[$t]=@{next=$n} } catch { $r[$t]=$null } }; $r | ConvertTo-Json -Compress")
            try:
                out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, timeout=15,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                result = json.loads(out.stdout.decode("utf-8", errors="replace") or "{}") or {}
            except (OSError, ValueError, subprocess.SubprocessError):
                result = {}
        self._next = (time.monotonic(), result)
        return result

    # =========================================================
    # 데이터 최신 현황 (파일이 바뀔 때만 다시 계산)
    # =========================================================
    def _cached(self, key, paths: list[Path], compute):
        stamp = tuple((str(p), p.stat().st_mtime_ns) for p in paths if p.exists())
        hit = self._cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
        value = compute()
        self._cache[key] = (stamp, value)
        return value

    def _latest_candidates(self) -> Path | None:
        files = sorted((self.data / "youtube" / "keyword_candidates").glob("keyword_candidates_*.csv"))
        return files[-1] if files else None

    def _latest_musinsa(self) -> Path | None:
        dirs = sorted(d for d in (self.data / "musinsa").glob("*") if d.is_dir() and re.fullmatch(r"\d{8}", d.name))
        return dirs[-1] if dirs else None

    def data_summary(self, key: str) -> dict:
        """수집기별 최신 데이터: 수집일(collected), 설명 문구(facts), 파일 존재 여부."""
        if key == "naver":
            path = self.data / "naver" / "keyword_trend.csv"
            if not path.exists():
                return {"exists": False}

            def compute():
                rows = _read_csv(path)
                cols = list(rows[0].keys()) if rows else []
                collected = datetime.fromtimestamp(path.stat().st_mtime, KST).date().isoformat()
                return {"exists": True, "collected": collected,
                        "facts": [["데이터 기간", f"{rows[0]['날짜']} ~ {rows[-1]['날짜']}" if rows else "—"],
                                  ["키워드", f"{max(0, len(cols) - 1)}개"], ["행 수(일)", f"{len(rows):,}"]]}
            return self._cached(key, [path], compute)
        if key == "youtube_candidates":
            path = self._latest_candidates()
            if not path:
                return {"exists": False}

            def compute():
                rows = _read_csv(path)
                stamp = path.stem.rsplit("_", 1)[-1]
                return {"exists": True, "collected": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}",
                        "facts": [["키워드 후보", f"{len(rows):,}개"], ["파일", path.name],
                                  ["누적 파일", f"{len(list(path.parent.glob('keyword_candidates_*.csv')))}개"]]}
            return self._cached(key, [path], compute)
        if key == "youtube_views":
            path = self.data / "youtube" / "keyword_views" / "keyword_views.csv"
            if not path.exists():
                return {"exists": False}

            def compute():
                rows = _read_csv(path)
                dates = sorted({r["수집일"] for r in rows})
                last = [r for r in rows if r["수집일"] == dates[-1]] if dates else []
                return {"exists": True, "collected": dates[-1] if dates else None,
                        "facts": [["누적 수집일", f"{len(dates)}일"], ["최근 수집 키워드", f"{len(last)}개"],
                                  # 키워드끼리 같은 영상이 겹쳐 세지므로 합계 대신 1위 키워드를 보여 줍니다.
                                  ["최근 1위 키워드", "—" if not last else (lambda top: f"{top['키워드']} {_num(top['총조회수'], int) or 0:,}")(
                                      max(last, key=lambda r: _num(r['총조회수'], int) or 0))]]}
            return self._cached(key, [path], compute)
        if key == "musinsa":
            folder = self._latest_musinsa()
            if not folder:
                return {"exists": False}
            files = [folder / f"{k}.csv" for k in ("ranking", "product_info", "review")]

            def compute():
                counts = {f.stem: (len(_read_csv(f)) if f.exists() else 0) for f in files}
                n = folder.name
                return {"exists": True, "collected": f"{n[:4]}-{n[4:6]}-{n[6:]}",
                        "facts": [["랭킹 상품", f"{counts['ranking']:,}개"], ["리뷰", f"{counts['review']:,}건"],
                                  ["누적 수집", f"{len([d for d in (self.data / 'musinsa').glob('*') if d.is_dir()])}주"]]}
            return self._cached(key, files, compute)
        raise ExternalError(404, "수집기를 찾지 못했습니다.")

    # =========================================================
    # 화면용 상태 묶음
    # =========================================================
    def status(self) -> dict:
        scheduled = self.scheduled_runs()
        nexts = self.next_runs()
        today = _now().date()
        items = []
        with self._lock:
            jobs = {k: dict(v) for k, v in self._jobs.items()}
            history = list(self._history)
        for key, c in COLLECTORS.items():
            runs = [r for r in scheduled if r["collector"] == key]
            manual = [h for h in history if h["collector"] == key]
            candidates = [{"source": "예약", "started_at": _iso(r["start"]), "finished_at": _iso(r["end"]),
                           "status": "running" if r["end"] is None and not r.get("stale") else
                                     "unknown" if r["end"] is None else ("succeeded" if r["exit_code"] == 0 else "failed"),
                           "exit_code": r["exit_code"], "log": r["log"]} for r in runs]
            candidates += [{"source": "수동", **{k: h.get(k) for k in ("started_at", "finished_at", "status", "exit_code", "log", "error")}} for h in manual]
            last = max(candidates, key=lambda r: r["started_at"] or "", default=None)
            job = jobs.get(key)
            if job and job.get("log"):
                job["log_tail"] = _tail(self.logs / job["log"])
            elif last and last.get("status") == "failed" and last.get("log"):
                last["log_tail"] = _tail(self.logs / last["log"], 8)
            try:
                data = self.data_summary(key)
            except (OSError, ValueError, KeyError, IndexError):
                data = {"exists": False, "error": "데이터 파일을 읽지 못했습니다."}
            days_old = (today - date.fromisoformat(data["collected"])).days if data.get("collected") else None
            items.append({
                "key": key, "title": c["title"], "script": c["script"], "schedule": c["schedule"], "duration": c["minutes"],
                "next_run": (nexts.get(c["task"]) or {}).get("next"), "last_run": last, "job": job,
                "running": bool(job and job["status"] == "running") or any(r["status"] == "running" for r in candidates if r["source"] == "예약"),
                "data": data, "days_old": days_old,
                "stale": days_old is None or days_old > c["stale_days"], "stale_days": c["stale_days"],
            })
        return {"today": today.isoformat(), "collectors": items,
                "scheduler": "available" if nexts else ("unavailable" if os.name == "nt" else "unsupported")}

    # =========================================================
    # 수동 실행 (수집기마다 하나씩)
    # =========================================================
    def start(self, body: dict) -> dict:
        key = (body or {}).get("collector")
        if key not in COLLECTORS:
            raise ExternalError(422, "수집기를 확인해 주세요.")
        c = COLLECTORS[key]
        script = self.root / c["script"]
        if not script.is_file():
            raise ExternalError(404, f"수집 코드가 없습니다: external_data/{c['script']}")
        if any(r["collector"] == key and r["end"] is None and not r.get("stale") for r in self.scheduled_runs()):
            raise ExternalError(409, "예약 실행이 진행 중입니다. 끝난 뒤 다시 시도해 주세요.")
        with self._lock:
            if self._jobs.get(key, {}).get("status") == "running":
                raise ExternalError(409, "이미 실행 중입니다.")
            started = _now()
            job = {"collector": key, "status": "running", "started_at": _iso(started), "finished_at": None, "exit_code": None,
                   "log": f"manual_{key}_{started:%Y%m%d_%H%M%S}.log", "error": None}
            self._jobs[key] = job
            self._history.append(dict(job))
            try:
                self._save_history()
            except OSError:
                # 기록을 못 남기면 시작하지 않습니다. 상태를 되돌려 '실행 중'으로 굳지 않게 합니다.
                self._jobs.pop(key, None)
                self._history.pop()
                raise ExternalError(503, "실행 기록을 저장하지 못해 수집을 시작하지 않았습니다. 디스크 공간·폴더 권한을 확인한 뒤 다시 시도해 주세요.") from None
        threading.Thread(target=self._run, args=(key,), daemon=True).start()
        return {"started": True, "job": job}

    def _run(self, key: str) -> None:
        c = COLLECTORS[key]
        job = self._jobs[key]
        self.logs.mkdir(parents=True, exist_ok=True)
        log = self.logs / job["log"]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        code, error = None, None
        try:
            with log.open("w", encoding="utf-8") as out:
                out.write(f"===== [{_now():%Y-%m-%d %H:%M:%S}] {c['script']} (대시보드 수동 실행)\n")
                out.flush()
                proc = subprocess.Popen([self.python, "-X", "utf8", c["script"]], cwd=self.root, stdout=out, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    code = proc.wait(timeout=c["timeout"])
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    error = f"{c['timeout'] // 60}분 안에 끝나지 않아 중단했습니다."
            with log.open("a", encoding="utf-8") as out:
                out.write(f"===== [{_now():%Y-%m-%d %H:%M:%S}] exit code {code}\n")
        except OSError as exc:
            error = f"실행하지 못했습니다: {exc.__class__.__name__}"
        with self._lock:
            job.update(status="succeeded" if code == 0 and not error else "failed", finished_at=_iso(_now()), exit_code=code, error=error)
            for item in reversed(self._history):
                if item["collector"] == key and item["started_at"] == job["started_at"]:
                    item.update(job)
                    break
            try:
                self._save_history()
            except OSError:
                pass

    # =========================================================
    # 그래프·미리보기 데이터
    # =========================================================
    def naver_series(self) -> dict:
        path = self.data / "naver" / "keyword_trend.csv"
        if not path.exists():
            return {"dates": [], "keywords": [], "series": {}}

        def compute():
            rows = _read_csv(path)
            keywords = [k for k in (rows[0].keys() if rows else []) if k != "날짜"]
            return {"dates": [r["날짜"] for r in rows], "keywords": keywords,
                    "series": {k: [_num(r[k]) for r in rows] for k in keywords}}
        return self._cached("naver_series", [path], compute)

    def views_series(self) -> dict:
        path = self.data / "youtube" / "keyword_views" / "keyword_views.csv"
        if not path.exists():
            return {"dates": [], "keywords": [], "series": {}, "videos": {}}

        def compute():
            rows = _read_csv(path)
            dates = sorted({r["수집일"] for r in rows})
            keywords = list(dict.fromkeys(r["키워드"] for r in rows))
            cell = {(r["수집일"], r["키워드"]): r for r in rows}
            pick = lambda d, k, col: _num(cell[(d, k)][col], int) if (d, k) in cell else None
            return {"dates": dates, "keywords": keywords,
                    "series": {k: [pick(d, k, "총조회수") for d in dates] for k in keywords},
                    "videos": {k: [pick(d, k, "집계 영상 수") for d in dates] for k in keywords}}
        return self._cached("views_series", [path], compute)

    def candidates_top(self, limit: int = 20) -> dict:
        path = self._latest_candidates()
        if not path:
            return {"date": None, "total": 0, "items": []}
        rows = self._cached("candidates_rows", [path], lambda: _read_csv(path))
        items = [{"category": r["상품 카테고리"], "keyword": r["키워드"], "channels": _num(r["등장 채널 수"], int),
                  "link": (r["근거 링크"].split(" | ") or [""])[0]} for r in rows[:limit]]
        return {"date": rows[0].get("수집일") if rows else None, "file": path.name, "total": len(rows), "items": items}

    def musinsa_ranking(self, category: str = "", limit: int = 20) -> dict:
        folder = self._latest_musinsa()
        if not folder:
            return {"date": None, "categories": [], "items": []}
        ranking, info = folder / "ranking.csv", folder / "product_info.csv"

        def compute():
            ranks = _read_csv(ranking) if ranking.exists() else []
            infos = {r["item_id"]: r for r in _read_csv(info)} if info.exists() else {}
            return ranks, infos
        ranks, infos = self._cached("musinsa_rows", [ranking, info], compute)
        categories = list(dict.fromkeys(r["category"] for r in ranks))
        category = category if category in categories else (categories[0] if categories else "")
        # 순위를 읽을 수 없는 상품은 맨 아래로 보냅니다.
        rank = lambda r: _num(r["ranking"], int)
        rows = sorted((r for r in ranks if r["category"] == category), key=lambda r: (rank(r) is None, rank(r) or 0))[:limit]
        items = [{"ranking": rank(r), "brand": r["brand"], "name": r["name"], "price": _num(r["price"]),
                  "discount_rate": _num(r["discount_rate"]), "product_gender": infos.get(r["item_id"], {}).get("product_gender") or "—",
                  "rating": _num(infos.get(r["item_id"], {}).get("rating")), "review_count": _num(infos.get(r["item_id"], {}).get("review_count")),
                  "url": f"https://www.musinsa.com/products/{r['item_id']}"} for r in rows]
        return {"date": f"{folder.name[:4]}-{folder.name[4:6]}-{folder.name[6:]}", "categories": categories, "category": category, "items": items}

    def download_path(self, dataset: str) -> Path:
        """내려받을 수 있는 CSV는 정해 둔 것만 고릅니다(경로를 입력으로 받지 않음)."""
        if dataset not in DOWNLOADS:
            raise ExternalError(404, "파일을 찾지 못했습니다.")
        if dataset == "naver":
            path = self.data / "naver" / "keyword_trend.csv"
        elif dataset == "youtube_views":
            path = self.data / "youtube" / "keyword_views" / "keyword_views.csv"
        elif dataset == "youtube_candidates":
            path = self._latest_candidates()
        else:
            folder = self._latest_musinsa()
            path = folder / f"{dataset.removeprefix('musinsa_')}.csv" if folder else None
        if not path or not path.is_file():
            raise ExternalError(404, "파일이 아직 없습니다.")
        return path
