"""Read-only view of the opt-in launcher, with an independent live DB probe."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time

STATUSES = {"ready", "starting", "down", "stale", "unconfigured"}
PHASES = {"starting", "idle", "polling", "analyzing", "source", "build", "apply", "error"}
COMPONENTS = ("docker", "analyzer", "worker")
LABELS = {"api": "API", "db": "DB", "docker": "Docker", "analyzer": "분석기", "worker": "배포 작업자"}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _snapshot(path, run_id, now):
    if not path or not run_id:
        return "unconfigured", None
    try:
        # Bound reads; the API never runs Docker or mutates launcher state.
        with Path(path).open() as stream:
            data = json.loads(stream.read(16385))
        if not isinstance(data, dict):
            return "stale", None
        checked, expires = data.get("checked_at"), data.get("expires_at")
        if (data.get("version") != 1 or data.get("run_id") != run_id
                or data.get("target") != "local_docker" or data.get("analysis_mode") != "rules"
                or not _number(checked) or not _number(expires)
                or not 0 <= checked <= now < expires <= checked + 15):
            return "stale", None
        components = data.get("components")
        if not isinstance(components, dict) or set(components) != set(COMPONENTS):
            return "stale", None
        for item in components.values():
            if not isinstance(item, dict) or item.get("status") not in STATUSES:
                return "stale", None
            if "phase" in item and item["phase"] not in PHASES:
                return "stale", None
        return "ready", data
    except FileNotFoundError:
        return "starting", None
    except (OSError, ValueError, TypeError):
        return "stale", None


def readiness(check_database, *, env=None, now=None):
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    state, snapshot = _snapshot(env.get("ONPREM_STATUS_FILE"), env.get("ONPREM_RUN_ID"), now)
    components = {"api": {"status": "ready"}}
    try:
        check_database()
        components["db"] = {"status": "ready"}
    except Exception:
        # Deliberately exclude exception text (connection details may contain secrets).
        components["db"] = {"status": "down"}
    for name in COMPONENTS:
        item = snapshot["components"][name] if snapshot else {"status": state}
        components[name] = {key: item[key] for key in ("status", "phase") if key in item}
    status = next((value for value in ("down", "unconfigured", "stale", "starting")
                   if any(item["status"] == value for item in components.values())), "ready")
    reasons = []
    for name, item in components.items():
        if item["status"] == "ready":
            continue
        action = "실행 터미널과 전용 데이터 폴더의 logs를 확인한 뒤 실행기를 다시 시작하세요."
        if item["status"] == "unconfigured":
            action = "플랫폼을 실행하는 PC에서 bash scripts/run_onprem.sh를 실행하세요."
        elif name == "docker":
            action = "Docker Desktop/Engine과 WSL 연결을 확인하세요. 원격 Docker는 지원하지 않습니다."
        reasons.append({"code": f"{name}_{item['status']}",
                        "message": f"{LABELS[name]} 준비 상태: {item['status']}", "action": action})
    return {"status": status, "target": "local_docker", "analysis_mode": "rules",
            "checked_at": datetime.fromtimestamp(snapshot["checked_at"], timezone.utc).isoformat() if snapshot else None,
            "expires_at": datetime.fromtimestamp(snapshot["expires_at"], timezone.utc).isoformat() if snapshot else None,
            "components": components, "reasons": reasons}
