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
  assert.deepEqual(calls, ['/projects/project-1/plans', '/plans/plan-1']);
}

async function checkMixedRecommendation(monthlyBudgetUsd, expectedConnection) {
  const aws = {
    id: 'aws-plan', target: 'aws', module_id: 'ecs-web-app', status: 'awaiting_approval',
    created_at: '2026-10-11T00:00:00Z', fingerprint: 'aws-fingerprint', terraform_plan_ready: true,
    summary: 'AWS 계획', cost_estimate: { amount: '120', pricing_as_of: '2026-10-11' },
    variables: {
      tier: 'balanced', recommended: true,
      dockerfile_draft: { content: 'FROM node:22-slim\n' },
      options: [
        { tier: 'lean', rank: 'lowest', peak_monthly: 40, total_monthly: 30 },
        { tier: 'balanced', rank: 'average', peak_monthly: 120, total_monthly: 90, recommended: true },
        { tier: 'roomy', rank: 'highest', peak_monthly: 200, total_monthly: 150 },
      ],
    },
  };
  const local = {
    ...aws, id: 'local-plan', target: 'local_docker', module_id: 'local-docker-compose',
    fingerprint: 'local-fingerprint', cost_estimate: { amount: '0' },
    variables: { tier: 'balanced', compose_yaml: 'services:\n  app:\n    image: local/app\n' },
  };
  const req = async (path) => {
    if (path === '/projects/project-1/plans') return structuredClone([local, aws]);
    if (path === '/plans/aws-plan') return structuredClone(aws);
    if (path === '/plans/local-plan') return structuredClone(local);
    throw new Error(`Unexpected HTTP request: ${path}`);
  };
  const api = loadTs('../../front/src/api/real.ts', {
    './http': { req, ApiError: class extends Error {} },
    '../format': format,
    '../providers': { isEnabled: () => true },
  });
  const rec = await api.recommend('project-1', { ...scale, monthlyBudgetUsd });
  assert.deepEqual(rec.options.map((o) => o.provider), ['aws', 'onprem']);
  assert.deepEqual(rec.options[0].tiers.map((t) => [t.key, t.planned]), [
    ['lean', false], ['balanced', true], ['roomy', false],
  ]);
  assert.deepEqual(Object.keys(rec.bundles).sort(), ['aws:balanced', 'local-docker:balanced']);
  assert.deepEqual(rec.recommended, { connectionId: expectedConnection, tier: 'balanced' });
  assert.equal(rec.bundles['aws:balanced'].files[0].name, 'Dockerfile (초안)');
  assert.equal(rec.bundles['aws:balanced'].files[0].content, aws.variables.dockerfile_draft.content);
  assert.equal(rec.bundles['local-docker:balanced'].files[0].content, local.variables.compose_yaml);
  assert.equal((await api.generate('project-1', { connectionId: 'aws', tier: 'balanced' })).planInfo.planId, 'aws-plan');
  assert.equal((await api.generate('project-1', { connectionId: 'local-docker', tier: 'balanced' })).planInfo.planId, 'local-plan');
  await assert.rejects(api.generate('project-1', { connectionId: 'aws', tier: 'lean' }));
}

await checkBundle('local_docker', ['python', '-m', 'app.initialize_database']);
await checkBundle('local_docker');
await checkBundle('aws', ['python', '-m', 'app.initialize_database']);
await checkMixedRecommendation(150, 'aws');
await checkMixedRecommendation(100, 'local-docker');

