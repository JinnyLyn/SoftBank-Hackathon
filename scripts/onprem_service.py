"""Linux/WSL의 한 PC용 서비스 supervisor (stdlib, foreground, 자동 설치 없음)."""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
BUSY = {'analyzing', 'source', 'build', 'apply'}
TTL = 15


def clean_env(source=None):
    source = os.environ if source is None else source
    return {k: v for k, v in source.items() if k in {
        'PATH', 'HOME', 'USER', 'LANG', 'LC_ALL', 'TMPDIR', 'XDG_RUNTIME_DIR',
        'SSL_CERT_FILE', 'SSL_CERT_DIR', 'SYSTEMROOT', 'WSL_INTEROP',
    }}


def atomic_json(path, value, mode=0o644):
    temp = path.with_suffix('.tmp')
    with temp.open('w') as stream:
        json.dump(value, stream, allow_nan=False)
    temp.chmod(mode)
    temp.replace(path)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def progress_status(path, run_id, process, now):
    if process is None:
        return {'status': 'starting'}
    if process.poll() is not None:
        return {'status': 'down'}
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {'status': 'starting'}
    except (OSError, ValueError):
        return {'status': 'stale'}
    if not isinstance(data, dict):
        return {'status': 'stale'}
    observed, success = data.get('observed_at'), data.get('last_success_at')
    phase, deadline = data.get('phase'), data.get('deadline_at')
    if (data.get('version') != 1 or data.get('run_id') != run_id
            or not number(observed) or observed > now or observed < 0
            or not isinstance(phase, str) or phase not in BUSY | {'starting', 'idle', 'polling', 'error'}):
        return {'status': 'stale'}
    result = {'status': 'stale', 'phase': phase}
    if phase == 'error':
        result['status'] = 'down'
    elif success is None:
        result['status'] = 'starting' if now - observed < TTL else 'stale'
    elif not number(success) or success > observed or success < 0:
        pass
    elif phase in BUSY:
        if number(deadline) and observed < deadline <= observed + 3600 and now < deadline:
            result['status'] = 'ready'
    elif phase in {'idle', 'polling'} and now - success < TTL:
        result['status'] = 'ready'
    return result


def safe_diagnosis(stderr):
    """Keep only known exception types and numeric DB codes, never their messages/SQL."""
    allowed = ('OperationalError', 'IntegrityError', 'ProgrammingError', 'InternalError',
               'InterfaceError', 'DataError', 'DatabaseError', 'ConnectionError',
               'TimeoutError', 'PermissionError', 'FileNotFoundError', 'ValueError',
               'RuntimeError', 'OSError')
    matches = re.findall(r'(?m)^(?:[A-Za-z_][A-Za-z_0-9]*\.)*('
                         + '|'.join(allowed) + r'): (?:\(([0-9]{1,5}),)?', (stderr or '')[-16384:])
    if not matches:
        return '진단 유형 확인 불가 (원문 비공개)'
    kind, code = matches[-1]
    return kind + (f' code={code}' if code else '')


def run(command, env, timeout=30, capture=True, label=None):
    """Own a command's whole process group, including bounded timeout cleanup."""
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE if capture else None,
                               stderr=subprocess.PIPE if capture else None,
                               text=True, start_new_session=True)
    try:
        out, stderr = process.communicate(timeout=timeout)
    except BaseException:
        stop_process(process)
        raise
    if process.returncode:
        # Subprocess stderr may include credentials; never echo it automatically.
        stage = label or f'{command[0]} {command[1]}'
        raise RuntimeError(f'{stage} 실패 (exit {process.returncode}; {safe_diagnosis(stderr)})')
    return (out or '').strip()


