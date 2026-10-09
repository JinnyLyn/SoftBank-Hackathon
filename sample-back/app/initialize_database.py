import time
from pathlib import Path

from .main import connection


def initialize_database() -> None:
    schema = (Path(__file__).with_name("schema.sql")).read_text()
    last_error: Exception | None = None
    for _ in range(30):
        try:
            with connection() as db, db.cursor() as cursor:
                for statement in schema.split(";"):
                    if statement.strip():
                        cursor.execute(statement)
                db.commit()
            return
        except Exception as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError("MySQL에 연결하지 못했습니다.") from last_error


if __name__ == "__main__":
    initialize_database()