// worker가 "다시 해도 같은 실패"(예산 부족)를 알리면 5분을 기다리지 않고 그 이유로 바로 끝난다. 예전 백엔드(404)는 계속 기다린다
{
  const reason = '월 예산 $30.00 안에 들어가는 구성이 없습니다.';
  let failures = [];
  const calls = [];
  const http = {
    req: async (path) => {
      calls.push(path);
      if (path === '/projects/p-budget/plans') return [];
      if (path === '/projects/p-budget/plan-failures') {
        if (failures === 404) throw new http.ApiError(404, 'Not Found');
        return failures;
      }
      throw new Error(`Unexpected HTTP request: ${path}`);
    },
    ApiError: class extends Error { constructor(status, message) { super(message); this.status = status; } },
  };
  const api = loadTs('../../front/src/api/real.ts', {
    './http': http, '../format': format, '../providers': { isEnabled: (provider) => provider === 'aws' },
  });
  failures = [{ target: 'aws', reason, retryable: false, attempts: 1, updated_at: '2026-10-11T00:00:00Z' }];
  await assert.rejects(api.recommend('p-budget', scale), (e) => e.status === 422 && e.message.includes(reason));
  assert.deepEqual(calls, ['/projects/p-budget/plans', '/projects/p-budget/plan-failures']);
  // 다시 시도할 수 있는 실패(retryable)나 예전 백엔드(404)면 바로 끝내지 않는다: 첫 확인 뒤 대기 상태로 남는지만 본다
  const final = failures;
  for (const pending of [[{ ...final[0], retryable: true }], 404]) {
    failures = pending;
    calls.length = 0;
    let settled = false;
    const run = api.recommend('p-budget', scale).then(() => { settled = true; }, (e) => { settled = true; throw e; });
    await new Promise((r) => setTimeout(r, 50));
    assert.equal(settled, false);
    assert.deepEqual(calls, ['/projects/p-budget/plans', '/projects/p-budget/plan-failures']);
    failures = final;   // 다음 확인(2초 뒤)에서 최종 실패가 보이면 끝난다
    await assert.rejects(run, (e) => e.status === 422);
  }
  console.log('PASS: 계획 실패 보고 — 다시 해도 같은 실패는 즉시 이유 표시, 재시도 가능·예전 백엔드는 계속 대기');
}
console.log('PASS: 공개 recommend/generate 어댑터 회귀 5개 (로컬 초기화 있음·없음, AWS, 혼합 대상의 예산 내·초과)');

