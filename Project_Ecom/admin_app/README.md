# SeSAC Ecom 관리자 대시보드

## 설치 및 실행

Python 3.10 이상이 필요하며 추가 패키지 설치는 없습니다. `admin_app` 폴더 전체를 `Project_Ecom`의 `slack_bot.py`와 같은 위치에 넣으세요.

```text
Project_Ecom/
├─ admin_app/
│  ├─ run_dashboard.py
│  ├─ service.py
│  ├─ standalone.py
│  ├─ __init__.py
│  ├─ README.md
│  └─ static/
├─ slack_bot.py
└─ logs/
```

VS Code에서 프로젝트 폴더를 열고 터미널에서 실행합니다.

```cmd
python admin_app/run_dashboard.py
```

`run_dashboard.py`를 열고 Python 실행 버튼을 눌러도 됩니다. 실행 위치와 관계없이 파일 위치를 기준으로 프로젝트 경로를 찾습니다. 기존 8000번 서버는 먼저 Ctrl+C로 종료하세요.

1. 로그 경로를 자동으로 선택합니다.
2. 관리자 아이디와 비밀번호를 입력합니다. 아이디는 Enter 입력 시 `admin`입니다. 비밀번호 입력 내용은 화면에 표시되지 않습니다.
3. 서버가 시작되고 브라우저가 열립니다.
4. 브라우저 인증 창에 관리자 계정을 입력합니다.
5. 다른 PC는 같은 네트워크에서 터미널에 표시된 IPv4 주소와 포트로 접속합니다. Windows 방화벽 허용이 필요할 수 있습니다.

`ECOM_ADMIN_USER`, `ECOM_ADMIN_PASSWORD` 환경변수가 이미 설정되어 있으면 재사용합니다. 비밀번호를 코드나 설정 파일에 저장하지 않습니다.

## 옵션

```cmd
python admin_app/run_dashboard.py --local-only
python admin_app/run_dashboard.py --port 8001
python admin_app/run_dashboard.py --project "프로젝트 전체 경로"
python admin_app/run_dashboard.py --no-browser
python admin_app/run_dashboard.py --check
```

- `--local-only`: 내 PC에서만 실행. 완전한 관리자 계정 설정이 없으면 로컬 접속에 한해 로그인 없이 조회합니다.
- `--port`: 사용할 포트 지정.
- `--project`: 자동 탐색 대신 실제 로그가 있는 프로젝트 폴더 지정.
- `--no-browser`: 브라우저 자동 열기 생략.
- `--check`: 서버를 켜지 않고 프로젝트 경로와 필수 파일 존재 여부만 확인.

## 기능 및 해석

운영 현황, 요청 검색·기간·상태 필터, 오류 모니터링, SQL·실행 단계·로그 상세, CSV 내보내기를 제공합니다. 선택적 기존 운영 DB가 있으면 답변·파일·Slack 전달 기록도 읽습니다. 이 배포 묶음은 운영 DB를 새로 작성하거나 Slack 봇 코드를 바꾸지 않습니다.

- 기본 로그 위치: `프로젝트/logs/app.log` 및 `app.log.날짜`.
- 최근 로그 15개, 파일당 최근 약 4MB, 기존 운영 DB 최근 5,000건이 최대 조회 범위입니다. 이 범위 밖의 기록은 통계에 포함되지 않습니다.
- 기존 운영 DB가 있다면 `프로젝트/admin_data/requests.sqlite3`를 읽습니다.
- 로그·DB가 없으면 빈 목록으로 안내하며 가짜 기록으로 대체하지 않습니다.
- `status=success`만으로 분석 완료를 확정하지 않습니다. ‘검증 필요’는 종료 기록은 있지만 분석 성공 근거가 부족한 상태입니다.
- ‘종료 미확인’은 종료 로그가 없는 요청입니다. 실행 중 또는 중단 상태를 로그만으로 확정하지 않습니다.
- 평균 시간은 소요 시간이 기록된 요청을 기준으로 계산하며 기준 건수를 표시합니다.
- 기록에 없는 최종 답변·파일·Slack 전달 결과는 미기록으로 표시합니다.
- `run_id=-`인 공통 로그를 특정 요청에 추측해서 연결하지 않습니다.
- 해당 요청에 기록된 파일 중 프로젝트의 `visualization_output`, `delivery_output` 아래 실제 파일만 제공합니다. PNG/JPG/CSV/JSON/PDF만 허용합니다.
- 자동 갱신은 화면이 열려 있는 동안 10초마다 조회하며 에이전트·LLM을 실행하지 않습니다.

## 교체 시

서버를 종료하고, 이전 `admin_app`을 별도 위치에 백업한 뒤 새 폴더로 교체하세요. 파일을 덮어쓰기만 하면 예전 묶음의 불필요한 파일이 남을 수 있습니다. `Project_Ecom`의 에이전트·로그·데이터는 삭제하지 마세요.

구버전의 선택적 기록 기능을 실제 Slack 봇에서 불러오고 있었다면 해당 사용자 코드와의 호환성을 먼저 확인하세요. 이 정리본에는 선택적 기록 작성 모듈이 포함되어 있지 않습니다.

같은 네트워크의 팀 시연용 기본 서버입니다. 인터넷 공개 운영은 HTTPS와 별도 서버 구성이 필요합니다. 로그·운영 DB·인증정보를 GitHub에 올리지 마세요.
