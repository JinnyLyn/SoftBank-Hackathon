// 실행: npm ci --prefix front && node scripts/ci/test_platform_front.mjs
// 실제 API 어댑터를 실행한다. HTTP·provider는 대역이며 실제 서버·브라우저 검증은 아니다.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInThisContext } from 'node:vm';

const requireFront = createRequire(new URL('../../front/package.json', import.meta.url));
const ts = requireFront('typescript');

function loadTs(relativePath, imports = {}) {
  const file = new URL(relativePath, import.meta.url);
  const { outputText } = ts.transpileModule(readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: file.pathname,
  });
  const module = { exports: {} };
  const require = (name) => {
    assert(Object.hasOwn(imports, name), `Unexpected import: ${name}`);
    return imports[name];
  };
  runInThisContext(`(function(require, module, exports) {\n${outputText}\n})`, { filename: file.pathname })(require, module, module.exports);
  return module.exports;
}

const format = loadTs('../../front/src/format.ts');
const scale = { expectedUsers: '100', pattern: 'steady', monthlyBudgetUsd: 100, purpose: '시험' };

async function checkBundle(target, initCommand) {
  const local = target === 'local_docker';
  const variables = {
    tier: 'balanced',
    source_sha256: 'a'.repeat(64),
    dockerfile: 'app/Dockerfile',
    app: {
      container_port: 8000,
      health_check_path: '/health/ready',
      use_database: true,
      environment: { COOKIE_SECURE: 'false' },
      ...(initCommand ? { init_command: initCommand } : {}),
    },
    ...(local ? {
      compose_yaml: 'services:\n  app:\n    image: pc-local/ab12cd34:s123\n',
      local: { bind: '127.0.0.2', host_port: 18123, public_host: 'demo.local' },
    } : { region: 'ap-northeast-2' }),
  };
  let plan = {
    id: 'plan-1', project_id: 'project-1', analysis_id: 'analysis-1',
    target, module_id: local ? 'local-docker-compose' : 'ecs-web-app',
    variables, summary: '검토할 계획', cost_estimate: { amount: '0', currency: 'USD', period: 'month', pricing_as_of: '2026-10-11' },
    terraform_plan_sha256: 'b'.repeat(64), terraform_plan_ready: true,
    fingerprint: 'fingerprint-1', status: 'awaiting_approval', created_at: '2026-10-11T00:00:00Z', approved_at: null,
  };
  const calls = [];
  const req = async (path) => {
    calls.push(path);
    if (path === '/connections') return [];
    if (path === '/projects/project-1/plans') return structuredClone([plan]);
    if (path === '/plans/plan-1') return structuredClone(plan);
    throw new Error(`Unexpected HTTP request: ${path}`);
  };
  const api = loadTs('../../front/src/api/real.ts', {
    './http': { req, ApiError: class extends Error {} },
    '../format': format,
    '../providers': { isEnabled: (provider) => provider === (local ? 'onprem' : 'aws') },
  });
  const choice = { connectionId: local ? 'local-docker' : 'aws', tier: 'balanced' };
  function verify(bundle) {
    assert.equal(bundle.tool, local ? 'compose' : 'terraform');
    assert.deepEqual(bundle.files.map((f) => f.name), local
      ? ['docker-compose.yml', 'local-plan.variables.json']
      : ['variables.auto.tfvars.json']);
    const file = bundle.files.find((f) => f.name.endsWith('.json'));
    assert.deepEqual(JSON.parse(file.content), plan.variables, '승인 변수 전체를 손실 없이 표시해야 한다');
    if (local) assert.equal(bundle.files[0].content, plan.variables.compose_yaml);
    assert.equal(bundle.planInfo.planId, plan.id);
    assert.equal(bundle.planInfo.fingerprint, plan.fingerprint);
  }
  const recommendation = await api.recommend('project-1', scale);
  verify(recommendation.bundles[`${choice.connectionId}:${choice.tier}`]);
  // generate가 재조회한 최신 실행 변수와 fingerprint도 검토 파일에 반영한다.
  plan = structuredClone(plan);
  plan.fingerprint = 'fingerprint-2';
  plan.variables.app.health_check_path = '/health/latest';
  if (initCommand) plan.variables.app.init_command = ['python', '-m', 'app.migrate_latest'];
  verify(await api.generate('project-1', choice));
  assert.deepEqual(calls, ['/projects/project-1/plans', '/connections', '/plans/plan-1']);
}

await checkBundle('local_docker', ['python', '-m', 'app.initialize_database']);
await checkBundle('local_docker');
await checkBundle('aws', ['python', '-m', 'app.initialize_database']);
console.log('PASS: 공개 recommend/generate 어댑터 회귀 3개 (로컬 초기화 있음·없음, AWS)');
