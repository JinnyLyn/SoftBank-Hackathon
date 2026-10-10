"""worker(planner·executor)와 cost 시험. 표준 라이브러리만 쓴다.

실행: python infra/worker/test_worker.py
실제 AWS·Terraform·Docker는 쓰지 않는다. 백엔드는 시험용 HTTP 서버로, deploy.sh는 같은 인터페이스의 가짜 bash 스크립트로 대신한다.
이 시험이 통과해도 실제 백엔드(PR #10)·실제 AWS와의 연동이 확인된 것은 아니다.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cost  # noqa: E402
import worker  # noqa: E402

TOKEN = "test-token-value-123"

FAKE_DEPLOY = r'''#!/usr/bin/env bash
# deploy.sh와 같은 인터페이스의 가짜. 호출 기록은 $FAKE_LOG 에 남긴다
echo "$*" >> "$FAKE_LOG"
cmd="$1"; shift
case "$cmd" in
  foundation-info)
    # 최신 foundation 출력(compact JSON, 마지막 줄). FAIL이면 실패, FAKE_FOUNDATION_JSON이 있으면 그 값, 없으면 캐시 파일, 아무것도 없으면 {}
    [ "${FAKE_FOUNDATION_FAIL:-0}" = "0" ] || { echo "foundation 출력을 읽지 못했습니다" >&2; exit 1; }
    if [ -n "${FAKE_FOUNDATION_JSON:-}" ]; then echo "$FAKE_FOUNDATION_JSON"
    elif [ -f "$FAKE_DEPLOYMENTS/foundation.json" ]; then tr -d '\n' < "$FAKE_DEPLOYMENTS/foundation.json"; echo
    else echo '{}'; fi ;;
  make-id) echo "fake0001" ;;
  image-ref) echo "123456789012.dkr.ecr.sa-east-1.amazonaws.com/paved-clouds/apps:$1-r1" ;;
  up)
    while [ $# -gt 0 ]; do case "$1" in --id) id="$2"; shift 2;; *) shift;; esac; done
    [ "${FAKE_UP_RC:-0}" = "0" ] || { echo "plan 실패" >&2; exit "$FAKE_UP_RC"; }
    mkdir -p "$FAKE_DEPLOYMENTS/$id"; echo "plan-bytes-$id" > "$FAKE_DEPLOYMENTS/$id/tfplan" ;;
  build)
    echo "빌드 로그" >&2
    [ "${FAKE_BUILD_RC:-0}" = "0" ] || { echo "빌드 실패 password=hunter2" >&2; exit "$FAKE_BUILD_RC"; }
    echo "${FAKE_BUILD_IMAGE}" ;;
  apply)
    echo "[00:00:01] apply (저장된 계획)"
    echo "[00:00:02] 헬스체크 대기 (최대 300초)"
    if [ -n "${FAKE_HANG:-}" ]; then
      ( while true; do date +%s%N > "$FAKE_HEARTBEAT"; sleep 0.2; done ) &
      sleep 60
    fi
    [ "${FAKE_APPLY_RC:-0}" = "0" ] || { echo "[00:00:09] 헬스체크 실패" ; exit "$FAKE_APPLY_RC"; }
    echo '{"url": {"value": "http://alb.example:8001"}}' > "$FAKE_DEPLOYMENTS/$1/outputs.json" ;;
  diagnose) echo '{"deployId": "fake0001", "targets": []}' ;;
  *) echo "unknown $cmd" >&2; exit 9 ;;
esac
'''


class FakeBackend:
    """back/API.md의 필요한 부분만 흉내 내는 서버."""

    def __init__(self):
        self.projects = []
        self.plans = {}          # project_id → [PlanOut]
        self.analyses = {}       # project_id → AnalysisOut
        self.created_plans = []
        self.uploads = []        # (plan_id, bytes, token 헤더)
        self.events = []         # (deployment_id, body)
        self.claims = []         # claim 요청에 대한 응답 큐
        self.headers_seen = []   # (path, X-Worker-Token)
        self.plan_status = 201
        self.event_failures = 0   # 앞으로 몇 번의 이벤트 보고를 503으로 거절할지
        self.pages = None         # 목록 페이지(리스트의 리스트). 지정하면 cursor로 넘긴다
        self.upload_failures = 0  # 앞으로 몇 번의 plan 파일 업로드를 503으로 거절할지
        self.lose_plan_response = 0   # 앞으로 몇 번의 계획 등록을 "서버에는 등록하고 응답 없이 연결을 닫는" 방식으로 처리할지
        self.plans_get_failures = 0   # 앞으로 몇 번의 계획 목록 조회를 503으로 거절할지
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None):
                data = json.dumps(obj if obj is not None else {}).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                outer.headers_seen.append((self.path, self.headers.get("X-Worker-Token")))
                p = self.path
                if p.startswith("/api/projects?"):
                    if outer.pages is not None:
                        cur = p.split("cursor=")[1] if "cursor=" in p else "0"
                        idx = int(cur)
                        nxt = str(idx + 1) if idx + 1 < len(outer.pages) else None
                        return self._send(200, {"items": outer.pages[idx], "next_cursor": nxt})
                    return self._send(200, {"items": outer.projects, "next_cursor": None})
                if p.endswith("/plans"):
                    if outer.plans_get_failures > 0:
                        outer.plans_get_failures -= 1
                        return self._send(503, {"error": "일시 오류"})
                    return self._send(200, outer.plans.get(p.split("/")[3], []))
                if p.endswith("/analyses/latest"):
                    a = outer.analyses.get(p.split("/")[3])
                    return self._send(200, a) if a else self._send(404, {"error": "분석 결과가 없습니다."})
                return self._send(404, {"error": "없음"})

            def do_POST(self):
                outer.headers_seen.append((self.path, self.headers.get("X-Worker-Token")))
                body = self._body()
                p = self.path
                if p == "/api/plans":
                    obj = json.loads(body)
                    outer.created_plans.append(obj)
                    if outer.plan_status != 201:
                        return self._send(outer.plan_status, {"error": "거부"})
                    created = {"id": "plan-1", "fingerprint": "f" * 64, "status": "awaiting_approval", "terraform_plan_ready": False, **obj}
                    outer.plans.setdefault(obj["project_id"], []).append(created)
                    if outer.lose_plan_response > 0:
                        outer.lose_plan_response -= 1
                        self.close_connection = True   # 서버는 등록을 끝냈지만 응답을 보내지 못한다
                        return
                    return self._send(201, created)
                if p.startswith("/api/worker/plans/") and p.endswith("/terraform-plan"):
                    if outer.upload_failures > 0:
                        outer.upload_failures -= 1
                        return self._send(503, {"error": "일시 오류"})
                    outer.uploads.append((p.split("/")[4], body, self.headers.get("X-Worker-Token")))
                    for plans in outer.plans.values():
                        for pl in plans:
                            if pl.get("id") == p.split("/")[4]:
                                pl["terraform_plan_ready"] = True
                    return self._send(200, {"ok": True})
                if p == "/api/worker/deployments/claim":
                    if self.headers.get("X-Worker-Token") != TOKEN:
                        return self._send(401, {"error": "인증 실패"})
                    return self._send(200, {"job": outer.claims.pop(0) if outer.claims else None})
                if p.startswith("/api/worker/deployments/") and p.endswith("/events"):
                    if outer.event_failures > 0:
                        outer.event_failures -= 1
                        return self._send(503, {"error": "일시 오류"})
                    outer.events.append((p.split("/")[4], json.loads(body)))
                    return self._send(200, {})
                return self._send(404, {"error": "없음"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wk-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "deployments").mkdir()
        script = self.tmp / "deploy.sh"
        script.write_bytes(FAKE_DEPLOY.encode("utf-8"))   # \n 줄 끝으로 쓴다
        self.log_file = self.tmp / "calls.log"
        self.old_env = dict(os.environ)
        os.environ.update({"FAKE_LOG": worker.posix(self.log_file), "FAKE_DEPLOYMENTS": worker.posix(self.tmp / "deployments")})
        for k in ("FAKE_UP_RC", "FAKE_BUILD_RC", "FAKE_APPLY_RC", "FAKE_BUILD_IMAGE", "FAKE_HANG", "FAKE_FOUNDATION_JSON", "FAKE_FOUNDATION_FAIL"):
            os.environ.pop(k, None)
        os.environ["WORKER_API_TOKEN"] = TOKEN
        os.environ["FAKE_HEARTBEAT"] = worker.posix(self.tmp / "heartbeat")
        self.old_retry_delay = worker.REPORT_RETRY_DELAY
        worker.REPORT_RETRY_DELAY = 0.0   # 보고 재시도 대기를 없앤다
        self.addCleanup(lambda: setattr(worker, "REPORT_RETRY_DELAY", self.old_retry_delay))
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.old_env)))
        self.cfg = worker.Config(token=TOKEN, deploy_sh=script, deployments_dir=self.tmp / "deployments")
        self.backend = FakeBackend()
        self.addCleanup(self.backend.close)
        self.api = worker.Api(self.backend.url, TOKEN, timeout=10)
        self.cfg.api_url = self.backend.url

    def calls(self):
        return self.log_file.read_text(encoding="utf-8").splitlines() if self.log_file.exists() else []


GOOD_RESULT = {
    "app_config": {"container_port": 8000, "health_check_path": "/health", "use_database": True,
                   "environment": {"COOKIE_SECURE": "false"}, "init_command": ["python", "-m", "backend.app.initialize_database"]},
    "dockerfile": "sample-back/Dockerfile",
    "scale": {"expected_users": "~1,000", "traffic_pattern": "steady", "monthly_budget_usd": 120},
}
SRC_BYTES = b"src-zip-bytes"
SRC_SHA = hashlib.sha256(SRC_BYTES).hexdigest()
PROJECT = {"id": "11111111-1111-1111-1111-111111111111", "name": "Launchpad", "source_sha256": SRC_SHA}


class CostTests(unittest.TestCase):
    def setUp(self):
        self.p = cost.load_prices()

    def test_estimate_matches_hand_calculation(self):
        # lean: (0.25 vCPU x 0.0696 + 0.5 GB x 0.0076) x 730 = 15.476, 공용: ALB 24.82 + RDS 24.82 + 스토리지 4.38 + 공인 IPv4 6개 21.90 = 75.92(기본 가정: 3 AZ + NAT 3대)
        e = cost.estimate("lean", self.p)
        self.assertAlmostEqual(e["app_monthly"], 15.48, places=2)
        self.assertAlmostEqual(e["shared_monthly"], 75.92, places=2)
        self.assertAlmostEqual(e["total_monthly"], 91.40, places=2)
        self.assertEqual(e["region"], "sa-east-1")
        self.assertTrue(e["excluded"])   # 제외 항목을 반드시 밝힌다
        self.assertIn("공인 IPv4 주소", [r["service"] for r in e["resources"]])   # 공인 IPv4 요금을 포함한다

    def test_public_ipv4_count_follows_the_deployed_foundation(self):
        # 기본 가정(foundation 기본값): 3 AZ + NAT 3대 = 6개
        self.assertEqual(cost.ipv4_count(self.p)[0], 6)
        # 배포된 foundation: 2 AZ, NAT 인스턴스 1대 = 3개 → lean 80.45
        f = {"task_subnet_ids": ["a", "b"], "assign_public_ip": False, "nat_instance_count": 1}
        self.assertEqual(cost.ipv4_count(self.p, f), (3, 2, 1))
        self.assertAlmostEqual(cost.estimate("lean", self.p, foundation=f)["total_monthly"], 80.45, places=2)
        # NAT 인스턴스가 없는 구성(assign_public_ip=true): ALB 주소만 2개
        f2 = {"task_subnet_ids": ["a", "b"], "assign_public_ip": True}
        self.assertEqual(cost.ipv4_count(self.p, f2), (2, 2, 0))
        # NAT 대수 출력이 없는 옛 foundation: AZ 수는 읽고 NAT는 기본 가정
        f3 = {"task_subnet_ids": ["a", "b"], "assign_public_ip": False}
        self.assertEqual(cost.ipv4_count(self.p, f3), (5, 2, 3))
        spec = cost.estimate("lean", self.p, foundation=f)["resources"][-1]["spec"]
        self.assertIn("ALB 가용 영역 2개", spec)
        self.assertIn("탄력적 IP 1개", spec)

    def test_task_public_ipv4_is_charged_per_task_when_there_is_no_nat(self):
        # NAT 인스턴스가 없는 구성은 앱 태스크마다 공인 IPv4가 붙는다(시간당 $0.005 x 730 = 월 $3.65). 태스크 수만큼 앱 비용에 더한다
        nonat = {"task_subnet_ids": ["a", "b"], "assign_public_ip": True}
        withnat = {"task_subnet_ids": ["a", "b"], "assign_public_ip": False, "nat_instance_count": 1}
        for tier, tasks in [("lean", 1), ("balanced", 1), ("roomy", 2)]:
            with self.subTest(tier=tier):
                a = cost.estimate(tier, self.p, foundation=nonat)
                b = cost.estimate(tier, self.p, foundation=withnat)
                self.assertAlmostEqual(a["app_monthly"] - b["app_monthly"], 3.65 * tasks, places=2)
        e = cost.estimate("roomy", self.p, foundation=nonat)
        self.assertIn("공인 IPv4 주소 (앱 태스크)", [r["service"] for r in e["resources"]])
        self.assertAlmostEqual(sum(r["monthlyUsd"] for r in e["resources"]), e["total_monthly"], delta=0.05)   # 항목 합이 총액과 맞다
        # foundation을 모르면 기본 가정(NAT 있음)이라 태스크 IPv4를 더하지 않는다
        self.assertNotIn("공인 IPv4 주소 (앱 태스크)", [r["service"] for r in cost.estimate("lean", self.p)["resources"]])
        # 예산 판정에도 들어간다: 이 비용이 빠져 있으면 예산을 살짝 넘는 구성이 추천됐다
        bal = cost.estimate("balanced", self.p, foundation=nonat)["total_monthly"]
        self.assertEqual(cost.recommend("~1,000", "steady", bal, self.p, foundation=nonat)["recommended"], "balanced")
        self.assertEqual(cost.recommend("~1,000", "steady", bal - 0.01, self.p, foundation=nonat)["recommended"], "lean")

    def test_balanced_and_roomy_cost_more_and_roomy_runs_two_tasks(self):
        lean, bal, roomy = (cost.estimate(t, self.p)["total_monthly"] for t in cost.ORDER)
        self.assertLess(lean, bal)
        self.assertLess(bal, roomy)
        r = cost.estimate("roomy", self.p)
        self.assertEqual((r["min_tasks"], r["max_tasks"]), (2, 4))
        self.assertIn("x 2개", r["resources"][0]["spec"])

    def test_arm_is_cheaper_than_x86(self):
        self.assertLess(cost.estimate("balanced", self.p, "ARM64")["app_monthly"], cost.estimate("balanced", self.p, "X86_64")["app_monthly"])

    def test_unknown_inputs_rejected(self):
        with self.assertRaises(ValueError):
            cost.estimate("huge", self.p)
        with self.assertRaises(ValueError):
            cost.estimate("lean", self.p, "MIPS")

    def test_users_pick_tier_and_peak_raises_one(self):
        self.assertEqual(cost.recommend("~100", "steady", None, self.p)["recommended"], "lean")
        self.assertEqual(cost.recommend("~1,000", "steady", None, self.p)["recommended"], "balanced")
        self.assertEqual(cost.recommend("~100", "peak", None, self.p)["recommended"], "balanced")
        self.assertEqual(cost.recommend("~10,000", "peak", None, self.p)["recommended"], "roomy")   # 이미 최대
        self.assertEqual(cost.recommend(None, None, None, self.p)["recommended"], "balanced")

    def test_budget_lowers_tier_and_reports_reason(self):
        r = cost.recommend("~10,000", "steady", 110, self.p)   # roomy는 199.73 > 110, balanced 106.87 이하
        self.assertEqual(r["recommended"], "balanced")
        self.assertIn("낮춤", r["reason"])

    def test_budget_below_cheapest_gives_no_recommendation_with_reason(self):
        r = cost.recommend("~100", "steady", 30, self.p)
        self.assertIsNone(r["recommended"])
        self.assertIn("91.40", r["reason"])

    def test_budget_exactly_equal_is_allowed(self):
        self.assertEqual(cost.recommend("~100", "steady", 91.40, self.p)["recommended"], "lean")


class AppConfigTests(unittest.TestCase):
    def conv(self, result, tier="balanced"):
        return worker.app_config_from_analysis(result, tier)

    def test_valid_config_gets_size_from_tier_not_from_analysis(self):
        res = json.loads(json.dumps(GOOD_RESULT))
        res["app_config"]["task_size"] = "medium"   # LLM이 정해도 무시한다
        app, df = self.conv(res, "balanced")
        self.assertEqual((app["task_size"], app["min_tasks"], app["max_tasks"]), ("small", 1, 2))
        self.assertEqual(df, "sample-back/Dockerfile")
        self.assertEqual(app["init_command"], ["python", "-m", "backend.app.initialize_database"])
        self.assertTrue(app["use_database"])

    def test_defaults(self):
        app, df = self.conv({"app_config": {"container_port": 3000}}, "lean")
        self.assertEqual((app["health_check_path"], app["environment"], app["use_database"], df), ("/", {}, False, "Dockerfile"))
        self.assertNotIn("init_command", app)

    def test_rejections(self):
        base = lambda **kw: {"app_config": {"container_port": 8000, **kw}}   # noqa: E731
        bad = [
            ({}, "app_config가 없"),
            ({"app_config": {}}, "container_port"),
            (base(container_port=0), "container_port"),
            (base(container_port=70000), "container_port"),
            (base(container_port=True), "container_port"),
            (base(health_check_path="health"), "health_check_path"),
            (base(health_check_path="/a b"), "health_check_path"),
            (base(environment={"DATABASE_URL": "x"}), "DATABASE_URL"),
            (base(environment={"API_KEY": "x"}), "허용되지"),
            (base(environment={"lower": "x"}), "허용되지"),
            (base(environment={"A": 1}), "문자열"),
            (base(environment={f"K{i}": "v" for i in range(21)}), "20개"),
            (base(init_command="python"), "init_command"),
            (base(init_command=["a"] * 11), "init_command"),
            (base(init_command=[""]), "init_command"),
        ]
        for res, expect in bad:
            with self.subTest(res=str(res)[:60]):
                with self.assertRaises(worker.PlanError) as cm:
                    self.conv(res)
                self.assertIn(expect, str(cm.exception))

    def test_secret_looking_env_values_are_rejected_even_with_harmless_names(self):
        for val in ["sk-abcdefghijklmnopqrstuvwxyz", "ghp_" + "a" * 30, "-----BEGIN RSA PRIVATE KEY-----", "mysql://u:p@h:3306/db",
                    "password=hunter2", "AKIAABCDEFGHIJKLMNOP", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig"]:
            with self.subTest(val=val[:20]):
                with self.assertRaises(worker.PlanError) as cm:
                    self.conv({"app_config": {"container_port": 80, "environment": {"APP_CONFIG": val}}})
                self.assertIn("APP_CONFIG", str(cm.exception))
                self.assertNotIn(val, str(cm.exception))   # 오류 메시지에도 값을 싣지 않는다

    def test_normal_env_values_pass(self):
        app, _ = self.conv({"app_config": {"container_port": 80, "environment": {"COOKIE_SECURE": "false", "BASE_URL": "https://example.com/a", "LANG_CODE": "ko_KR"}}})
        self.assertEqual(app["environment"]["BASE_URL"], "https://example.com/a")

    def test_dockerfile_path_must_stay_inside_source(self):
        for df in ["/etc/Dockerfile", "../Dockerfile", "a/../../b", ""]:
            with self.subTest(df=df):
                with self.assertRaises(worker.PlanError):
                    self.conv({"app_config": {"container_port": 80}, "dockerfile": df})

    def test_scale(self):
        self.assertEqual(worker.scale_from_analysis(GOOD_RESULT), ("~1,000", "steady", 120.0))
        self.assertEqual(worker.scale_from_analysis({}), (None, None, None))
        with self.assertRaises(worker.PlanError):
            worker.scale_from_analysis({"scale": {"monthly_budget_usd": "abc"}})
        with self.assertRaises(worker.PlanError):
            worker.scale_from_analysis({"scale": {"monthly_budget_usd": -1}})


class RedactTests(unittest.TestCase):
    def test_secrets_are_masked(self):
        for text in ["AWS_SECRET_ACCESS_KEY=abcd1234", "password: hunter2", "key AKIAABCDEFGHIJKLMNOP end", "mysql://user:pw123@host:3306/db"]:
            with self.subTest(text=text):
                out = worker.redact(text)
                self.assertIn("[가림]", out)
                for secret in ["abcd1234", "hunter2", "AKIAABCDEFGHIJKLMNOP", "pw123"]:
                    self.assertNotIn(secret, out)

    def test_plain_text_kept(self):
        self.assertEqual(worker.redact("헬스체크 대기 (최대 300초)"), "헬스체크 대기 (최대 300초)")


class ApiTests(Base):
    def test_token_only_on_worker_paths(self):
        self.api.get("/api/projects?limit=20")
        self.api.post("/api/worker/deployments/claim")
        seen = dict(self.backend.headers_seen)
        self.assertIsNone(seen["/api/projects?limit=20"])
        self.assertEqual(seen["/api/worker/deployments/claim"], TOKEN)

    def test_http_error_becomes_apierror_with_status(self):
        with self.assertRaises(worker.ApiError) as cm:
            self.api.get("/api/projects/x/analyses/latest")
        self.assertEqual(cm.exception.status, 404)

    def test_wrong_token_is_401(self):
        bad = worker.Api(self.backend.url, "wrong")
        with self.assertRaises(worker.ApiError) as cm:
            bad.post("/api/worker/deployments/claim")
        self.assertEqual(cm.exception.status, 401)

    def test_connection_failure_is_apierror_status_0(self):
        dead = worker.Api("http://127.0.0.1:9", TOKEN, timeout=2)
        with self.assertRaises(worker.ApiError) as cm:
            dead.get("/api/projects")
        self.assertEqual(cm.exception.status, 0)

    def test_config_repr_hides_token(self):
        self.assertNotIn(TOKEN, repr(self.cfg))


class PlannerTests(Base):
    def analysis(self, result=None):
        return {"id": "22222222-2222-2222-2222-222222222222", "project_id": PROJECT["id"], "result": result or GOOD_RESULT}

    def test_happy_path_registers_plan_with_hash_and_uploads_binary(self):
        plan = worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertEqual(plan["id"], "plan-1")
        body = self.backend.created_plans[0]
        self.assertEqual((body["project_id"], body["target"], body["module_id"]), (PROJECT["id"], "aws", "ecs-web-app"))
        v = body["variables"]
        self.assertEqual(v["deploy_id"], "fake0001")
        self.assertTrue(v["image"].endswith("apps:fake0001-r1"))
        self.assertEqual(v["dockerfile"], "sample-back/Dockerfile")
        self.assertEqual(v["tier"], "balanced")
        self.assertTrue(v["recommended"])
        self.assertEqual(v["app"]["task_size"], "small")
        self.assertEqual(v["source_sha256"], SRC_SHA)          # 승인된 소스 지문이 계획 변수(fingerprint 대상)에 들어간다
        self.assertEqual(v["cpu_architecture"], "X86_64")
        self.assertEqual(v["resources"][0]["service"], "ECS Fargate")
        self.assertEqual(body["cost_estimate"]["currency"], "USD")
        self.assertEqual(body["cost_estimate"]["period"], "month")
        self.assertGreater(float(body["cost_estimate"]["amount"]), 0)
        # 해시는 실제 plan 파일의 SHA-256, 업로드한 바이트도 같은 파일
        plan_bytes = (self.tmp / "deployments" / "fake0001" / "tfplan").read_bytes()
        self.assertEqual(body["terraform_plan_sha256"], hashlib.sha256(plan_bytes).hexdigest())
        self.assertEqual(len(self.backend.uploads), 1)
        self.assertEqual(self.backend.uploads[0][:2], ("plan-1", plan_bytes))
        self.assertEqual(self.backend.uploads[0][2], TOKEN)   # 업로드는 worker 경로라 토큰이 간다
        self.assertIn("제외", body["summary"])
        self.assertIn("가격표", body["summary"])

    def test_plan_is_made_with_plan_only_and_without_building(self):
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        calls = self.calls()
        up = [c for c in calls if c.startswith("up ")]
        self.assertEqual(len(up), 1)
        self.assertIn("--plan-only", up[0])
        self.assertIn("--id fake0001", up[0])
        self.assertIn("--arch X86_64", up[0])
        self.assertFalse(any(c.startswith("build") or c.startswith("apply") for c in calls))   # 승인 전에는 빌드도 적용도 하지 않는다

    def test_requested_architecture_is_passed_to_planning_and_recorded(self):
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis(), arch="ARM64")
        up = [c for c in self.calls() if c.startswith("up ")][0]
        self.assertIn("--arch ARM64", up)
        self.assertEqual(self.backend.created_plans[0]["variables"]["cpu_architecture"], "ARM64")

    def test_project_without_source_sha256_is_a_plan_error_before_anything_runs(self):
        for bad in [None, "", "xyz", "A" * 64]:
            with self.subTest(bad=bad):
                proj = {"id": PROJECT["id"], "name": "x", "source_sha256": bad}
                with self.assertRaises(worker.PlanError):
                    worker.plan_project(self.api, self.cfg, proj, self.analysis())
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.backend.created_plans, [])

    def test_estimate_uses_the_deployed_foundation_file(self):
        (self.tmp / "deployments" / "foundation.json").write_text(
            json.dumps({"task_subnet_ids": ["a", "b"], "assign_public_ip": False, "nat_instance_count": 1}), encoding="utf-8")
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        body = self.backend.created_plans[0]
        ipv4 = [r for r in body["variables"]["resources"] if r["service"] == "공인 IPv4 주소"][0]
        self.assertIn("탄력적 IP 1개", ipv4["spec"])
        self.assertAlmostEqual(float(body["cost_estimate"]["amount"]), 95.92, places=2)   # balanced, 2 AZ + NAT 1대

    def test_replan_discards_an_unapplied_previous_folder(self):
        old = self.tmp / "deployments" / "fake0001"
        old.mkdir()
        (old / "tfplan").write_text("old-plan", encoding="utf-8")
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertEqual((old / "tfplan").read_text(encoding="utf-8").strip(), "plan-bytes-fake0001")   # 새 계획으로 교체됐다
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_replan_refuses_a_folder_that_was_ever_applied(self):
        old = self.tmp / "deployments" / "fake0001"
        old.mkdir()
        (old / "history.log").write_text("x", encoding="utf-8")
        with self.assertRaises(worker.PlanError) as cm:
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertIn("이미 적용된", str(cm.exception))
        self.assertTrue(old.exists())
        self.assertEqual(self.backend.created_plans, [])

    def test_upload_failure_keeps_the_local_plan_and_marks_it_pending(self):
        self.backend.upload_failures = worker.REPORT_RETRIES   # 재시도까지 모두 실패
        plan = worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertTrue(plan.get("_upload_pending"))
        self.assertTrue((self.tmp / "deployments" / "fake0001" / "tfplan").exists())   # 파일을 지우지 않는다
        self.assertEqual(self.backend.uploads, [])
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_transient_upload_errors_are_retried_inside_plan_project(self):
        self.backend.upload_failures = 2
        plan = worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertFalse(plan.get("_upload_pending"))
        self.assertEqual(len(self.backend.uploads), 1)

    def test_secret_like_values_are_not_in_posted_body(self):
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertNotIn(TOKEN, json.dumps(self.backend.created_plans))

    def test_malformed_scale_values_are_plan_errors_not_crashes(self):
        # 분석 결과의 scale 값이 리스트·객체·NaN이면 처리되지 않은 TypeError가 worker 전체를 멈췄다: 해당 프로젝트의 계획 오류로만 처리한다
        cases = [("expected_users", [], False), ("expected_users", {}, False), ("expected_users", 123, False),
                 ("traffic_pattern", [], False), ("traffic_pattern", {}, False), ("traffic_pattern", 5, False),
                 ("monthly_budget_usd", True, False), ("monthly_budget_usd", float("nan"), False),
                 ("monthly_budget_usd", float("inf"), False), ("monthly_budget_usd", "abc", False), ("monthly_budget_usd", [], False)]
        for key, bad, _ in cases:
            with self.subTest(key=key, bad=bad):
                res = json.loads(json.dumps(GOOD_RESULT))
                res["scale"][key] = bad
                with self.assertRaises(worker.PlanError):
                    worker.plan_project(self.api, self.cfg, PROJECT, self.analysis(res))
        self.assertEqual(self.backend.created_plans, [])
        # 올바른 값(문자열 사용자 수, 숫자 예산)은 그대로 통과한다
        self.assertEqual(worker.scale_from_analysis({"scale": {"expected_users": "~100", "traffic_pattern": "peak", "monthly_budget_usd": "50"}}),
                         ("~100", "peak", 50.0))
        self.assertEqual(worker.scale_from_analysis({}), (None, None, None))

    def test_budget_too_small_raises_without_touching_backend_or_terraform(self):
        res = json.loads(json.dumps(GOOD_RESULT))
        res["scale"]["monthly_budget_usd"] = 20
        with self.assertRaises(worker.PlanError) as cm:
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis(res))
        self.assertIn("예산", str(cm.exception))
        self.assertEqual(self.backend.created_plans, [])
        self.assertEqual(self.calls(), ["foundation-info"])   # 읽기 전용인 foundation-info만 부른다(plan·ID 생성·빌드·적용은 하지 않는다)

    def test_cost_uses_the_fresh_foundation_not_the_stale_cache_file(self):
        # 캐시(옛 구성: 2 AZ + NAT 1대)로는 balanced가 $95.92라 예산 $100 안이지만, 지금 구성(3 AZ + NAT 3대)에서는 $106.87이라 넘는다
        (self.tmp / "deployments" / "foundation.json").write_text(
            json.dumps({"task_subnet_ids": ["a", "b"], "assign_public_ip": False, "nat_instance_count": 1}), encoding="utf-8")
        os.environ["FAKE_FOUNDATION_JSON"] = json.dumps({"task_subnet_ids": ["a", "b", "c"], "assign_public_ip": False, "nat_instance_count": 3})
        res = json.loads(json.dumps(GOOD_RESULT))
        res["scale"]["monthly_budget_usd"] = 100
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis(res))
        body = self.backend.created_plans[0]
        self.assertEqual(body["variables"]["tier"], "lean")   # 옛 캐시였다면 balanced($106.87)가 예산을 넘은 채 추천됐다
        self.assertLessEqual(float(body["cost_estimate"]["amount"]), 100)
        # 비용 계산(foundation-info)이 계획 생성(up)보다 먼저다
        calls = self.calls()
        self.assertLess(calls.index("foundation-info"), [i for i, c in enumerate(calls) if c.startswith("up ")][0])

    def test_unreadable_foundation_stops_planning_instead_of_using_the_cache(self):
        (self.tmp / "deployments" / "foundation.json").write_text('{"task_subnet_ids": ["a"]}', encoding="utf-8")
        os.environ["FAKE_FOUNDATION_FAIL"] = "1"
        with self.assertRaises(worker.PlanError) as cm:
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertIn("foundation 정보를 읽지 못해", str(cm.exception))
        self.assertEqual(self.backend.created_plans, [])
        self.assertFalse(any(c.startswith("up ") for c in self.calls()))

    def test_terraform_plan_failure_raises_and_registers_nothing(self):
        os.environ["FAKE_UP_RC"] = "1"
        with self.assertRaises(worker.PlanError) as cm:
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertIn("계획을 만들지 못했습니다", str(cm.exception))
        self.assertEqual(self.backend.created_plans, [])

    def test_backend_rejection_discards_unapplied_deploy_dir_so_retry_is_possible(self):
        self.backend.plan_status = 500
        with self.assertRaises(worker.ApiError):
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertFalse((self.tmp / "deployments" / "fake0001").exists())

    def test_lost_registration_response_keeps_the_plan_and_resumes_the_upload(self):
        # 서버는 등록을 끝냈는데 응답만 유실돼도 로컬 plan 파일을 지우지 않고, 서버에서 등록된 계획을 찾아 업로드를 이어간다
        self.backend.lose_plan_response = 1
        plan = worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertEqual(plan["id"], "plan-1")
        self.assertTrue((self.tmp / "deployments" / "fake0001" / "tfplan").exists())
        self.assertEqual(len(self.backend.uploads), 1)   # 같은 계획의 plan 파일이 올라갔다
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_lost_registration_response_with_unreachable_lookup_keeps_the_plan_file(self):
        # 응답이 유실됐고 등록 여부 조회까지 실패하면 등록됐는지 모른다: 파일을 남기고, 다음 점검에서 서버 목록의 계획에 올린다
        self.backend.lose_plan_response = 1
        self.backend.plans_get_failures = 1
        with self.assertRaises(worker.ApiError):
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertTrue((self.tmp / "deployments" / "fake0001" / "tfplan").exists())   # 지우지 않는다
        self.assertEqual(self.backend.uploads, [])
        # 다음 점검: 서버 목록에 등록된 계획이 보이므로 같은 파일을 올리고 새 계획을 또 만들지 않는다
        self.backend.projects = [PROJECT]
        self.backend.analyses[PROJECT["id"]] = self.analysis()
        state = worker.State(retry_after=0.0)
        worker.plan_pending(self.api, self.cfg, state)
        self.assertEqual(len(self.backend.uploads), 1)
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_server_rejection_with_4xx_discards_the_folder(self):
        self.backend.plan_status = 422
        with self.assertRaises(worker.ApiError):
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertFalse((self.tmp / "deployments" / "fake0001").exists())

    def test_discard_keeps_dirs_that_were_ever_applied(self):
        for marker in ["terraform.tfstate", "history.log", "applied.app.json"]:
            d = self.tmp / "deployments" / ("keep" + marker[:3])
            d.mkdir()
            (d / marker).write_text("x", encoding="utf-8")
            (d / "tfplan").write_text("x", encoding="utf-8")
            worker.discard_unapplied(self.cfg, d.name)
            self.assertTrue(d.exists(), marker)

    def test_missing_app_config_is_a_plan_error(self):
        with self.assertRaises(worker.PlanError):
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis({"stack": []}))
        self.assertEqual(self.calls(), [])


class ExecutorTests(Base):
    IMAGE = "123456789012.dkr.ecr.sa-east-1.amazonaws.com/paved-clouds/apps:fake0001-r1"

    def make_job(self, tamper_sha=False, deploy_id="fake0001", image=None, arch="X86_64"):
        d = self.tmp / "deployments" / "fake0001"
        d.mkdir(parents=True, exist_ok=True)
        (d / "tfplan").write_bytes(b"approved-plan")
        (self.tmp / "src.zip").write_bytes(SRC_BYTES)
        sha = hashlib.sha256(b"approved-plan").hexdigest()
        if tamper_sha:
            sha = "0" * 64
        os.environ["FAKE_BUILD_IMAGE"] = self.IMAGE
        return {"deployment_id": "dep-1", "status": "provisioning", "target": "aws", "plan_id": "plan-1", "project_id": PROJECT["id"],
                "variables": {"deploy_id": deploy_id, "image": image or self.IMAGE, "dockerfile": "sample-back/Dockerfile",
                              "source_sha256": SRC_SHA, "cpu_architecture": arch},
                "terraform_plan_sha256": sha, "source_path": str(self.tmp / "src.zip"), "source_filename": "src.zip",
                "source_sha256": SRC_SHA}

    def statuses(self):
        return [e[1]["status"] for e in self.backend.events]

    def test_success_reports_deploying_then_healthy_with_url(self):
        worker.execute_job(self.api, self.cfg, self.make_job())
        self.assertEqual(self.statuses(), ["deploying", "healthy"])
        last = self.backend.events[-1]
        self.assertEqual(last[0], "dep-1")
        self.assertEqual(last[1]["url"], "http://alb.example:8001")
        self.assertEqual([c.split()[0] for c in self.calls()], ["build", "apply"])
        build = self.calls()[0]
        self.assertIn("--tag fake0001-r1", build)
        self.assertIn("--dockerfile sample-back/Dockerfile", build)
        self.assertIn("--arch X86_64", build)

    def test_deploying_is_reported_before_health_wait_ends_once(self):
        worker.execute_job(self.api, self.cfg, self.make_job())
        self.assertEqual(self.statuses().count("deploying"), 1)

    def test_changed_source_after_approval_is_rejected_before_build(self):
        job = self.make_job()
        (self.tmp / "src.zip").write_bytes(b"edited after approval")   # 승인 뒤에 소스 파일이 바뀌었다
        worker.execute_job(self.api, self.cfg, job)
        self.assertEqual(self.statuses(), ["failed"])
        self.assertEqual(self.backend.events[0][1]["event_type"], "source_mismatch")
        self.assertEqual(self.calls(), [])   # 빌드도 적용도 하지 않는다

    def test_missing_source_file_or_digest_is_rejected(self):
        cases = {}
        job = self.make_job()
        job["variables"].pop("source_sha256")
        cases["variables에 source_sha256 없음"] = job
        job = self.make_job()
        job["source_sha256"] = "0" * 64   # 백엔드가 가진 소스 지문이 승인된 계획의 것과 다르다
        cases["작업의 source_sha256이 다름"] = job
        job = self.make_job()
        job["source_path"] = str(self.tmp / "missing.zip")
        cases["소스 파일 없음"] = job
        for name, j in cases.items():
            with self.subTest(name=name):
                self.backend.events.clear()
                worker.execute_job(self.api, self.cfg, j)
                self.assertEqual(self.statuses(), ["failed"])
                self.assertEqual(self.backend.events[0][1]["event_type"], "source_mismatch")
        self.assertEqual(self.calls(), [])

    def test_planned_architecture_is_passed_to_build(self):
        worker.execute_job(self.api, self.cfg, self.make_job(arch="ARM64"))
        self.assertIn("--arch ARM64", self.calls()[0])

    def test_invalid_architecture_in_job_fails_without_building(self):
        worker.execute_job(self.api, self.cfg, self.make_job(arch="MIPS"))
        self.assertEqual(self.backend.events[0][1]["event_type"], "invalid_job")
        self.assertEqual(self.calls(), [])

    def test_plan_hash_mismatch_never_builds_or_applies(self):
        worker.execute_job(self.api, self.cfg, self.make_job(tamper_sha=True))
        self.assertEqual(self.statuses(), ["failed"])
        self.assertEqual(self.backend.events[0][1]["event_type"], "plan_mismatch")
        self.assertEqual(self.calls(), [])

    def test_missing_plan_file_fails(self):
        job = self.make_job()
        (self.tmp / "deployments" / "fake0001" / "tfplan").unlink()
        worker.execute_job(self.api, self.cfg, job)
        self.assertEqual(self.statuses(), ["failed"])
        self.assertEqual(self.calls(), [])

    def test_invalid_deploy_id_fails_without_running_anything(self):
        for bad in ["../foundation", "UPPER123", "a", ""]:
            with self.subTest(bad=bad):
                self.backend.events.clear()
                worker.execute_job(self.api, self.cfg, self.make_job(deploy_id=bad))
                self.assertEqual(self.statuses(), ["failed"])
        self.assertEqual(self.calls(), [])

    def test_build_failure_reports_failed_with_redacted_log_and_does_not_apply(self):
        os.environ["FAKE_BUILD_RC"] = "1"
        job = self.make_job()
        os.environ["FAKE_BUILD_RC"] = "1"
        worker.execute_job(self.api, self.cfg, job)
        self.assertEqual(self.statuses(), ["failed"])
        ev = self.backend.events[0][1]
        self.assertEqual(ev["event_type"], "build_failed")
        self.assertNotIn("hunter2", json.dumps(ev))
        self.assertEqual([c.split()[0] for c in self.calls()], ["build"])

    def test_built_image_different_from_plan_is_rejected_before_apply(self):
        job = self.make_job()
        os.environ["FAKE_BUILD_IMAGE"] = "other.registry/apps:evil"
        worker.execute_job(self.api, self.cfg, job)
        self.assertEqual(self.backend.events[0][1]["event_type"], "image_mismatch")
        self.assertNotIn("apply", [c.split()[0] for c in self.calls()])

    def test_apply_failure_reports_failed_with_diagnose_and_no_auto_rollback(self):
        os.environ["FAKE_APPLY_RC"] = "1"
        job = self.make_job()
        os.environ["FAKE_APPLY_RC"] = "1"
        worker.execute_job(self.api, self.cfg, job)
        self.assertEqual(self.statuses(), ["deploying", "failed"])
        ev = self.backend.events[-1][1]
        self.assertEqual(ev["event_type"], "apply_failed")
        self.assertEqual(ev["details"]["diagnose"]["deployId"], "fake0001")
        self.assertIn("헬스체크 실패", ev["details"]["log_tail"])
        self.assertFalse(any(c.startswith("rollback") for c in self.calls()))   # 새 계획의 사용자 승인 없이 자동 롤백하지 않는다

    def test_missing_outputs_url_fails(self):
        job = self.make_job()
        orig = self.cfg.deployments_dir
        # apply는 성공하지만 outputs.json을 만들지 못하게 한다: 디렉터리로 막는다
        (orig / "fake0001" / "outputs.json").mkdir()
        worker.execute_job(self.api, self.cfg, job)
        self.assertIn("failed", self.statuses())
        self.assertNotIn("healthy", self.statuses())

    def test_transient_backend_errors_are_retried_so_results_are_not_lost(self):
        self.backend.event_failures = 2   # 첫 보고가 두 번 503이어도 재시도로 들어간다
        worker.execute_job(self.api, self.cfg, self.make_job())
        self.assertEqual(self.statuses(), ["deploying", "healthy"])

    def test_failed_deploying_report_is_resent_before_healthy(self):
        # deploying 보고가 재시도까지 모두 실패해도(4번) 읽기를 멈추지 않고, 끝난 뒤 deploying을 다시 보내고 healthy를 보낸다
        self.backend.event_failures = worker.REPORT_RETRIES
        worker.execute_job(self.api, self.cfg, self.make_job())
        self.assertEqual(self.statuses(), ["deploying", "healthy"])
        self.assertEqual(self.backend.events[-1][1]["url"], "http://alb.example:8001")

    def test_report_gives_up_on_client_errors_without_retry(self):
        class Always4xx:
            calls = 0

            def post(self, path, body=None):
                Always4xx.calls += 1
                raise worker.ApiError(409, "상태 전이 불가")
        with self.assertRaises(worker.ApiError):
            worker.report(Always4xx(), "dep-1", "healthy", "healthy", "m", url="http://x")
        self.assertEqual(Always4xx.calls, 1)

    def test_timeout_kills_child_processes_and_reports_failure(self):
        os.environ["FAKE_HANG"] = "1"
        self.cfg.apply_timeout = 2
        worker.execute_job(self.api, self.cfg, self.make_job())
        self.assertEqual(self.statuses()[-1], "failed")
        ev = self.backend.events[-1][1]
        self.assertEqual(ev["event_type"], "apply_failed")
        self.assertIn("제한 시간", ev["details"]["log_tail"])
        hb = self.tmp / "heartbeat"
        self.assertTrue(hb.exists())
        first = hb.read_text()
        time.sleep(1.2)
        self.assertEqual(hb.read_text(), first, "시간 초과 뒤에도 하위 프로세스가 계속 돌고 있다")

    def test_run_capture_timeout_raises_and_kills(self):
        t0 = time.time()
        with self.assertRaises(subprocess.TimeoutExpired):
            worker.run_capture([self.cfg.bash, "-c", "sleep 30"], 1)
        self.assertLess(time.time() - t0, 15)

    def test_token_is_not_passed_to_child_processes(self):
        env = worker.child_env(self.cfg)
        self.assertNotIn("WORKER_API_TOKEN", env)


class LoopTests(Base):
    def setUp(self):
        super().setUp()
        self.state = worker.State(retry_after=0.0)
        self.backend.projects = [PROJECT]

    def test_projects_on_later_pages_are_listed_and_planned(self):
        old = [{"id": f"old-{i}", "name": f"o{i}"} for i in range(4)]
        self.backend.pages = [old[:2], old[2:], [PROJECT]]
        self.assertEqual(len(worker.list_projects(self.api)), 5)
        for o in old:
            self.backend.plans[o["id"]] = [{"id": "p", "status": "approved", "terraform_plan_ready": True}]   # 오래된 프로젝트는 이미 계획이 있다
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(len(self.backend.created_plans), 1)
        self.assertEqual(self.backend.created_plans[0]["project_id"], PROJECT["id"])

    def test_project_without_analysis_waits(self):
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(self.backend.created_plans, [])
        self.assertEqual(self.state.failures, {})
        self.assertEqual(self.calls(), [])

    def test_project_with_existing_plan_is_skipped_and_remembered(self):
        self.backend.plans[PROJECT["id"]] = [{"id": "p", "status": "awaiting_approval", "terraform_plan_ready": True}]
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(self.backend.created_plans, [])
        self.assertIn(PROJECT["id"], self.state.done)

    def test_active_or_finished_plans_are_never_replanned(self):
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        for status in ["awaiting_approval", "approved", "consumed"]:
            with self.subTest(status=status):
                self.state.done.clear()
                self.backend.plans[PROJECT["id"]] = [{"id": "p", "status": status, "terraform_plan_ready": True}]
                worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(self.backend.created_plans, [])

    def test_project_whose_plans_are_all_superseded_is_replanned(self):
        self.backend.plans[PROJECT["id"]] = [{"id": "old1", "status": "superseded"}, {"id": "old2", "status": "superseded"}]
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(len(self.backend.created_plans), 1)   # 거절·대체된 계획만 남은 프로젝트에 새 계획을 만든다

    def test_plan_that_becomes_superseded_in_the_same_process_is_replanned(self):
        # 활성 계획이 있어 done에 들어간 프로젝트도 일정 간격마다 상태를 다시 조회해, 계획이 superseded로 바뀌면 다시 계획한다
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        self.backend.plans[PROJECT["id"]] = [{"id": "p1", "status": "awaiting_approval", "terraform_plan_ready": True}]
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertIn(PROJECT["id"], self.state.done)
        self.assertEqual(self.backend.created_plans, [])
        self.backend.plans[PROJECT["id"]] = [{"id": "p1", "status": "superseded"}]
        worker.plan_pending(self.api, self.cfg, self.state)   # 재조회 간격 안: 아직 조회하지 않는다
        self.assertEqual(self.backend.created_plans, [])
        self.state.recheck_after = 0.0                        # 간격이 지났다고 본다
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_failed_plan_upload_is_repaired_on_the_next_check(self):
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        self.backend.upload_failures = worker.REPORT_RETRIES
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(len(self.backend.created_plans), 1)
        self.assertEqual(self.backend.uploads, [])
        self.assertIn(PROJECT["id"], self.state.repair)
        worker.plan_pending(self.api, self.cfg, self.state)   # 백엔드가 회복된 뒤
        self.assertEqual(len(self.backend.uploads), 1)
        self.assertNotIn(PROJECT["id"], self.state.repair)
        self.assertEqual(len(self.backend.created_plans), 1)   # 새 계획을 또 만들지 않는다

    def test_repair_does_not_upload_a_local_file_that_differs_from_the_registered_plan(self):
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        self.backend.upload_failures = worker.REPORT_RETRIES
        worker.plan_pending(self.api, self.cfg, self.state)
        (self.tmp / "deployments" / "fake0001" / "tfplan").write_bytes(b"tampered")   # 등록된 SHA-256과 달라진다
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(self.backend.uploads, [])

    def test_unexpected_error_in_one_project_does_not_stop_the_other_projects(self):
        # 한 프로젝트의 예상 못 한 예외(예: 잘못된 분석 결과의 TypeError)가 worker 점검 전체를 중단하면 안 된다
        bad = {"id": "bad-1", "name": "bad", "source_sha256": SRC_SHA}
        self.backend.projects = [bad, PROJECT]
        self.backend.analyses["bad-1"] = {"id": "a1", "result": GOOD_RESULT}
        self.backend.analyses[PROJECT["id"]] = {"id": "a2", "result": GOOD_RESULT}
        orig = worker.plan_project

        def flaky(api, cfg, project, *a, **k):
            if project["id"] == "bad-1":
                raise TypeError("unhashable type: 'list'")
            return orig(api, cfg, project, *a, **k)
        worker.plan_project = flaky
        self.addCleanup(setattr, worker, "plan_project", orig)
        worker.plan_pending(self.api, self.cfg, self.state)   # 예외가 밖으로 나오지 않는다
        self.assertEqual([p["project_id"] for p in self.backend.created_plans], [PROJECT["id"]])   # 다른 프로젝트는 계속 계획된다
        self.assertEqual(self.state.failures["bad-1"][0], 1)

    def test_plan_created_once_for_analyzed_project(self):
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        worker.plan_pending(self.api, self.cfg, self.state)
        worker.plan_pending(self.api, self.cfg, self.state)   # 두 번째 점검은 이미 끝난 프로젝트를 건너뛴다
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_failures_are_counted_and_stop_after_max_tries(self):
        bad = json.loads(json.dumps(GOOD_RESULT))
        bad["app_config"]["container_port"] = 0
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": bad}
        for _ in range(5):
            worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(self.state.failures[PROJECT["id"]][0], 3)
        self.assertEqual(self.backend.created_plans, [])

    def test_tick_runs_approved_job_then_plans(self):
        ex = ExecutorTests("test_success_reports_deploying_then_healthy_with_url")
        ex.tmp, ex.cfg = self.tmp, self.cfg
        job = ExecutorTests.make_job(ex)
        self.backend.claims.append(job)
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        ran = worker.tick(self.api, self.cfg, self.state)
        self.assertTrue(ran)
        self.assertEqual([e[1]["status"] for e in self.backend.events], ["deploying", "healthy"])
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_tick_without_job_returns_false(self):
        self.assertFalse(worker.tick(self.api, self.cfg, self.state))

    def test_main_requires_token(self):
        os.environ.pop("WORKER_API_TOKEN", None)
        self.assertEqual(worker.main(["--once"]), 2)


class PathTests(unittest.TestCase):
    def test_posix_converts_windows_drive_paths_only_on_windows(self):
        got = worker.posix("C:/Users/x/a.zip" if os.name == "nt" else "/tmp/a.zip")
        self.assertEqual(got, "/c/Users/x/a.zip" if os.name == "nt" else "/tmp/a.zip")


if __name__ == "__main__":
    unittest.main(verbosity=1)
