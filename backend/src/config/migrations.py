"""Idempotent column upgrades for existing demo databases (PostgreSQL)."""

from sqlalchemy import text
from src.config.database import engine

_UPGRADES = [
    "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'checkout'",
    "CREATE INDEX IF NOT EXISTS ix_datasets_kind ON datasets (kind)",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS finance_dataset_id INTEGER NULL REFERENCES datasets (id)",
    "ALTER TABLE schedules ADD COLUMN IF NOT EXISTS finance_dataset_id INTEGER NULL REFERENCES datasets (id)",
]


def apply_upgrades():
    with engine.begin() as connection:
        for statement in _UPGRADES:
            connection.execute(text(statement))
