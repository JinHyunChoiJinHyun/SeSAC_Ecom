# CART SIGNAL develop + 현행 멀티턴 안전 통합 (검토용)

## 중요한 조건
- 현행 ZIP을 기준으로, develop에만 있던 파일을 **기존 경로 그대로** 복원했습니다.
- 공통 수정 파일은 현행 멀티턴 기능을 우선하고 팀원 원본은 merge_reference/develop_versions/에 원본 그대로 보존했습니다.
- 이는 ZIP 두 버전의 파일 결합이지 Git 3-way 병합이 아닙니다. PR 충돌 해소 및 실행 테스트는 로컬 Git에서 따로 진행해야 합니다.
- 기존 DB, .env, 런타임 파일을 덮어쓰지 마세요. 별도 폴더에서 검토 후 필요한 변경만 반영하세요.
- 오래된 팀원 에이전트 구조는 그대로 보존했으며 현재 Orchestrator 경로에 자동 연결되지는 않습니다.

복원 파일: 39개 / 서로 다르게 수정된 파일: 9개 / 패키지 버전 상충: 0개

## develop에만 있어 복원한 파일
- `.coderabbit.yml`
- `.env.example`
- `.gitignore`
- `agent_system/delivery/__init__.py`
- `agent_system/delivery/delivery_agent.py`
- `agent_system/general_analysis/__init__.py`
- `agent_system/general_analysis/general_analysis_agent.py`
- `agent_system/shared_analysis/__init__.py`
- `agent_system/shared_analysis/analysis_router.py`
- `agent_system/shared_analysis/data_agent.py`
- `agent_system/shared_analysis/experiment_agent.py`
- `agent_system/shared_analysis/graph.py`
- `agent_system/shared_analysis/model_agent.py`
- `agent_system/shared_analysis/nodes.py`
- `agent_system/shared_analysis/prep_agent.py`
- `agent_system/shared_analysis/state.py`
- `agent_system/shared_analysis/visualization_agent.py`
- `agent_system/specialized_agents/campaign_timing_agent.py`
- `agent_system/specialized_agents/product_affinity_agent.py`
- `agent_system/specialized_agents/purchase_targeting_agent.py`
- `agent_system/supervisor/__init__.py`
- `agent_system/supervisor/top_supervisor.py`
- `agent_system/tools/__init__.py`
- `agent_system/tools/data_tools.py`
- `agent_system/tools/delivery_tools.py`
- `agent_system/tools/experiment_tools.py`
- `agent_system/tools/model_tools.py`
- `agent_system/tools/my_tools.py`
- `agent_system/tools/prep_tools.py`
- `agent_system/tools/visualization_tools.py`
- `database/sql/analysis_setup/validate/cart_as_interest_signal.sql`
- `database/sql/association_analysis/analytics_association.sql`
- `database/sql/association_analysis/build_mart_basket.sql`
- `database/sql/hourly_forecasting/build_category_hourly_base.sql`
- `database/sql/hourly_forecasting/build_category_hourly_timeseries.sql`
- `database/sql/hourly_forecasting/check_hourly_purchase.sql`
- `db_setup.py`
- `env/db_client.py`
- `tests/test_database/test_connection.py`

## 양쪽이 수정한 파일 (현행 우선, develop 원본 백업)
- `backend/api/dependencies.py`
- `backend/api/endpoints/healthy.py`
- `backend/main.py`
- `common/db_client.py`
- `database/operations/connection.py`
- `main.py`
- `pytest.ini`
- `requirements.txt`
- `slack_bot.py`

## 패키지 버전 상충

## 안전 검증 방법
```bash
cd ~/Desktop/WORKS
git status --short
git fetch origin
git switch Kyung_agent
git merge origin/develop  # 실제 PR 병합은 별도 백업 브랜치에서 먼저 시도 권장
git diff --name-only --diff-filter=U
python -m compileall -q agent_system backend common database
python -m pytest -q tests/test_agents tests/test_backend
```
실제 Git 브랜치에는 ZIP을 통째로 덮어쓰기 전에 팀원 변경 내역을 반드시 검토해야 합니다.
