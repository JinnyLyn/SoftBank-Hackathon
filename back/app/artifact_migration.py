"""Explicit, resumable migration of local DB artifact references to private S3."""

from __future__ import annotations

import os
import hmac
from pathlib import Path
from uuid import uuid4

from app.artifacts import artifact_sha256, delete_artifact, upload_file_to_s3
from app.database import connect
from app.settings import plan_artifact_directory, upload_directory


def migrate_local_artifacts(*, apply: bool = False) -> dict[str, int]:
    """Preflight then copy local source/plan artifacts and update DB references.

    Local files are deliberately retained so the operation can be resumed safely
    and rolled back manually if a later server cutover needs them.
    """
    bucket = os.getenv("ARTIFACT_S3_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("ARTIFACT_S3_BUCKET을 설정해야 합니다.")

    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, source_path, source_sha256 AS expected_sha256 "
            "FROM projects WHERE source_path IS NOT NULL"
        )
        source_rows = cursor.fetchall()
        cursor.execute(
            "SELECT id, terraform_plan_path, terraform_plan_sha256 AS expected_sha256 "
            "FROM deployment_plans WHERE terraform_plan_path IS NOT NULL"
        )
        plan_rows = cursor.fetchall()

    sources = [
        ("projects", "source_path", row["id"], row["source_path"], row["expected_sha256"],
         f"projects/{row['id']}/source-{uuid4().hex}.zip", "application/zip", upload_directory())
        for row in source_rows if not str(row["source_path"]).startswith("s3://")
    ]
    plans = [
        ("deployment_plans", "terraform_plan_path", row["id"], row["terraform_plan_path"],
         row["expected_sha256"], f"plans/{row['id']}/legacy-{uuid4().hex}.tfplan", "application/octet-stream",
         plan_artifact_directory())
        for row in plan_rows if not str(row["terraform_plan_path"]).startswith("s3://")
    ]
    entries = sources + plans
    missing = 0
    for _, _, _, reference, expected_sha256, _, _, root in entries:
        path = Path(str(reference)).expanduser().resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            missing += 1
            continue
        actual_sha256 = artifact_sha256(str(path))
        if not actual_sha256 or not expected_sha256 or not hmac.compare_digest(actual_sha256, expected_sha256):
            missing += 1
    if missing:
        raise RuntimeError(
            f"경로·파일·SHA-256 확인에 실패한 artifact가 {missing}개 있습니다. "
            "UPLOAD_DIR/PLAN_ARTIFACT_DIR과 원본 파일을 확인하세요."
        )

    summary = {
        "projects_total": len(source_rows),
        "plans_total": len(plan_rows),
        "already_s3": len(source_rows) + len(plan_rows) - len(entries),
        "local_pending": len(entries),
        "migrated": 0,
    }
    if not apply:
        return summary

    for table, column, identifier, old_reference, _, key, content_type, _ in entries:
        local_path = Path(str(old_reference)).expanduser().resolve()
        new_reference = upload_file_to_s3(local_path, key, content_type)
        try:
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {table} SET {column} = %s WHERE id = %s AND {column} = %s",
                    (new_reference, identifier, old_reference),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    delete_artifact(new_reference)
                    continue
                connection.commit()
        except Exception:
            # Commit outcome can be ambiguous after a network interruption. Keep
            # the S3 object so a committed DB reference is never left dangling.
            raise
        summary["migrated"] += 1
    return summary
