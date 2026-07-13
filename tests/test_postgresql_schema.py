import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from eslee_bot.database import Database
from eslee_bot.database.models import Base


def test_all_metadata_compiles_for_postgresql() -> None:
    dialect = postgresql.dialect()

    table_ddl = [
        str(CreateTable(table).compile(dialect=dialect)) for table in Base.metadata.sorted_tables
    ]
    index_ddl = [
        str(CreateIndex(index).compile(dialect=dialect))
        for table in Base.metadata.sorted_tables
        for index in table.indexes
    ]

    assert len(table_ddl) == 4
    assert all("CREATE TABLE" in statement for statement in table_ddl)
    assert index_ddl
    assert all("INDEX" in statement for statement in index_ddl)


@pytest.mark.asyncio
async def test_database_selects_asyncpg_for_standard_postgresql_url() -> None:
    database = Database("postgresql://user:password@database.example:5432/eslee")
    try:
        assert database.url == (
            "postgresql+asyncpg://user:password@database.example:5432/eslee"
        )
        assert database.engine.dialect.name == "postgresql"
        assert database.engine.dialect.driver == "asyncpg"
    finally:
        await database.close()
