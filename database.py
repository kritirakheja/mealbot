# database configuration

import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.schema import CreateColumn

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DATA_DIR / 'mealbot.db'}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency to get DB session per request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def sync_schema(bind=None):
    """Create missing tables, then add any columns a table is missing.

    Base.metadata.create_all only creates whole tables that don't exist yet;
    it silently no-ops on a table that already exists, even if the ORM model
    has since grown new columns. In production the SQLite file lives on a
    persistent disk that survives across deploys, so an additive model
    change (e.g. a new `updated_at` column) would otherwise crash every
    startup with "no such column" until someone manually alters the file.
    This covers that case by diffing each existing table's columns against
    the model and ALTER TABLE ... ADD COLUMN-ing whatever's missing.
    """
    bind = bind or engine
    Base.metadata.create_all(bind=bind)

    inspector = inspect(bind)
    with bind.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing_columns = {
                col["name"] for col in inspector.get_columns(table.name)
            }
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                ddl = CreateColumn(column).compile(dialect=bind.dialect)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
