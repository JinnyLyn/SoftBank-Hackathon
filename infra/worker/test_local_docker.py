"""로컬 Docker 배포(local_docker.py와 worker의 target=local_docker 경로) 시험. 표준 라이브러리만 쓴다.

실행: python -X utf8 infra/worker/test_local_docker.py
실제 Docker·AWS는 쓰지 않는다. docker는 같은 인터페이스의 가짜 스크립트로, 백엔드는 test_worker.py의 시험용 HTTP 서버로 대신하고,
앱의 헬스체크 응답은 시험 안에서 띄운 작은 HTTP 서버가 낸다. 이 시험이 통과해도 실제 Docker 데몬·실제 백엔드와의 연동이 확인된 것은 아니다.
"""
import hashlib
import json
import os
import socket
import stat
import sys
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_docker  # noqa: E402
import test_worker  # noqa: E402
import worker  # noqa: E402

# docker CLI의 가짜. 호출 인자는 한 줄 JSON으로 $FAKE_DOCKER_LOG 에 남긴다.
#   info/compose version : FAKE_INFO(기본 "8 17179869184 29.8.2"), FAKE_INFO_FAIL=1, FAKE_COMPOSE_FAIL=1
#   build                : FAKE_BUILD_RC, 받은 컨텍스트 파일 목록을 FAKE_BUILD_LOG 에 남긴다
#   compose up|run       : FAKE_UP_RC / FAKE_UP_OUT, FAKE_RUN_RC
#   compose ps           : FAKE_PS(JSON 배열 문자열, 기본 앱 running), FAKE_PS_ARRAY=1이면 한 줄 배열로
#   compose logs         : 비밀 형태의 줄과 실제 DB 비밀번호가 든 줄을 낸다(마스킹 시험용)
#   volume inspect       : FAKE_VOLUME_RC(기본 1 = 볼륨 없음)
FAKE_DOCKER = r'''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(args, ensure_ascii=False) + "\n")
env = os.environ.get
if args[:1] == ["info"]:
    if env("FAKE_INFO_FAIL"):
        print("Cannot connect to the Docker daemon", file=sys.stderr); sys.exit(1)
    print(env("FAKE_INFO", "8 17179869184 29.8.2"))
elif args[:2] == ["compose", "version"]:
    if env("FAKE_COMPOSE_FAIL"):
        print("docker: 'compose' is not a docker command", file=sys.stderr); sys.exit(1)
    print("5.5.1")
elif args[:1] == ["rmi"]:
    pass
elif args[:1] == ["build"]:
    df = args[args.index("-f") + 1]
    ctx = args[-1]
    if env("FAKE_BUILD_LOG"):
        with open(env("FAKE_BUILD_LOG"), "w", encoding="utf-8") as f:
            json.dump({"dockerfile": df, "dockerfile_exists": os.path.isfile(df), "ctx": ctx, "files": sorted(os.listdir(ctx))}, f)
    rc = int(env("FAKE_BUILD_RC", "0"))
    if rc:
        print("Step 3/7 : RUN pip install\nERROR: build failed password=hunter2", file=sys.stderr)
    sys.exit(rc)
elif args[:2] == ["image", "inspect"]:
    sys.exit(int(env("FAKE_INSPECT_RC", "0")))
elif args[:2] == ["volume", "inspect"]:
    sys.exit(int(env("FAKE_VOLUME_RC", "1")))
elif args[:1] == ["rm"]:
    pass
elif args[:1] == ["compose"]:
    i = args.index("-f")
    sub = args[i + 2]
    d = os.path.dirname(args[i + 1])
    if sub == "up":
        if env("FAKE_UP_OUT"):
            print(env("FAKE_UP_OUT"), file=sys.stderr)
        sys.exit(int(env("FAKE_UP_RC", "0")))
    elif sub == "run":
        print("init output")
        sys.exit(int(env("FAKE_RUN_RC", "0")))
    elif sub == "ps":
        items = json.loads(env("FAKE_PS", '[{"Service": "app", "State": "running", "ExitCode": 0}]'))
        if env("FAKE_PS_ARRAY"):
            print(json.dumps(items))
        else:
            for it in items:
                print(json.dumps(it))
    elif sub == "logs":
        print("app-1  | listening on 8000")
        print("app-1  | api_key=sk-abcdefghijklmnop1234")
        try:
            for line in open(os.path.join(d, "secrets", "db.env"), encoding="utf-8"):
                if line.startswith("MYSQL_PASSWORD="):
                    print("db-1   | connect user with " + line.strip().split("=", 1)[1])
        except OSError:
            pass
'''

APP = {"container_port": 8000, "health_check_path": "/health", "use_database": True,
       "environment": {"COOKIE_SECURE": "false", "NOTE": "cost $5"}, "init_command": ["python", "-m", "backend.app.initialize_database"]}
INFO = {"ncpu": 8, "mem_mb": 16384, "version": "29.8.2"}
SOURCE_SHA = hashlib.sha256(b"src").hexdigest()