def stop_process(process):
    # Only groups created by our own start_new_session Popen are passed here.
    for sig, timeout in ((signal.SIGTERM, 5), (signal.SIGKILL, 3)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            continue
        if sig == signal.SIGTERM:
            # A direct child can exit while its descendants still own the group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        break


def docker_endpoint(env, source=None):
    source = os.environ if source is None else source
    context = source.get('DOCKER_CONTEXT')
    if context or not source.get('DOCKER_HOST'):
        context = context or run(['docker', 'context', 'show'], env)
        endpoint = run(['docker', 'context', 'inspect', context, '--format', '{{.Endpoints.docker.Host}}'], env)
    else:
        endpoint = source['DOCKER_HOST']
    if not endpoint.startswith(('unix://', 'npipe://')):
        raise RuntimeError('이 서비스는 이 PC의 unix/npipe Docker만 지원합니다. SSH/TCP context는 사용할 수 없습니다.')
    return endpoint


def check_ports(ports):
    if len(set(ports)) != len(ports):
        raise RuntimeError('API와 웹 포트는 서로 달라야 합니다.')
    for port in ports:
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
            except OSError as error:
                raise RuntimeError(f'127.0.0.1:{port} 사용 중입니다. 기존 서비스는 종료하지 않습니다.') from error


class Service:
    def __init__(self, args):
        self.args = args
        self.state = args.state_dir.expanduser().resolve()
        self.run_id = str(uuid.uuid4())
        self.env = clean_env()
        self.children = {}
        self.logs = []
        self.lock = None
        self.compose_started = False
        self.compose = []

    def prepare(self):
        for executable in ('docker', 'uv', 'node', 'npm'):
            if not shutil.which(executable):
                raise RuntimeError(f'{executable} 실행기가 필요합니다. 설치 후 다시 실행하세요.')
        if not (ROOT / 'front/node_modules/.bin/vite').exists():
            raise RuntimeError('프런트 의존성이 없습니다. npm ci --prefix front 실행 후 다시 시도하세요.')
        version = run(['node', '--version'], self.env).lstrip('v').split('.')
        if tuple(map(int, version[:2])) < (22, 12):
            raise RuntimeError('Node 22.12 이상이 필요합니다.')
        check_ports([self.args.api_port, self.args.web_port])
        if self.state == ROOT or ROOT in self.state.parents:
            raise RuntimeError('--state-dir는 저장소 밖의 전용 디렉터리여야 합니다.')
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (self.state / 'service.lock').open('a+')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('같은 state-dir 서비스가 이미 실행 중입니다.') from error
        identity = self.state / 'identity.json'
        if identity.exists():
            config = json.loads(identity.read_text())
            if config.get('version') != 1 or not config.get('project', '').startswith('paved-onprem-'):
                raise RuntimeError('state-dir identity 형식이 맞지 않습니다.')
        else:
            if any(p.name != 'service.lock' for p in self.state.iterdir()):
                raise RuntimeError('새 state-dir는 비어 있어야 합니다. 기존 데이터를 변경하지 않습니다.')
            config = dict(version=1, project='paved-onprem-' + uuid.uuid4().hex[:16],
                          token=secrets.token_hex(32), db_password=secrets.token_hex(24),
                          db_root_password=secrets.token_hex(24))
            atomic_json(identity, config, 0o600)
        self.state.chmod(0o700)
        for directory in ('uploads', 'plans', 'status', 'progress', 'logs', 'local-deployments'):
            path = self.state / directory
            path.mkdir(exist_ok=True)
            # API uid 10001 writes these bind roots; private ancestor prevents host traversal.
            path.chmod(0o777 if directory in ('uploads', 'plans') else 0o755)
        self.env.update(DOCKER_HOST=docker_endpoint(self.env), WORKER_API_TOKEN=config['token'],
                        ONPREM_DB_PASSWORD=config['db_password'], ONPREM_DB_ROOT_PASSWORD=config['db_root_password'],
                        ONPREM_STATE_DIR=str(self.state), ONPREM_RUN_ID=self.run_id,
                        ONPREM_API_PORT=str(self.args.api_port), AWS_EC2_METADATA_DISABLED='true')
        run(['docker', 'info', '--format', '{{.ServerVersion}}'], self.env, timeout=3)
        run(['docker', 'compose', 'version'], self.env)
        self.compose = ['docker', 'compose', '--env-file', '/dev/null', '--project-name', config['project'],
                        '-f', str(ROOT / 'scripts/onprem.compose.yaml')]
        atomic_json(self.state / 'run.json', dict(run_id=self.run_id, project=config['project'],
                    docker_endpoint=self.env['DOCKER_HOST'], pid=os.getpid(), children={}), 0o600)
        self.snapshot()

    def snapshot(self):
        try:
            run(['docker', 'info', '--format', '{{.ServerVersion}}'], self.env, timeout=3)
            docker = {'status': 'ready'}
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            docker = {'status': 'down'}
        now = time.time()
        components = {'docker': docker}
        for name in ('analyzer', 'worker'):
            components[name] = progress_status(self.state / 'progress' / f'{name}.json', self.run_id,
                                               self.children.get(name), now)
        atomic_json(self.state / 'status/status.json', dict(version=1, run_id=self.run_id,
                    checked_at=now, expires_at=now + TTL, analysis_mode='rules', target='local_docker', components=components))
        return all(c['status'] == 'ready' for c in components.values())

    def spawn(self, name, command, cwd, env):
        log = (self.state / 'logs' / f'{name}.log').open('a')
        self.logs.append(log)
        self.children[name] = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                              stdout=log, stderr=log, start_new_session=True)
        manifest = json.loads((self.state / 'run.json').read_text())
        manifest['children'][name] = self.children[name].pid
        atomic_json(self.state / 'run.json', manifest, 0o600)

    def start(self):
        self.compose_started = True  # Partial `up` must also be cleaned up.
        run(self.compose + ['up', '--detach', '--build', '--wait', '--wait-timeout', '180'], self.env,
            timeout=self.args.startup_timeout, capture=False)
        run(self.compose + ['exec', '-T', 'api', 'python', '-m', 'app.cli', 'migrate'], self.env, timeout=60,
            label='플랫폼 DB 마이그레이션')
        api = f'http://127.0.0.1:{self.args.api_port}'
        with urllib.request.urlopen(api + '/ready', timeout=5) as response:
            if response.status != 200:
                raise RuntimeError('API/DB가 준비되지 않았습니다.')
        python = ['uv', 'run', '--no-project', '--no-config', '--python', '3.12', '--no-python-downloads', 'python']
        # API uploads are UID10001-owned 0600 files. Do not weaken their permissions.
        # This intentionally absent, per-run path forces the authenticated source API fallback.
        analyzer_upload_dir = self.state / 'source-via-api' / self.run_id
        for name, script, options in (
            ('analyzer', 'ai/runner.py', ['--no-llm', '--no-dotenv', '--upload-dir', str(analyzer_upload_dir)]),
            ('worker', 'infra/worker/worker.py', ['--targets', 'local_docker', '--local-dir', str(self.state / 'local-deployments')]),
        ):
            self.spawn(name, python + [str(ROOT / script), '--api-url', api, '--progress-file',
                       str(self.state / 'progress' / f'{name}.json'), '--run-id', self.run_id] + options, ROOT, self.env)
        web_env = clean_env()
        web_env.update(API_TARGET=api, VITE_USE_MOCK='false', VITE_API_BASE_URL='/api',
                       VITE_PROVIDERS='aws', VITE_DOCKERFILE_DRAFT_BUILD='false')
        self.spawn('web', ['node', str(ROOT / 'front/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1',
                          '--port', str(self.args.web_port), '--strictPort'], ROOT / 'front', web_env)
        print(f'플랫폼 PC의 Docker · 규칙 분석 · HTTP 전용: http://127.0.0.1:{self.args.web_port}/onprem\n'
              f'데이터: {self.state}\nCtrl-C: 서비스 종료, 데이터와 배포 앱 보존', flush=True)
        deadline = time.monotonic() + 60
        while True:
            ready = self.snapshot()
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{self.args.web_port}/onprem', timeout=2) as response:
                    web_ready = response.status == 200
            except (OSError, ValueError):
                web_ready = False
            if ready and web_ready and self.children['web'].poll() is None:
                break
            if any(p.poll() is not None for p in self.children.values()) or time.monotonic() >= deadline:
                raise RuntimeError(f'서비스 초기 준비 실패. {self.state}/logs를 확인하세요.')
            time.sleep(2)

    def close(self):
        for process in reversed(list(self.children.values())):
            stop_process(process)
        if self.compose_started:
            try:
                run(self.compose + ['down', '--timeout', '10'], self.env, timeout=60)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                print('서비스 정리 실패: run.json의 정확한 project/endpoint를 확인하세요. 데이터는 보존했습니다.', file=sys.stderr)
                return False
        for log in self.logs:
            log.close()
        if self.lock:
            self.lock.close()
        return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path.home() / '.local/share/paved-clouds/onprem')
    parser.add_argument('--api-port', type=int, default=18000)
    parser.add_argument('--web-port', type=int, default=15173)
    parser.add_argument('--startup-timeout', type=int, default=600)
    args = parser.parse_args(argv)
    if any(not 1 <= port <= 65535 for port in (args.api_port, args.web_port)) or args.startup_timeout <= 0:
        parser.error('포트는 1..65535, timeout은 양수여야 합니다.')
    service = Service(args)
    def interrupted(signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    result = 0
    try:
        service.prepare()
        service.start()
        while True:
            service.snapshot()
            time.sleep(5)
    except KeyboardInterrupt:
        print('on-prem 서비스 종료 중…', flush=True)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'on-prem 시작/실행 오류: {error}', file=sys.stderr)
        result = 1
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if not service.close():
            result = 1
    return result


if __name__ == '__main__':
    sys.exit(main())
