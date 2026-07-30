from sqlalchemy import create_engine, inspect, select

from database import sync_schema
from models import Meal


def make_stale_engine(tmp_path):
    """A SQLite file whose `meals` table predates the updated_at/
    analysis_error/clarification_text columns, reproducing the on-disk state
    that crashed the Render deploy: `create_all` had already run against an
    older version of models.py, and the file persisted across later deploys
    that added columns to the model.
    """
    database_path = tmp_path / "stale_mealbot.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE meals (
                id INTEGER PRIMARY KEY,
                user_phone VARCHAR NOT NULL,
                twilio_message_sid VARCHAR(64) UNIQUE NOT NULL,
                image_url VARCHAR NOT NULL,
                image_content_type VARCHAR,
                meal_type VARCHAR(16),
                status VARCHAR(32),
                created_at DATETIME
            )
            """
        )
    return engine


def test_sync_schema_adds_missing_columns_to_existing_table(tmp_path):
    engine = make_stale_engine(tmp_path)

    columns_before = {col["name"] for col in inspect(engine).get_columns("meals")}
    assert "updated_at" not in columns_before

    sync_schema(bind=engine)

    columns_after = {col["name"] for col in inspect(engine).get_columns("meals")}
    assert "updated_at" in columns_after
    assert "analysis_error" in columns_after
    assert "clarification_text" in columns_after


def test_sync_schema_leaves_query_against_new_column_working(tmp_path):
    engine = make_stale_engine(tmp_path)
    sync_schema(bind=engine)

    with engine.connect() as conn:
        # This is the exact query recover_stuck_meals runs on startup; it's
        # what crashed with "no such column: meals.updated_at" on Render.
        result = conn.execute(select(Meal).where(Meal.status == "stored"))
        assert result.fetchall() == []


def test_sync_schema_is_idempotent(tmp_path):
    engine = make_stale_engine(tmp_path)

    sync_schema(bind=engine)
    sync_schema(bind=engine)  # should not error on already-added columns

    columns = {col["name"] for col in inspect(engine).get_columns("meals")}
    assert "updated_at" in columns
