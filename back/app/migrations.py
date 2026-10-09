from __future__ import annotations

from pathlib import Path

from app.database import connect

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def migrate() -> None:
    with connect(autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(120) NOT NULL PRIMARY KEY,
                applied_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""
        )
        cursor.execute("SELECT version FROM schema_migrations")
        applied = {row["version"] for row in cursor.fetchall()}
        for migration_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = migration_path.name
            if version in applied:
                continue
            sql = migration_path.read_text(encoding="utf-8")
            for statement in (part.strip() for part in sql.split(";")):
                if statement:
                    cursor.execute(statement)
            cursor.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            print(f"Applied migration: {version}")
