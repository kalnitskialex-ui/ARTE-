import os
from pathlib import Path

import pytest

from arte.config import Settings
from arte.storage import db

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def settings(tmp_path) -> Settings:
    # ARTE_TEST_DATABASE_URL=postgresql+asyncpg://... — прогнать тесты на PostgreSQL.
    url = os.environ.get("ARTE_TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path}/test.db"
    return Settings(database_url=url, per_host_delay=0,
                    respect_robots_txt=False, max_retries=1, adaptive_storage=str(tmp_path / "ad.db"),
                    _env_file=None)


@pytest.fixture
async def database(settings):
    db.init_engine(settings.database_url)
    async with db._engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)
    await db.create_all()
    yield db.sessionmaker()
    await db._engine.dispose()