def make_zip(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return Path(path)


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plan_for(host_port=18123, app=None, deploy_id="ab12cd34", source_sha=SOURCE_SHA, tier="balanced", mem_mb=16384):
    app = dict(app or APP)
    _, profile, _ = local_docker.fit_tier(tier, app["use_database"], 8, mem_mb)
    return local_docker.make_plan(deploy_id=deploy_id, image=local_docker.image_ref(deploy_id, source_sha), dockerfile="sample-back/Dockerfile", app=app,
                                  source_sha256=source_sha, tier=tier, profile=profile, reasons=["시험"], host_port=host_port, bind="127.0.0.1",
                                  public_host="localhost", docker_info={"ncpu": 8, "mem_mb": mem_mb})


class Health:
    """앱의 헬스체크 응답을 흉내 내는 서버. code를 바꾸면 응답이 바뀐다."""

    def __init__(self, code=200):
        self.code = code
        self.hits = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits += 1
                self.send_response(outer.code)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class RenderTests(unittest.TestCase):
    def params(self, **kw):
        p = {"deploy_id": "ab12cd34", "image": "pc-local/ab12cd34:s0123456789ab", "container_port": 8000, "host_port": 18123, "bind": "127.0.0.1",
             "environment": {"COOKIE_SECURE": "false"}, "use_database": True, "app_cpus": 0.5, "app_mem_mb": 512, "db_cpus": 1.0, "db_mem_mb": 1024}
        p.update(kw)
        return p

    def test_same_input_gives_the_same_text_and_digest(self):
        a, b = local_docker.render_compose(self.params()), local_docker.render_compose(self.params())
        self.assertEqual(a, b)
        self.assertEqual(local_docker.compose_digest(a), hashlib.sha256(a.encode("utf-8")).hexdigest())
        self.assertNotEqual(a, local_docker.render_compose(self.params(host_port=18124)))

    def test_compose_has_no_secret_and_no_host_access(self):
        text = local_docker.render_compose(self.params())
        for bad in ("password", "DATABASE_URL", "privileged", "docker.sock", "network_mode", "pid:", "cap_add", "devices:"):
            self.assertNotIn(bad.lower(), text.lower())
        # 호스트 경로 마운트가 없다: volumes는 이름 있는 볼륨(db-data) 하나뿐
        self.assertEqual([ln.strip() for ln in text.splitlines() if ln.strip().startswith("- ") and ":/" in ln], ['- "db-data:/var/lib/mysql"'])
        self.assertIn("./secrets/app.env", text)   # 비밀은 파일로만 넘긴다
        self.assertIn('"127.0.0.1:18123:8000"', text)
        self.assertIn("pull_policy: never", text)

    def test_dollar_in_environment_value_is_escaped(self):
        text = local_docker.render_compose(self.params(environment={"NOTE": "cost $5 ${HOME}"}))
        self.assertIn('NOTE: "cost $$5 $${HOME}"', text)

    def test_without_database_there_is_no_db_service_or_env_file(self):
        text = local_docker.render_compose(self.params(use_database=False))
        for absent in ("db:", "mysql", "env_file", "depends_on", "volumes", "secrets"):
            self.assertNotIn(absent, text)

    def test_invalid_inputs_are_rejected(self):
        bad = [
            {"deploy_id": "AB"}, {"deploy_id": "../x"}, {"image": "evil:latest"}, {"image": "pc-local/zzzzzzzz:s1"},
            {"container_port": 0}, {"container_port": True}, {"host_port": 70000}, {"bind": "0.0.0.0/0"}, {"bind": "300.1.1.1"},
            {"environment": {"lower": "x"}}, {"environment": {"DATABASE_URL": "x"}}, {"environment": {"A": 1}},
            {"app_cpus": 0}, {"app_cpus": True}, {"app_mem_mb": 1}, {"db_image": "mysql:latest"},
        ]
        for kw in bad:
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                local_docker.render_compose(self.params(**kw))

    def test_environment_keys_are_sorted_so_the_text_does_not_depend_on_order(self):
        a = local_docker.render_compose(self.params(environment={"B": "1", "A": "2"}))
        b = local_docker.render_compose(self.params(environment={"A": "2", "B": "1"}))
        self.assertEqual(a, b)


class PlanHelperTests(unittest.TestCase):
    def test_tier_follows_users_and_peak(self):
        self.assertEqual(local_docker.recommend_tier("~100", "steady")[0], "lean")
        self.assertEqual(local_docker.recommend_tier("~1,000", "steady")[0], "balanced")
        self.assertEqual(local_docker.recommend_tier("~100", "peak")[0], "balanced")
        self.assertEqual(local_docker.recommend_tier("10,000+", "peak")[0], "roomy")
        self.assertEqual(local_docker.recommend_tier(None, None)[0], "balanced")

    def test_tier_is_lowered_to_fit_docker_memory_and_cpu_is_capped(self):
        t, prof, why = local_docker.fit_tier("roomy", True, 4, 16384)
        self.assertEqual(t, "roomy")
        self.assertEqual((prof["app_cpus"], prof["db_cpus"]), (2.0, 2.0))
        t, prof, why = local_docker.fit_tier("roomy", True, 4, 4096)   # 4096 x 0.7 = 2867 < 4096(roomy) → balanced(2048)
        self.assertEqual(t, "balanced")
        self.assertTrue(why and "낮춤" in why[0])
        t, prof, _ = local_docker.fit_tier("balanced", False, 1, 8192)   # CPU가 1개뿐이면 한도를 1로 줄인다(넘으면 컨테이너 생성이 거부된다)
        self.assertEqual(prof["app_cpus"], 1.0)
        with self.assertRaises(local_docker.LocalError) as cm:
            local_docker.fit_tier("lean", True, 4, 1024)
        self.assertEqual(cm.exception.event_type, "capacity")

    def test_deploy_id_is_stable_and_valid(self):
        a = local_docker.local_deploy_id("11111111-1111-1111-1111-111111111111")
        self.assertEqual(a, local_docker.local_deploy_id("11111111-1111-1111-1111-111111111111"))
        self.assertRegex(a, r"^[a-z0-9]{8}$")
        self.assertNotEqual(a, local_docker.local_deploy_id("22222222-2222-2222-2222-222222222222"))

    def test_port_is_in_range_stable_and_skips_busy_or_taken_ports(self):
        p = local_docker.pick_host_port("ab12cd34")
        self.assertTrue(local_docker.PORT_MIN <= p <= local_docker.PORT_MAX)
        self.assertEqual(p, local_docker.pick_host_port("ab12cd34"))
        self.assertNotEqual(p, local_docker.pick_host_port("ab12cd34", taken=(p,)))
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))   # 이미 쓰는 포트
            self.assertNotEqual(p, local_docker.pick_host_port("ab12cd34"))
        finally:
            s.close()

    def test_make_plan_shape_and_roundtrip(self):
        plan = plan_for()
        v = plan["variables"]
        self.assertEqual(plan["digest"], local_docker.compose_digest(v["compose_yaml"]))
        self.assertEqual(local_docker.render_compose(local_docker.params_from_variables(v)), v["compose_yaml"])   # 변수에서 다시 만들어도 같다
        self.assertEqual(plan["cost_estimate"]["amount"], "0.0000")
        self.assertEqual(v["cost"]["total_monthly"], 0)
        self.assertTrue(v["cost"]["excluded"])   # 제외 비용을 밝힌다
        self.assertEqual(v["connection_name"], local_docker.CONNECTION_NAME)
        self.assertEqual(local_docker.public_url(v), "http://localhost:18123")
        self.assertIn("추가 비용 없음", plan["summary"])
        self.assertEqual([r["service"] for r in v["resources"]], ["앱 컨테이너", "MySQL 컨테이너"])
        self.assertEqual(plan_for(app={**APP, "use_database": False})["variables"]["resources"][0]["service"], "앱 컨테이너")
        # 승인 화면 데이터에 비밀이 없다
        blob = json.dumps(plan, ensure_ascii=False).lower()
        self.assertNotIn("password", blob)
        self.assertNotIn("database_url", blob)

    def test_public_url_rejects_bad_host_or_port(self):
        v = plan_for()["variables"]
        for k, val in (("public_host", "a b"), ("public_host", "x/y"), ("public_host", ""), ("host_port", 0), ("host_port", True)):
            bad = json.loads(json.dumps(v))
            bad["local"][k] = val
            with self.subTest(k=k, val=val), self.assertRaises(ValueError):
                local_docker.public_url(bad)


class ZipTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        import shutil
        self.tmp = Path(tempfile.mkdtemp(prefix="ld-zip-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_single_top_folder_is_the_base(self):
        z = make_zip(self.tmp / "a.zip", {"repo-main/Dockerfile": "FROM x", "repo-main/app/main.py": "print(1)"})
        base = local_docker.extract_zip(z, self.tmp / "out")
        self.assertEqual(base.name, "repo-main")
        self.assertTrue((base / "app" / "main.py").is_file())

    def test_several_top_level_entries_use_the_root(self):
        z = make_zip(self.tmp / "a.zip", {"Dockerfile": "FROM x", "app/main.py": "print(1)"})
        base = local_docker.extract_zip(z, self.tmp / "out")
        self.assertTrue((base / "Dockerfile").is_file())

    def test_zip_permissions_restore_only_execute_bits(self):
        modes = {"entrypoint.sh": 0o755, "owner-only.sh": 0o700, "privileged.sh": 0o7777, "data.txt": 0o666}
        archive = self.tmp / "modes.zip"
        with zipfile.ZipFile(archive, "w") as z:
            for name, mode in modes.items():
                info = zipfile.ZipInfo("repo/" + name)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | mode) << 16
                z.writestr(info, "#!/bin/sh\nexit 0\n")
        baseline = self.tmp / "baseline"
        baseline.write_text("x", encoding="utf-8")
        base_mode = stat.S_IMODE(baseline.stat().st_mode)
        real_chmod = os.chmod
        with mock.patch.object(local_docker.os, "chmod", wraps=real_chmod) as chmod:
            root = local_docker.extract_zip(archive, self.tmp / "out")
        for name, mode in modes.items():
            with self.subTest(name=name):
                path = root / name
                expected = base_mode | (mode & 0o111)
                self.assertEqual(path.read_text(encoding="utf-8"), "#!/bin/sh\nexit 0\n")
                if mode & 0o111:
                    chmod.assert_any_call(str(path), expected)
                if os.name != "nt":  # Windows chmod는 POSIX 실행 비트를 보존하지 않는다
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected)
        self.assertEqual(chmod.call_count, 3)

    def test_dangerous_archives_are_rejected(self):
        evil = {"parent": {"../evil.txt": "x"}, "absolute": {"/etc/x": "x"}, "drive": {"C:/x": "x"}, "backslash": {"a\\..\\..\\b": "x"}}
        for name, files in evil.items():
            with self.subTest(name=name), self.assertRaises(local_docker.LocalError) as cm:
                local_docker.extract_zip(make_zip(self.tmp / f"{name}.zip", files), self.tmp / f"out-{name}")
            self.assertEqual(cm.exception.event_type, "source_invalid")
        self.assertFalse((self.tmp / "evil.txt").exists())

    def test_symlink_is_rejected(self):
        p = self.tmp / "link.zip"
        with zipfile.ZipFile(p, "w") as z:
            info = zipfile.ZipInfo("link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "/etc/passwd")
        with self.assertRaises(local_docker.LocalError):
            local_docker.extract_zip(p, self.tmp / "out")

    def test_limits(self):
        z = make_zip(self.tmp / "many.zip", {f"f{i}": "x" for i in range(5)})
        with mock.patch.object(local_docker, "MAX_FILES", 3), self.assertRaises(local_docker.LocalError):
            local_docker.extract_zip(z, self.tmp / "o1")
        big = make_zip(self.tmp / "bomb.zip", {"zeros.bin": b"\0" * (4 << 20)})   # 압축률이 200배를 넘는다
        with self.assertRaises(local_docker.LocalError) as cm:
            local_docker.extract_zip(big, self.tmp / "o2")
        self.assertIn("압축률", cm.exception.message)
        with mock.patch.dict(os.environ, {"ZIP_MAX_BYTES": "10"}), self.assertRaises(local_docker.LocalError):
            local_docker.extract_zip(make_zip(self.tmp / "b.zip", {"a": "x" * 50}), self.tmp / "o3")

    def test_not_a_zip(self):
        p = self.tmp / "no.zip"
        p.write_bytes(b"not a zip")
        with self.assertRaises(local_docker.LocalError):
            local_docker.extract_zip(p, self.tmp / "o")


class HealthAddressTests(unittest.TestCase):
    def test_probe_uses_bind_address_and_wildcard_uses_loopback(self):
        for bind, host in (("127.0.0.1", "127.0.0.1"), ("127.0.0.2", "127.0.0.2"),
                           ("192.168.1.10", "192.168.1.10"), ("0.0.0.0", "127.0.0.1")):
            with self.subTest(bind=bind), mock.patch.object(local_docker, "probe", return_value=200) as probe:
                local_docker.wait_healthy(local_docker.LocalConfig(bind=bind), "ab12cd34", 18123, "/health")
                probe.assert_called_once_with(f"http://{host}:18123/health")


class DockerBase(unittest.TestCase):
    """가짜 docker와 임시 배포 폴더를 준비한다."""

    def setUp(self):
        import tempfile
        import shutil
        self.tmp = Path(tempfile.mkdtemp(prefix="ld-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = self.tmp / "fake_docker.py"
        self.fake.write_text(FAKE_DOCKER, encoding="utf-8")
        self.log = self.tmp / "docker.log"
        self.old_env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.old_env)))
        for k in list(os.environ):
            if k.startswith("FAKE_"):
                del os.environ[k]
        os.environ["FAKE_DOCKER_LOG"] = str(self.log)
        os.environ["WORKER_API_TOKEN"] = "must-not-reach-docker"
        self.lc = local_docker.LocalConfig(docker=[sys.executable, str(self.fake)], root=self.tmp / "local", health_timeout=3, poll_seconds=0.02,
                                           build_timeout=60, up_timeout=60, init_timeout=60)

    def calls(self):
        return [json.loads(ln) for ln in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def subcommands(self):
        """compose 하위 명령 이름들과 그 밖의 docker 명령 이름들(호출 순서대로)."""
        out = []
        for a in self.calls():
            out.append(a[a.index("-f") + 2] if a[:1] == ["compose"] and "-f" in a else " ".join(a[:2] if a[:1] in (["image"], ["volume"], ["compose"]) else a[:1]))
        return out


class ApplyTests(DockerBase):
    def setUp(self):
        super().setUp()
        self.health = Health(200)
        self.addCleanup(self.health.close)
        self.plan = plan_for(host_port=self.health.port)
        self.text = self.plan["variables"]["compose_yaml"]

    def apply(self, **kw):
        args = dict(host_port=self.health.port, health_path="/health", init_command=APP["init_command"], use_database=True)
        args.update(kw)
        return local_docker.apply(self.lc, "ab12cd34", self.text, **args)

    def test_docker_info_reads_resources_and_errors(self):
        self.assertEqual(local_docker.docker_info(self.lc), {"ncpu": 8, "mem_mb": 16384, "version": "29.8.2"})
        os.environ["FAKE_INFO_FAIL"] = "1"
        with self.assertRaises(local_docker.LocalError) as cm:
            local_docker.docker_info(self.lc)
        self.assertEqual(cm.exception.event_type, "docker_unavailable")
        del os.environ["FAKE_INFO_FAIL"]
        os.environ["FAKE_COMPOSE_FAIL"] = "1"
        with self.assertRaises(local_docker.LocalError) as cm:
            local_docker.docker_info(self.lc)
        self.assertEqual(cm.exception.event_type, "compose_missing")

    def test_missing_docker_executable_is_reported(self):
        self.lc.docker = [str(self.tmp / "no-such-docker")]
        with self.assertRaises(local_docker.LocalError) as cm:
            local_docker.docker_info(self.lc)
        self.assertEqual(cm.exception.event_type, "docker_missing")

    def test_build_image_extracts_builds_and_cleans_up(self):
        zp = make_zip(self.tmp / "src.zip", {"repo/sample-back/Dockerfile": "FROM x", "repo/sample-back/app.py": "x"})
        os.environ["FAKE_BUILD_LOG"] = str(self.tmp / "build.json")
        image = self.plan["variables"]["image"]
        local_docker.build_image(self.lc, zp, "sample-back/Dockerfile", image)
        info = json.loads((self.tmp / "build.json").read_text(encoding="utf-8"))
        self.assertTrue(info["dockerfile_exists"])
        self.assertEqual(info["files"], ["sample-back"])
        self.assertFalse(Path(info["ctx"]).exists())   # 압축을 푼 소스는 빌드 뒤 지운다
        self.assertEqual(self.subcommands()[:3], ["rmi", "build", "image inspect"])
        build = next(a for a in self.calls() if a[0] == "build")
        self.assertIn(image, build)
        self.assertNotIn("push", self.subcommands())   # 어디에도 올리지 않는다

    def test_build_failure_is_reported_with_masked_log(self):
        os.environ["FAKE_BUILD_RC"] = "1"
        zp = make_zip(self.tmp / "src.zip", {"Dockerfile": "FROM x"})
        with self.assertRaises(local_docker.LocalError) as cm:
            local_docker.build_image(self.lc, zp, "Dockerfile", self.plan["variables"]["image"])
        e = cm.exception
        self.assertEqual(e.event_type, "build_failed")
        self.assertIn("pip install", e.details["log_tail"])
        self.assertNotIn("hunter2", e.details["log_tail"])

    def test_build_rejects_bad_dockerfile_path_and_missing_dockerfile(self):
        zp = make_zip(self.tmp / "src.zip", {"Dockerfile": "FROM x"})
        for df in ("../Dockerfile", "/etc/passwd", ""):
            with self.subTest(df=df), self.assertRaises(local_docker.LocalError) as cm:
                local_docker.build_image(self.lc, zp, df, self.plan["variables"]["image"])
            self.assertEqual(cm.exception.event_type, "invalid_job")
        with self.assertRaises(local_docker.LocalError) as cm:
            local_docker.build_image(self.lc, zp, "sub/Dockerfile", self.plan["variables"]["image"])
        self.assertEqual(cm.exception.event_type, "build_failed")
        self.assertEqual(self.calls(), [])   # Dockerfile이 없으면 docker를 부르지 않는다

    def test_build_rejects_windows_and_posix_host_paths_before_extraction(self):
        paths = (r"C:\host\Dockerfile", "C:/host/Dockerfile", "C:Dockerfile",
                 r"\\server\share\Dockerfile", r"\host\Dockerfile", "/host/Dockerfile",
                 "../Dockerfile", r"app\..\Dockerfile", "app/../Dockerfile")
        with mock.patch.object(local_docker, "extract_zip") as extract:
            for dockerfile in paths:
                with self.subTest(dockerfile=dockerfile):
                    with self.assertRaises(local_docker.LocalError) as cm:
                        local_docker.build_image(self.lc, self.tmp / "src.zip", dockerfile, self.plan["variables"]["image"])
                    self.assertEqual(cm.exception.event_type, "invalid_job")
            extract.assert_not_called()
        self.assertEqual(self.calls(), [])

    def test_build_checks_resolved_dockerfile_stays_inside_context(self):
        ctx = self.tmp / "context"
        ctx.mkdir()
        (ctx / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
        outside = self.tmp / "context-other" / "Dockerfile"
        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            # 플랫폼별 symlink 생성 권한 없이 경로 해석 결과가 외부인 경우를 검증한다.
            return outside if path == ctx / "Dockerfile" else original_resolve(path, *args, **kwargs)

        with (
            mock.patch.object(local_docker, "extract_zip", return_value=ctx),
            mock.patch.object(Path, "resolve", resolve),
            self.assertRaises(local_docker.LocalError) as cm,
        ):
            local_docker.build_image(self.lc, self.tmp / "src.zip", "Dockerfile", self.plan["variables"]["image"])
        self.assertEqual(cm.exception.event_type, "invalid_job")
        self.assertEqual(self.calls(), [])

    def test_build_normalizes_safe_nested_dockerfile_paths(self):
        zp = make_zip(self.tmp / "src.zip", {"repo/app/Dockerfile": "FROM scratch"})
        for dockerfile in ("app/./Dockerfile", r"app\Dockerfile"):
            with self.subTest(dockerfile=dockerfile):
                local_docker.build_image(self.lc, zp, dockerfile, self.plan["variables"]["image"])
                build = [args for args in self.calls() if args[0] == "build"][-1]
                ctx = Path(build[-1])
                self.assertEqual(Path(build[build.index("-f") + 1]), ctx / "app" / "Dockerfile")

    def test_apply_success_writes_compose_and_private_secrets(self):
        seen = []
        self.apply(on_deploying=lambda: seen.append(self.subcommands()[:]))
        d = self.lc.root / "ab12cd34"
        self.assertEqual((d / "docker-compose.yml").read_text(encoding="utf-8"), self.text)
        db_env = (d / "secrets" / "db.env").read_text(encoding="utf-8")
        app_env = (d / "secrets" / "app.env").read_text(encoding="utf-8")
        pw = dict(ln.split("=", 1) for ln in db_env.splitlines())["MYSQL_PASSWORD"]
        self.assertGreaterEqual(len(pw), 24)
        self.assertEqual(app_env.strip(), f"DATABASE_URL=mysql://app_ab12cd34:{pw}@db:3306/app_ab12cd34")   # DB 이름·사용자는 이 배포 전용
        self.assertNotIn(pw, (d / "docker-compose.yml").read_text(encoding="utf-8"))   # 비밀은 compose 본문에 없다
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE((d / "secrets" / "db.env").stat().st_mode), 0o600)
        # 순서: up → 초기화 → (deploying 알림) → 헬스체크(ps는 헬스체크 중에만 부른다)
        self.assertEqual(seen, [["volume inspect", "up", "rm", "run"]])
        up = next(a for a in self.calls() if a[:1] == ["compose"] and a[a.index("-f") + 2] == "up")
        self.assertEqual(up[:4], ["compose", "-p", "pc-ab12cd34", "-f"])
        run = next(a for a in self.calls() if a[:1] == ["compose"] and a[a.index("-f") + 2] == "run")
        self.assertEqual(run[run.index("run") + 1:], ["--rm", "-T", "--no-deps", "--name", "pc-ab12cd34-init", "app", "python", "-m", "backend.app.initialize_database"])
        self.assertTrue((d / "history.log").read_text(encoding="utf-8").strip().endswith("|" + local_docker.compose_digest(self.text)))
        self.assertGreaterEqual(self.health.hits, 1)

    def test_docker_does_not_get_worker_token_or_aws_keys(self):
        os.environ["AWS_SECRET_ACCESS_KEY"] = "aws-secret-should-not-pass"
        env = local_docker.docker_env()
        self.assertNotIn("WORKER_API_TOKEN", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)

    def test_secrets_are_kept_across_redeploys(self):
        self.apply()
        first = (self.lc.root / "ab12cd34" / "secrets" / "db.env").read_text(encoding="utf-8")
        self.apply()
        self.assertEqual((self.lc.root / "ab12cd34" / "secrets" / "db.env").read_text(encoding="utf-8"), first)   # 볼륨은 남으니 비밀번호도 그대로여야 한다

    def test_volume_without_secrets_is_not_reused(self):
        os.environ["FAKE_VOLUME_RC"] = "0"   # 볼륨이 남아 있다
        with self.assertRaises(local_docker.LocalError) as cm:
            self.apply()
        self.assertEqual(cm.exception.event_type, "secrets_missing")
        self.assertNotIn("up", self.subcommands())   # 컨테이너를 띄우지 않는다
        self.assertFalse((self.lc.root / "ab12cd34" / "secrets" / "db.env").exists())

    def test_without_database_no_secrets_and_no_volume_check(self):
        plan = plan_for(host_port=self.health.port, app={**APP, "use_database": False})
        local_docker.apply(self.lc, "ab12cd34", plan["variables"]["compose_yaml"], host_port=self.health.port, health_path="/health", use_database=False)
        self.assertFalse((self.lc.root / "ab12cd34" / "secrets" / "db.env").exists())
        self.assertNotIn("volume inspect", self.subcommands())

    def test_up_failure_stops_containers_and_explains_busy_port(self):
        os.environ["FAKE_UP_RC"] = "1"
        os.environ["FAKE_UP_OUT"] = "Bind for 127.0.0.1:18123 failed: port is already allocated"
        with self.assertRaises(local_docker.LocalError) as cm:
            self.apply()
        self.assertEqual(cm.exception.event_type, "apply_failed")
        self.assertIn(f"호스트 포트 {self.health.port}", cm.exception.message)
        self.assertEqual(self.subcommands()[-1], "stop")   # 재시작 루프가 PC 자원을 쓰지 않게 멈춘다. 지우지는 않는다
        self.assertNotIn("down", self.subcommands())

    def test_init_failure_removes_the_init_container_and_stops(self):
        os.environ["FAKE_RUN_RC"] = "3"
        with self.assertRaises(local_docker.LocalError) as cm:
            self.apply()
        self.assertEqual(cm.exception.event_type, "init_failed")
        self.assertIn("종료 코드 3", cm.exception.message)
        self.assertEqual(self.subcommands()[-2:], ["rm", "stop"])
        self.assertEqual(self.health.hits, 0)   # 초기화가 실패하면 헬스체크까지 가지 않는다

    def test_health_failures(self):
        cases = [
            ({"code": 404}, {}, "health_failed"),                                           # 4xx가 4번 연속
            ({"code": 503}, {"FAKE_PS": '[{"Service": "app", "State": "exited", "ExitCode": 1}]'}, "container_exited"),
            ({"code": 503}, {"FAKE_PS": '[{"Service": "app", "State": "restarting", "ExitCode": 1}]'}, "container_restarting"),
            ({"code": 503}, {}, "health_timeout"),                                          # 5xx는 계속 기다리다 시간 초과
        ]
        for health, env, expected in cases:
            with self.subTest(expected=expected):
                self.health.code = health["code"]
                os.environ.update(env)
                self.addCleanup(lambda env=env: [os.environ.pop(k, None) for k in env])   # 한 경우가 실패해도 다음 경우로 새지 않게 한다
                self.lc.health_timeout = 0.4
                try:
                    with self.assertRaises(local_docker.LocalError) as cm:
                        self.apply(init_command=None)
                    names = self.subcommands()
                    self.assertEqual(cm.exception.event_type, expected)
                    self.assertEqual(names[-1], "stop")   # 진단(상태·로그 조회) 뒤에 컨테이너를 멈춘다. 지우지는 않는다
                    self.assertIn("logs", names)
                    self.assertNotIn("down", names)
                    self.assertEqual(cm.exception.details["diagnose"]["deployId"], "ab12cd34")
                finally:
                    for k in env:
                        os.environ.pop(k, None)
                    self.log.unlink(missing_ok=True)

    def test_5xx_then_ok_passes(self):
        self.health.code = 503
        threading.Timer(0.3, lambda: setattr(self.health, "code", 200)).start()
        self.lc.health_timeout = 5
        self.apply(init_command=None)

    def test_service_states_accepts_json_lines_and_array(self):
        self.assertEqual(local_docker.service_states(self.lc, "ab12cd34"), {"app": ("running", 0)})
        os.environ["FAKE_PS_ARRAY"] = "1"
        os.environ["FAKE_PS"] = '[{"Service": "app", "State": "running", "ExitCode": 0}, {"Service": "db", "State": "exited", "ExitCode": 137}]'
        self.assertEqual(local_docker.service_states(self.lc, "ab12cd34"), {"app": ("running", 0), "db": ("exited", 137)})

    def test_diagnose_masks_secrets_including_the_generated_db_password(self):
        self.apply()
        pw = dict(ln.split("=", 1) for ln in (self.lc.root / "ab12cd34" / "secrets" / "db.env").read_text(encoding="utf-8").splitlines())["MYSQL_PASSWORD"]
        diag = local_docker.diagnose(self.lc, "ab12cd34")
        self.assertIn("listening on 8000", diag["log_tail"])
        self.assertNotIn(pw, diag["log_tail"])
        self.assertNotIn("sk-abcdefghijklmnop1234", diag["log_tail"])
        self.assertIn("[가림]", diag["log_tail"])

    def test_destroy_keeps_data_unless_asked(self):
        self.apply()
        local_docker.destroy(self.lc, "ab12cd34")
        down = next(a for a in self.calls() if a[:1] == ["compose"] and a[a.index("-f") + 2] == "down")
        self.assertNotIn("--volumes", down)
        self.assertTrue((self.lc.root / "ab12cd34" / "secrets" / "db.env").exists())
        local_docker.destroy(self.lc, "ab12cd34", drop_data=True)
        downs = [a for a in self.calls() if a[:1] == ["compose"] and a[a.index("-f") + 2] == "down"]
        self.assertIn("--volumes", downs[-1])
        self.assertFalse((self.lc.root / "ab12cd34").exists())
        with self.assertRaises(local_docker.LocalError):
            local_docker.destroy(self.lc, "ab12cd34")   # 이미 지워졌다
        with self.assertRaises(local_docker.LocalError):
            local_docker.destroy(self.lc, "../x")

    def test_list_deployments(self):
        self.assertEqual(local_docker.list_deployments(self.lc), [])
        self.apply()
        self.assertEqual(local_docker.list_deployments(self.lc), ["ab12cd34"])


class WorkerBase(test_worker.Base):
    """test_worker.Base(가짜 백엔드·가짜 deploy.sh·가짜 aws)에 가짜 docker를 더한다."""

    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "fake_docker.py"
        self.fake.write_text(FAKE_DOCKER, encoding="utf-8")
        self.docker_log = self.tmp / "docker.log"
        for k in list(os.environ):
            if k.startswith("FAKE_") and k not in ("FAKE_LOG", "FAKE_DEPLOYMENTS", "FAKE_HEARTBEAT", "FAKE_AWS_LOG"):
                del os.environ[k]
        os.environ["FAKE_DOCKER_LOG"] = str(self.docker_log)
        self.cfg.targets = ("aws", "local_docker")
        self.cfg.local = local_docker.LocalConfig(docker=[sys.executable, str(self.fake)], root=self.tmp / "local", health_timeout=3, poll_seconds=0.02,
                                                  build_timeout=60, up_timeout=60, init_timeout=60)
        self.health = Health(200)
        self.addCleanup(self.health.close)

    def docker_calls(self):
        return [json.loads(ln) for ln in self.docker_log.read_text(encoding="utf-8").splitlines()] if self.docker_log.exists() else []

    def sub(self, a):
        return a[a.index("-f") + 2] if a[:1] == ["compose"] and "-f" in a else a[0]

    def seed_project(self, pid=test_worker.PROJECT["id"], source_sha=test_worker.SRC_SHA, result=None):
        project = {**test_worker.PROJECT, "id": pid, "source_sha256": source_sha}
        self.backend.projects = [project]
        self.backend.analyses[pid] = {"id": "analysis-1", "result": result or test_worker.GOOD_RESULT}
        return project


class LocalPlannerTests(WorkerBase):
    def test_plan_is_registered_without_touching_aws_or_deploy_sh(self):
        project = self.seed_project()
        worker.plan_pending_local(self.api, self.cfg, worker.State())
        self.assertEqual(len(self.backend.created_plans), 1)
        body = self.backend.created_plans[0]
        self.assertEqual((body["target"], body["module_id"]), ("local_docker", "local-docker-compose"))
        v = body["variables"]
        self.assertEqual(body["terraform_plan_sha256"], local_docker.compose_digest(v["compose_yaml"]))
        self.assertEqual(v["deploy_id"], local_docker.local_deploy_id(project["id"]))
        self.assertEqual(v["source_sha256"], project["source_sha256"])
        self.assertEqual(v["dockerfile"], "sample-back/Dockerfile")
        self.assertEqual(v["tier"], "balanced")   # ~1,000명, 접속이 고르다
        self.assertEqual(body["cost_estimate"]["amount"], "0.0000")
        self.assertNotIn("task_size", v["app"])   # Fargate 태스크 설정은 없다
        self.assertEqual(self.calls(), [])        # deploy.sh(bash·terraform) 호출 없음
        self.assertEqual(self.aws_calls(), [])    # aws CLI 호출 없음
        self.assertEqual(self.backend.uploads, [])   # plan 파일 업로드 없음(본문은 변수에 있다)
        # 이미지를 만들거나 컨테이너를 띄우지 않는다(승인 전). docker는 info·compose version만 불렀다
        self.assertEqual({self.sub(a) for a in self.docker_calls()}, {"info", "compose"})

    def test_dockerfile_draft_goes_into_local_plan_variables(self):
        res = json.loads(json.dumps(test_worker.GOOD_RESULT))
        res["dockerfile"] = None
        res["dockerfile_draft"] = {"path": "Dockerfile", "content": "FROM python:3.12-slim\nCMD [\"python\"]\n", "port": 8000, "based_on": []}
        self.seed_project(result=res)
        worker.plan_pending_local(self.api, self.cfg, worker.State())
        v = self.backend.created_plans[0]["variables"]
        self.assertIsNone(v["dockerfile"])
        self.assertEqual(v["dockerfile_draft"]["sha256"], hashlib.sha256(res["dockerfile_draft"]["content"].encode()).hexdigest())

    def test_budget_does_not_block_a_free_target(self):
        project = self.seed_project()
        project["monthly_budget_usd"] = "0.0000"
        worker.plan_pending_local(self.api, self.cfg, worker.State())
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_tier_is_lowered_when_docker_memory_is_small(self):
        self.seed_project()
        os.environ["FAKE_INFO"] = "2 2147483648 29.8.2"   # 2GB의 70%는 1433MB. 가장 작은 구성(앱 512MB + DB 1GB = 1536MB)도 못 담는다
        state = worker.State()
        worker.plan_pending_local(self.api, self.cfg, state)
        self.assertEqual(self.backend.created_plans, [])
        self.assertEqual(state.local_failures[test_worker.PROJECT["id"]][0], 1)
        os.environ["FAKE_INFO"] = "2 2684354560 29.8.2"   # 2.5GB의 70%는 1792MB: balanced(2048MB)는 못 담고 lean(1536MB)은 담는다
        state2 = worker.State()
        worker.plan_pending_local(self.api, self.cfg, state2)
        v = self.backend.created_plans[0]["variables"]
        self.assertEqual(v["tier"], "lean")
        self.assertIn("낮춤", v["reason"])

    def test_docker_down_is_a_counted_failure_not_a_crash(self):
        self.seed_project()
        os.environ["FAKE_INFO_FAIL"] = "1"
        state = worker.State()
        worker.plan_pending_local(self.api, self.cfg, state)
        self.assertEqual(self.backend.created_plans, [])
        self.assertEqual(state.local_failures[test_worker.PROJECT["id"]][0], 1)

    def test_existing_local_plan_or_a_decision_skips(self):
        pid = self.seed_project()["id"]
        for plans in (
            [{"status": "awaiting_approval", "target": "local_docker"}],
            [{"status": "consumed", "target": "local_docker"}],
            [{"status": "consumed", "target": "aws"}],            # AWS로 이미 배포한 프로젝트
            [{"status": "approved", "target": "aws"}],
        ):
            with self.subTest(plans=plans):
                self.backend.plans = {pid: plans}
                worker.plan_pending_local(self.api, self.cfg, worker.State())
                self.assertEqual(self.backend.created_plans, [])

    def test_both_targets_are_planned_when_only_the_other_awaits(self):
        pid = self.seed_project()["id"]
        self.backend.plans = {pid: [{"status": "awaiting_approval", "target": "aws"}, {"status": "superseded", "target": "local_docker"}]}
        worker.plan_pending_local(self.api, self.cfg, worker.State())
        self.assertEqual([b["target"] for b in self.backend.created_plans], ["local_docker"])

    def test_aws_planner_ignores_local_plans_but_respects_a_local_decision(self):
        pid = self.seed_project()["id"]
        os.environ["FAKE_FOUNDATION_JSON"] = "{}"
        self.backend.plans = {pid: [{"status": "awaiting_approval", "target": "local_docker"}]}
        worker.plan_pending(self.api, self.cfg, worker.State())
        self.assertTrue(any(c.startswith("up ") for c in self.calls()))   # 로컬 계획이 있어도 AWS 계획은 만든다
        self.calls_before = len(self.calls())
        self.backend.plans = {pid: [{"status": "consumed", "target": "local_docker"}]}
        self.backend.created_plans.clear()
        before = len(self.calls())
        worker.plan_pending(self.api, self.cfg, worker.State())
        self.assertEqual(len(self.calls()), before)   # 로컬로 이미 배포했으면 AWS 계획을 새로 만들지 않는다
        self.assertEqual(self.backend.created_plans, [])

    def test_aws_reservation_ignores_local_plans(self):
        self.seed_project()
        other = {"id": "22222222-2222-2222-2222-222222222222", "name": "other", "source_sha256": test_worker.SRC_SHA}
        self.backend.projects = [test_worker.PROJECT, other]
        self.backend.plans = {other["id"]: [{"status": "awaiting_approval", "target": "local_docker", "variables": {"app": {"task_size": "large", "max_tasks": 9}}}]}
        self.assertEqual(worker.reserved_vcpu(self.api, test_worker.PROJECT["id"]), 0.0)
        self.backend.plans = {other["id"]: [{"status": "awaiting_approval", "target": "aws", "variables": {"app": {"task_size": "large", "max_tasks": 2}}}]}
        self.assertEqual(worker.reserved_vcpu(self.api, test_worker.PROJECT["id"]), 4.0)

    def test_lost_registration_response_does_not_double_register(self):
        self.seed_project()
        self.backend.lose_plan_response = 1   # 서버는 등록했지만 응답이 끊긴다
        worker.plan_pending_local(self.api, self.cfg, worker.State())
        self.assertEqual(len(self.backend.created_plans), 1)
        self.assertEqual(len(self.backend.plans[test_worker.PROJECT["id"]]), 1)

    def test_server_rejection_is_a_counted_failure(self):
        self.seed_project()
        self.backend.plan_status = 422
        state = worker.State()
        worker.plan_pending_local(self.api, self.cfg, state)
        self.assertEqual(state.local_failures[test_worker.PROJECT["id"]][0], 1)

    def test_no_analysis_yet_waits(self):
        self.seed_project()
        self.backend.analyses = {}
        state = worker.State()
        worker.plan_pending_local(self.api, self.cfg, state)
        self.assertEqual(self.backend.created_plans, [])
        self.assertEqual(state.local_failures, {})

    def test_bad_source_sha_and_unusable_analysis_are_plan_errors(self):
        self.seed_project(source_sha="nope")
        state = worker.State()
        worker.plan_pending_local(self.api, self.cfg, state)
        self.assertEqual(state.local_failures[test_worker.PROJECT["id"]][0], 1)
        self.seed_project(result={"app_config": {"container_port": 8000, "environment": {"API_KEY": "x"}}})
        state = worker.State()
        worker.plan_pending_local(self.api, self.cfg, state)   # 비밀로 보이는 환경 변수 이름은 AWS와 같이 거부한다
        self.assertEqual(self.backend.created_plans, [])

    def test_tick_claims_with_targets_and_plans_local_first(self):
        self.seed_project()
        os.environ["FAKE_FOUNDATION_JSON"] = "{}"
        worker.tick(self.api, self.cfg, worker.State())
        self.assertEqual(self.backend.order[0], "claim")
        paths = [p for p, _ in self.backend.headers_seen if "claim" in p]
        self.assertEqual(paths, ["/api/worker/deployments/claim?targets=aws,local_docker"])
        targets = [b["target"] for b in self.backend.created_plans]
        self.assertEqual(targets[0], "local_docker")   # 몇 초 걸리는 로컬 계획이 AWS 계획을 기다리지 않는다

    def test_default_worker_is_unchanged(self):
        self.cfg.targets = ("aws",)
        self.assertEqual(worker.claim_path(self.cfg), "/api/worker/deployments/claim")
        self.seed_project()
        os.environ["FAKE_FOUNDATION_JSON"] = "{}"
        worker.tick(self.api, self.cfg, worker.State())
        self.assertEqual({b["target"] for b in self.backend.created_plans}, {"aws"})
        self.assertEqual(self.docker_calls(), [])   # 로컬 Docker를 켜지 않았으면 docker를 부르지 않는다

    def test_local_only_worker_never_calls_aws_or_deploy_sh(self):
        self.cfg.targets = ("local_docker",)
        self.seed_project()
        self.backend.connections = [{"id": "c1", "provider": "aws", "account_id": "123456789012", "role_arn": "arn:aws:iam::123456789012:role/PavedCloudsReadOnlyRole",
                                     "external_id": "pc-" + "a" * 32}]
        worker.tick(self.api, self.cfg, worker.State())
        self.assertEqual([b["target"] for b in self.backend.created_plans], ["local_docker"])
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.aws_calls(), [])
        self.assertNotIn("pending", self.backend.order)   # AWS 연결 확인도 하지 않는다
        self.assertEqual(worker.claim_path(self.cfg), "/api/worker/deployments/claim?targets=local_docker")


