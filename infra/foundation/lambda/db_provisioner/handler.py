"""앱별 DB·계정을 만들고 지우고 확인하는 Lambda (VPC 안에서 RDS에 접속한다).

Fargate 작업으로 하던 일(deploy.sh의 db_task)과 같은 일을 한다. Fargate 작업은 시작에만 약 70초가 걸리지만
Lambda는 몇 초면 끝난다. 접속 정보는 환경 변수와 SSM 파라미터에서 읽고 이 함수 역할만 DB 관리자 비밀번호를 읽을 수 있다.

event: {"mode": "provision" | "drop" | "verify", "id": "<배포 ID>"}
반환: {"ok": bool, "log": "여러 줄 문자열"}   log의 마커(PROVISION_OK, DROP_OK, VERIFY_OK 등)는 Fargate 작업과 같다.
비밀번호는 로그와 반환값에 넣지 않는다.
"""
import os
import re
from urllib.parse import unquote, urlparse

import boto3
import pymysql

ID_RE = re.compile(r"^[a-z0-9]{4,8}$")


def _param(name):
    return boto3.client("ssm").get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def _connect(user, password, database=None):
    # MySQL 8.4의 기본 인증(caching_sha2_password)은 암호화되지 않은 연결에서 cryptography가 필요하다.
    # 순수 파이썬만 넣으려고 TLS로 접속한다(RDS는 TLS를 지원한다). VPC 안의 연결이라 서버 인증서 검증은 하지 않는다
    return pymysql.connect(
        host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]), user=user, password=password, database=database,
        connect_timeout=10, read_timeout=30, write_timeout=30, ssl={"check_hostname": False}, autocommit=True,
    )


def _app_password(deploy_id):
    url = _param(f"{os.environ['APPS_PARAM_PREFIX']}/{deploy_id}/database-url")
    p = urlparse(url)
    return unquote(p.password or "")


def _provision(deploy_id, db):
    pw = _app_password(deploy_id)
    admin = _connect(os.environ["DB_ADMIN_USER"], _param(os.environ["ADMIN_PW_PARAM"]))
    with admin, admin.cursor() as c:
        c.execute(f"CREATE DATABASE IF NOT EXISTS `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        c.execute("CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s", (db, pw))
        c.execute("ALTER USER %s@'%%' IDENTIFIED BY %s", (db, pw))
        c.execute(f"GRANT ALL PRIVILEGES ON `{db}`.* TO %s@'%%'", (db,))
    return True, "PROVISION_OK"


def _drop(deploy_id, db):
    admin = _connect(os.environ["DB_ADMIN_USER"], _param(os.environ["ADMIN_PW_PARAM"]))
    with admin, admin.cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS `{db}`")
        c.execute("DROP USER IF EXISTS %s@'%%'", (db,))
    return True, "DROP_OK"


def _denied(conn_user, pw, statement, database=None):
    """문장이 거부되면 (True, 오류 메시지), 실행되면 (False, '')."""
    conn = _connect(conn_user, pw, database)
    try:
        with conn.cursor() as c:
            c.execute(statement)
            c.fetchall()
        return False, ""
    except pymysql.err.OperationalError as e:
        return True, str(e)
    finally:
        conn.close()


def _verify(deploy_id, db):
    """앱 전용 계정이 자기 DB만 쓸 수 있는지 확인한다(Fargate 작업의 verify와 같은 검사)."""
    pw = _app_password(deploy_id)
    other = os.environ.get("OTHER_DB", "app")
    lines = []
    try:
        conn = _connect(db, pw, db)
        with conn, conn.cursor() as c:
            c.execute("SELECT 1")
            c.fetchall()
        lines.append("OWN_DB_OK")
    except pymysql.err.Error as e:
        return False, f"{e}\nOWN_DB_FAIL"
    denied, msg = _denied(db, pw, f"USE `{other}`")
    lines.append(msg)
    if not denied:
        return False, "\n".join(lines + ["OTHER_DB_ACCESSIBLE"])
    lines.append("OTHER_DB_DENIED")
    denied, msg = _denied(db, pw, "SELECT COUNT(*) FROM mysql.user")
    lines.append(msg)
    if not denied:
        return False, "\n".join(lines + ["SYSTEM_TABLES_ACCESSIBLE"])
    lines.append("SYSTEM_TABLES_DENIED")
    conn = _connect(db, pw)
    with conn, conn.cursor() as c:
        c.execute("SHOW DATABASES")
        visible = [r[0] for r in c.fetchall()]
    lines.append("VISIBLE_DBS: " + " ".join(visible))
    extra = [d for d in visible if d not in ("information_schema", "performance_schema", db)]
    if extra:
        return False, "\n".join(lines + ["OTHER_DBS_VISIBLE: " + " ".join(extra)])
    return True, "\n".join(lines + ["OTHER_APP_DBS_HIDDEN", "VERIFY_OK"])


MODES = {"provision": _provision, "drop": _drop, "verify": _verify}


def handler(event, context):
    mode = (event or {}).get("mode")
    deploy_id = (event or {}).get("id", "")
    if mode not in MODES:
        return {"ok": False, "log": f"알 수 없는 mode: {mode!r}"}
    if not isinstance(deploy_id, str) or not ID_RE.match(deploy_id):
        return {"ok": False, "log": "배포 ID가 올바르지 않습니다(소문자·숫자 4~8자)"}
    try:
        ok, log = MODES[mode](deploy_id, f"app_{deploy_id}")
    except Exception as e:  # noqa: BLE001 - 오류 종류와 메시지만 돌려준다(비밀번호는 예외 메시지에 들어가지 않는다)
        return {"ok": False, "log": f"{type(e).__name__}: {str(e)[:300]}"}
    return {"ok": ok, "log": log}
