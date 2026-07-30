# database configuration

import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, literal, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.schema import CreateColumn, DefaultClause

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
    change (e.g. a new `clarification_text` column) would otherwise crash
    every startup with "no such column" until someone manually alters the
    file. This covers that case by diffing each existing table's columns
    against the model and ALTER TABLE ... ADD COLUMN-ing whatever's missing.

    SQLite refuses to add a NOT NULL column with no DEFAULT to a table that
    already has rows ("Cannot add a NOT NULL column with default value
    NULL") — existing rows would have nothing to fill it with. A column
    like `User.reminders_enabled` is NOT NULL but only carries a Python-side
    `default=` (applied by the ORM on insert), not a DDL-level
    `server_default`, so the plain compiled column definition hits exactly
    that error. For that case we render the Python default as a literal and
    add it as the column's DDL default, so SQLite can backfill existing rows.

    This only handles additive, constant-default column changes — not
    renames/drops/type changes, and not columns whose default is a
    non-constant expression (e.g. `server_default=func.now()`), which
    SQLite refuses to add via ALTER TABLE to a non-empty table under any
    circumstances. Those need a one-off manual fix (see project notes).
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
                add_column = column.copy()
                if (
                    not add_column.nullable
                    and add_column.server_default is None
                    and column.default is not None
                    and column.default.is_scalar
                ):
                    default_literal = literal(
                        column.default.arg, type_=column.type
                    ).compile(dialect=bind.dialect, compile_kwargs={"literal_binds": True})
                    add_column.server_default = DefaultClause(
                        text(str(default_literal))
                    )
                ddl = CreateColumn(add_column).compile(dialect=bind.dialect)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