class LocalExecutorTests(WorkerBase):
    DEP = "dep-local-1"

    def setUp(self):
        super().setUp()
        self.zip = make_zip(self.tmp / "src.zip", {"repo/sample-back/Dockerfile": "FROM x", "repo/sample-back/app.py": "x"})
        self.sha = sha256_file(self.zip)
        self.plan = plan_for(host_port=self.health.port, source_sha=self.sha, deploy_id=local_docker.local_deploy_id(test_worker.PROJECT["id"]))
        os.environ["FAKE_BUILD_LOG"] = str(self.tmp / "build.json")

    def job(self, **over):
        v = json.loads(json.dumps(self.plan["variables"]))
        j = {"deployment_id": self.DEP, "target": "local_docker", "operation_type": "deploy", "project_id": test_worker.PROJECT["id"],
             "variables": v, "terraform_plan_sha256": self.plan["digest"], "source_sha256": self.sha, "source_path": str(self.zip)}
        j.update(over)
        return j

    def events(self):
        return [b for d, b in self.backend.events if d == self.DEP]

    def statuses(self):
        return [(b["status"], b["event_type"]) for b in self.events()]

    def run_job(self, **over):
        worker.execute_job(self.api, self.cfg, self.job(**over))

    def assert_nothing_ran(self):
        names = {self.sub(a) for a in self.docker_calls()}
        self.assertFalse(names & {"build", "up", "run", "rmi"}, names)

    def draft_vars(self, content="FROM python:3.12-slim\n", sha=None):
        v = json.loads(json.dumps(self.plan["variables"]))
        v["dockerfile"] = None
        v["dockerfile_draft"] = {"content": content, "sha256": sha or hashlib.sha256(content.encode()).hexdigest()}
        return v

    def test_approved_draft_is_built_from_outside_the_source(self):
        self.run_job(variables=self.draft_vars())
        self.assertEqual(self.statuses()[-1], ("healthy", "healthy"))
        built = json.loads(Path(os.environ["FAKE_BUILD_LOG"]).read_text(encoding="utf-8"))
        self.assertTrue(built["dockerfile_exists"])
        self.assertNotIn("Dockerfile", built["files"], "초안은 소스(컨텍스트) 안에 쓰지 않음")
        self.assertFalse(Path(built["dockerfile"]).exists(), "빌드 뒤 임시 파일은 지움")

    def test_local_draft_changed_after_approval_is_not_built(self):
        self.run_job(variables=self.draft_vars(sha="0" * 64))
        self.assertEqual(self.statuses()[-1][0], "failed")
        self.assert_nothing_ran()

    def test_success_reports_deploying_then_healthy_with_the_local_url(self):
        self.run_job()
        self.assertEqual(self.statuses(), [("deploying", "apply_done"), ("healthy", "healthy")])
        healthy = self.events()[-1]
        self.assertEqual(healthy["url"], f"http://localhost:{self.health.port}")
        self.assertEqual(healthy["details"]["compose_sha256"], self.plan["digest"])
        d = self.cfg.local.root / local_docker.local_deploy_id(test_worker.PROJECT["id"])
        self.assertEqual((d / "docker-compose.yml").read_text(encoding="utf-8"), self.plan["variables"]["compose_yaml"])
        self.assertEqual(self.calls(), [])   # deploy.sh를 부르지 않았다
        order = [self.sub(a) for a in self.docker_calls()]
        self.assertLess(order.index("build"), order.index("up"))
        self.assertLess(order.index("up"), order.index("run"))
        self.assertTrue(json.loads((self.tmp / "build.json").read_text(encoding="utf-8"))["dockerfile_exists"])

    def test_health_uses_approved_bind_and_report_uses_approved_public_host(self):
        job = self.job()
        variables = job["variables"]
        variables["local"].update(bind="127.0.0.2", public_host="demo.local")
        variables["compose_yaml"] = local_docker.render_compose(local_docker.params_from_variables(variables))
        job["terraform_plan_sha256"] = local_docker.compose_digest(variables["compose_yaml"])
        self.cfg.local.bind = "192.168.1.10"  # 승인 뒤 실행기 설정이 바뀌어도 승인값을 쓴다
        self.cfg.local.public_host = "changed.local"
        with mock.patch.object(local_docker, "probe", return_value=200) as probe:
            worker.execute_job(self.api, self.cfg, job)
        probe.assert_called_once_with(f"http://127.0.0.2:{self.health.port}/health")
        self.assertEqual(self.statuses(), [("deploying", "apply_done"), ("healthy", "healthy")])
        self.assertEqual(self.events()[-1]["url"], f"http://demo.local:{self.health.port}")
        compose = (self.cfg.local.root / variables["deploy_id"] / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn(f'"127.0.0.2:{self.health.port}:8000"', compose)

    def test_downloaded_source_is_used_and_its_temp_dir_removed(self):
        self.backend.sources[test_worker.PROJECT["id"]] = self.zip.read_bytes()
        self.run_job(source_path=None, source_download_path=f"/api/worker/projects/{test_worker.PROJECT['id']}/source")
        self.assertEqual(self.statuses()[-1], ("healthy", "healthy"))
        self.assertEqual(self.backend.source_gets, 1)

    def test_tampered_compose_is_not_executed(self):
        # 승인된 변수와 다른 compose(호스트 루트 마운트를 몰래 더함)는 SHA-256이 맞더라도 실행하지 않는다
        j = self.job()
        evil = j["variables"]["compose_yaml"].replace("    ports:", '    volumes:\n      - "/:/host"\n    ports:')
        j["variables"]["compose_yaml"] = evil
        j["terraform_plan_sha256"] = local_docker.compose_digest(evil)
        worker.execute_job(self.api, self.cfg, j)
        self.assertEqual(self.statuses(), [("failed", "plan_mismatch")])
        self.assert_nothing_ran()

    def test_digest_mismatch_is_not_executed(self):
        self.run_job(terraform_plan_sha256="0" * 64)
        self.assertEqual(self.statuses(), [("failed", "plan_mismatch")])
        self.run_job(terraform_plan_sha256=None)
        self.assert_nothing_ran()

    def test_variables_changed_after_approval_are_not_executed(self):
        j = self.job()
        j["variables"]["app"]["container_port"] = 9999   # compose 본문은 그대로인데 변수만 바뀜
        worker.execute_job(self.api, self.cfg, j)
        self.assertEqual(self.statuses(), [("failed", "plan_mismatch")])
        self.assert_nothing_ran()

    def test_source_changed_after_approval_is_not_built(self):
        self.zip.write_bytes(self.zip.read_bytes() + b"x")   # 승인한 뒤 소스 파일이 바뀜
        self.run_job()
        self.assertEqual(self.statuses(), [("failed", "source_mismatch")])
        self.assert_nothing_ran()
        self.backend.events.clear()
        self.run_job(source_sha256="f" * 64)
        self.assertEqual(self.statuses(), [("failed", "source_mismatch")])

    def test_invalid_jobs_fail_without_running_docker(self):
        bad_vars = []
        for path, val in (("deploy_id", "../x"), ("image", "evil:latest")):
            v = json.loads(json.dumps(self.plan["variables"]))
            v[path] = val
            bad_vars.append(v)
        v = json.loads(json.dumps(self.plan["variables"]))
        v["app"]["health_check_path"] = "/bad path"
        bad_vars.append(v)
        v = json.loads(json.dumps(self.plan["variables"]))
        v["local"]["public_host"] = "evil host"
        bad_vars.append(v)
        v = json.loads(json.dumps(self.plan["variables"]))
        v["app"]["init_command"] = "not-a-list"
        bad_vars.append(v)
        for v in bad_vars:
            with self.subTest(v=v.get("deploy_id")):
                self.backend.events.clear()
                self.run_job(variables=v)
                self.assertEqual(self.statuses(), [("failed", "invalid_job")])
        self.assert_nothing_ran()

    def test_rollback_and_disabled_target_are_refused(self):
        self.run_job(operation_type="rollback")
        self.assertEqual(self.statuses(), [("failed", "unsupported_operation")])
        self.backend.events.clear()
        self.cfg.targets = ("aws",)
        self.run_job()
        self.assertEqual(self.statuses(), [("failed", "unsupported_target")])
        self.assert_nothing_ran()

    def test_build_failure_is_reported_masked_and_stops_before_apply(self):
        os.environ["FAKE_BUILD_RC"] = "1"
        self.run_job()
        self.assertEqual(self.statuses(), [("failed", "build_failed")])
        self.assertNotIn("hunter2", json.dumps(self.events()))
        self.assertNotIn("up", {self.sub(a) for a in self.docker_calls()})

    def test_unhealthy_app_fails_with_diagnose_and_stops_containers(self):
        self.health.code = 404
        self.run_job()
        self.assertEqual(self.statuses(), [("deploying", "apply_done"), ("failed", "health_failed")])
        failed = self.events()[-1]
        self.assertIn("diagnose", failed["details"])
        self.assertNotIn("sk-abcdefghijklmnop1234", json.dumps(failed))
        self.assertIn("stop", {self.sub(a) for a in self.docker_calls()})

    def test_init_failure_never_reports_healthy(self):
        os.environ["FAKE_RUN_RC"] = "1"
        self.run_job()
        self.assertEqual(self.statuses(), [("failed", "init_failed")])

    def test_failed_deploying_report_is_retried_before_healthy(self):
        self.backend.event_failures = 4   # 첫 보고(deploying)가 재시도 한도까지 실패한다
        self.run_job()
        self.assertEqual(self.statuses(), [("deploying", "apply_done"), ("healthy", "healthy")])   # 배포는 이어지고 healthy 전에 다시 보낸다

    def test_docker_missing_is_reported_as_failure(self):
        self.cfg.local.docker = [str(self.tmp / "no-such-docker")]
        self.run_job()
        self.assertEqual(self.statuses(), [("failed", "docker_missing")])

    def test_unexpected_error_is_still_reported(self):
        with mock.patch.object(local_docker, "build_image", side_effect=RuntimeError("boom password=hunter2")):
            self.run_job()
        self.assertEqual(self.statuses(), [("failed", "worker_error")])
        self.assertNotIn("hunter2", json.dumps(self.events()))

    def test_worker_token_does_not_reach_docker(self):
        os.environ["WORKER_API_TOKEN"] = test_worker.TOKEN
        seen = []
        real_run = local_docker._run

        def spy(cmd, timeout, cwd=None, env=None):
            seen.append(dict(env or {}))
            return real_run(cmd, timeout, cwd=cwd, env=env)

        with mock.patch.object(local_docker, "_run", spy):
            self.run_job()
        self.assertTrue(seen)
        self.assertTrue(all("WORKER_API_TOKEN" not in e for e in seen))


class CliTests(DockerBase):
    def test_list_status_destroy(self):
        plan = plan_for()
        local_docker.apply(self.lc, "ab12cd34", plan["variables"]["compose_yaml"], host_port=Health().port, health_path="/health", use_database=False)
        base = ["--root", str(self.lc.root)]
        self.assertEqual(local_docker.main(base + ["list"]), 0)
        with mock.patch.object(local_docker, "LocalConfig", return_value=self.lc):
            self.assertEqual(local_docker.main(base + ["status", "ab12cd34"]), 0)
            self.assertEqual(local_docker.main(base + ["destroy", "ab12cd34"]), 0)
            self.assertEqual(local_docker.main(base + ["destroy", "zzzzzzzz"]), 1)   # 없는 배포는 오류로 끝난다


class MainArgumentTests(unittest.TestCase):
    def run_main(self, *argv, env=None):
        base = {"WORKER_API_TOKEN": "t" * 8}
        base.update(env or {})
        with mock.patch.dict(os.environ, base), mock.patch.object(worker, "tick", side_effect=SystemExit(0)), \
                mock.patch.object(worker, "warn_if_insecure"), mock.patch.object(worker, "run_capture", return_value=(0, "X86_64", "")):
            try:
                return worker.main(["--once", *argv])
            except SystemExit as e:
                return e.code

    def test_bad_arguments_are_refused(self):
        self.assertEqual(self.run_main("--targets", "azure"), 2)
        self.assertEqual(self.run_main("--targets", ""), 2)
        self.assertEqual(self.run_main("--targets", "local_docker", "--local-bind", "not-an-ip"), 2)
        self.assertEqual(self.run_main("--targets", "local_docker", "--local-host", "bad host"), 2)

    def test_valid_targets_start(self):
        self.assertEqual(self.run_main("--targets", "local_docker", "--arch", "X86_64"), 0)
        self.assertEqual(self.run_main("--targets", "aws,local_docker", "--arch", "X86_64"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
