""" 추후 공부 필요
Alembic 마이그레이션 실행 환경 설정 (env.py)

- alembic 명령(upgrade, downgrade, revision --autogenerate 등)을 실행할 때마다 이 파일이 실행된다.
- DB 접속 정보는 alembic.ini가 아니라 앱의 get_engine()에서 가져온다.
  (접속 정보를 한 곳에서만 관리하기 위함)
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from database.operations.base import Base
from database.operations.connection import get_engine


# ORM 모델 모듈을 import해서 Base.metadata에 테이블을 등록한다.
# 코드에서 직접 쓰지 않아 "미사용 import"로 보이지만 꼭 필요하다.
# 이 줄이 지워지면 autogenerate가 모든 테이블을 drop하는 마이그레이션을 만든다.
# → noqa로 린터/IDE 자동 정리 대상에서 제외
import database.operations.orm # noqa: F401

# Alembic 설정 객체: alembic.ini 값에 접근할 때 사용
config = context.config

# alembic.ini의 [loggers] 설정으로 로깅 구성
# disable_existing_loggers=False:
#   위에서 앱 모듈을 먼저 import했기 때문에 앱 로거가 이미 만들어져 있다.
#   기본값(True)이면 그 로거들이 전부 꺼지므로 False로 둬서 앱 로그도 보이게 한다.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False) # 로그 켜기

# autogenerate가 비교 기준으로 삼을 메타데이터
# (ORM 모델 정의 ↔ 실제 DB 스키마를 비교해서 변경분을 찾아낸다)
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """
    오프라인 모드: DB에 접속하지 않고 마이그레이션 SQL 스크립트만 생성한다.
    실행 예) alembic upgrade head --sql > migration.sql
    → DBA 검토용이나 운영 DB에 직접 반영할 SQL을 뽑을 때 사용
    """
    # 기존 템플릿 방식: alembic.ini의 sqlalchemy.url 사용
    # url = config.get_main_option("sqlalchemy.url")

    # 앱 엔진에서 URL을 가져온다.
    # SQLAlchemy 2.x에서 str(url)은 비밀번호를 ***로 가리기 때문에
    # 실제 비밀번호를 그대로 쓰려면 hide_password=False로 렌더링해야 한다.
    url = get_engine().url.render_as_string(hide_password=False)

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True, # 바인드 파라미터(:param) 대신 실제 값을 SQL에 직접 넣는다
        dialect_opts={"paramstyle": "named"}, # 파라미터 표기 방식 지정
        compare_type=True # 컬럼 타입 변경도 감지 (예: VARCHAR(50) → VARCHAR(100))
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    온라인 모드: 실제 DB에 접속해서 마이그레이션을 실행한다.
    실행 예) alembic upgrade head
    """
    # NullPool: 커넥션 풀을 쓰지 않는다.
    # 마이그레이션은 한 번 실행하고 끝나는 작업이라 풀을 유지할 필요가 없다.
    engine = get_engine(poolclass=pool.NullPool)

    # 기존 템플릿 방식: alembic.ini의 [alembic] 섹션 설정으로 엔진 생성
    # connectable = engine_from_config(
    #     config.get_section(config.config_ini_section, {}),
    #     prefix="sqlalchemy.",
    #     poolclass=pool.NullPool,
    # )

    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True
        )

        # 마이그레이션 전체를 하나의 트랜잭션으로 실행한다.
        # 중간에 실패하면 롤백된다. (DDL 트랜잭션을 지원하는 DB 기준, MySQL은 DDL이 자동 커밋됨)
        with context.begin_transaction():
            context.run_migrations()

# alembic 명령에 --sql 옵션이 있으면 오프라인 모드, 없으면 온라인 모드
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