// Dedicated local service: immutable identity, no global provider/connection/domain requests.
const localPlan = () => ({
  id: 'local-1', project_id: 'project-1', analysis_id: 'analysis-1', target: 'local_docker',
  module_id: 'local-docker-compose', variables: { compose_yaml: 'services:\n  app:\n    image: local/app\n' },
  summary: '로컬 계획', fingerprint: 'reviewed-fingerprint', status: 'awaiting_approval',
  cost_estimate: { amount: '0' }, terraform_plan_ready: true,
  created_at: '2026-10-11T00:00:00Z',
});
function localAdapter(handler) {
  const http = { req: handler, send: (method, body) => ({ method, body: JSON.stringify(body) }), ApiError: class extends Error { constructor(status, message) { super(message); this.status = status; } } };
  const real = loadTs('../../front/src/api/real.ts', {
    './http': http, '../format': format, '../providers': { isEnabled: (provider) => provider === 'aws' },
  });
  return { local: loadTs('../../front/src/onprem/api.ts', { '../api/http': http, '../api/real': real }), real };
}
{
  const calls = [];
  const { local, real } = localAdapter(async (path, init) => {
    calls.push([path, init]);
    if (path === '/projects/project-1/plans') return [localPlan(), { ...localPlan(), id: 'aws', target: 'aws' }];
    if (path === '/projects/aws-project/plans') return [{ ...localPlan(), id: 'aws', target: 'aws', variables: { tier: 'balanced' } }];
    if (path === '/plans/local-1') return localPlan();
    if (path.startsWith('/deployments?')) return [];
    if (path === '/plans/local-1/approve') return { ...localPlan(), status: 'approved' };
    if (path === '/deployments') return {};
    throw new Error(`Unexpected request ${path}`);
  });
  const reviewed = await local.recommendLocal('project-1');
  assert(Object.isFrozen(reviewed.reference));
  assert.deepEqual(reviewed.reference, { projectId: 'project-1', planId: 'local-1', fingerprint: 'reviewed-fingerprint', target: 'local_docker' });
  assert.equal(reviewed.bundle.tool, 'compose');
  assert.deepEqual(calls.map(([path]) => path), ['/projects/project-1/plans']);
  // Replacing the existing AWS cache cannot affect the local review.
  await real.recommend('aws-project', scale);
  calls.length = 0;
  await local.approveLocal(reviewed.reference);
  assert.deepEqual(calls.filter(([, init]) => init).map(([path, init]) => [path, JSON.parse(init.body)]), [
    ['/plans/local-1/approve', { expected_fingerprint: 'reviewed-fingerprint' }],
    ['/deployments', { plan_id: 'local-1', expected_fingerprint: 'reviewed-fingerprint' }],
  ]);
  assert(!calls.some(([path]) => /connections|domain/.test(path)));
}
for (const mutation of [
  { id: 'different-plan' }, { target: 'aws' }, { project_id: 'other-project' },
  { fingerprint: 'changed' }, { status: 'superseded' }, { terraform_plan_ready: false },
]) {
  const calls = [];
  const { local } = localAdapter(async (path, init) => {
    calls.push([path, init]);
    if (path.startsWith('/deployments?')) return [];
    return { ...localPlan(), ...mutation };
  });
  await assert.rejects(local.approveLocal({ projectId: 'project-1', planId: 'local-1', fingerprint: 'reviewed-fingerprint', target: 'local_docker' }));
  assert(!calls.some(([, init]) => init), `Mutation ${JSON.stringify(mutation)} must never write`);
}
{
  const { local } = localAdapter(async () => [{ ...localPlan(), target: 'aws' }, { ...localPlan(), project_id: 'other-project' }]);
  assert.equal(await local.recommendLocal('project-1'), null, 'AWS/other-project plans never satisfy a local wait');
}
{
  let resumeFirst;
  let count = 0;
  const { local } = localAdapter(async () => {
    if (++count === 1) return new Promise((resolve) => { resumeFirst = resolve; });
    return [{ ...localPlan(), id: 'new-plan' }];
  });
  const operations = new local.Operations();
  let visible;
  const firstCurrent = operations.next();
  const first = local.recommendLocal('project-1').then((review) => { if (firstCurrent()) visible = review; });
  const secondCurrent = operations.next();
  const second = await local.recommendLocal('project-1');
  if (secondCurrent()) visible = second;
  resumeFirst([localPlan()]); await first;
  assert.equal(visible.reference.planId, 'new-plan', 'Same-project late response must not replace the new review');
  const beforeReset = operations.next();
  operations.invalidate();
  assert.equal(beforeReset(), false, 'Reset must invalidate late file/review responses');
}
console.log('PASS: local-only selection, immutable approval, AWS cache isolation, 6 changed-plan rejections, same-project late response/reset');
{
  let called;
  const { local } = localAdapter(async (path, init) => { called = [path, init]; return {}; });
  await local.readiness();
  assert.deepEqual(called, ['/onprem/readiness', { timeoutMs: 3000 }]);
}
{
  const calls = [];
  const { local } = localAdapter(async (path, init) => {
    calls.push([path, init]);
    if (path.startsWith('/deployments?')) return [];
    return { ...localPlan(), ...(path.endsWith('/approve') ? { target: 'aws', status: 'approved' } : {}) };
  });
  await assert.rejects(local.approveLocal({ projectId: 'project-1', planId: 'local-1', fingerprint: 'reviewed-fingerprint', target: 'local_docker' }));
  assert(!calls.some(([path, init]) => path === '/deployments' && init), 'Changed approval response cannot deploy');
}
console.log('PASS: readiness 3-second timeout and changed approval response rejected');
