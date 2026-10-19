#!/usr/bin/env python3
"""Run small platform backend API contract checks against a live test stack.

- 라우터가 만드는 404·405도 다른 오류처럼 {"error": ...} 형식이다.
- 255자를 넘는 ZIP 파일명을 잘라도 확장자(.zip)가 남아 업로드가 거절되지 않는다.
"""

from __future__ import annotations

import sys
import uuid

import test_platform_back_rollbacks as api


def main() -> int:
    api.wait_for_api()

    missing = api.request(f"/api/{uuid.uuid4().hex}", expected=404)
    api.require(isinstance(missing.get("error"), str) and "detail" not in missing, f"unknown path: not an error body: {missing}")
    wrong_method = api.request("/health", method="DELETE", expected=405)
    api.require(isinstance(wrong_method.get("error"), str) and "detail" not in wrong_method, f"wrong method: not an error body: {wrong_method}")
    print("PASS: unknown paths and wrong methods use the {error} body")

    long_name = "a" * 300 + ".zip"
    project = api.request(
        f"/api/projects?name=long-name-{uuid.uuid4().hex[:8]}&filename={long_name}",
        method="POST", data=api.source_zip(), expected=201, headers={"Content-Type": "application/zip"},
    )
    stored = project["source_filename"]
    api.require(len(stored) == 255 and stored.endswith(".zip"), f"long file name was not truncated safely: {stored[-20:]!r} ({len(stored)})")
    print("PASS: a long ZIP file name is truncated without losing the .zip extension")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
