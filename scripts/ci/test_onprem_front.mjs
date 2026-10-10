// Deterministic HTTP fixtures only; this does not prove Docker deployment.
import assert from 'node:assert/strict';
import { parseArgs } from 'node:util';
import { launchBrowser } from './browser.mjs';
const { values } = parseArgs({ options: { url: { type: 'string' } } });
const base = new URL(values.url);
assert(base.protocol === 'http:' && ['127.0.0.1', '[::1]'].includes(base.hostname), 'Loopback HTTP URL required');
const browser = await launchBrowser();
const calls = [];
const errors = [];
let health = 'ready';
let failedComponent = 'worker';
let deploymentState = 'running';
let fingerprint = 'fingerprint-1';
let approved = false;
let deployed = false;
const plan = () => ({ id: 'plan-1', project_id: 'project-1', target: 'local_docker', module_id: 'local-docker-compose',
  status: approved ? 'approved' : 'awaiting_approval', created_at: '2026-10-11T00:00:00Z',
  fingerprint, terraform_plan_ready: true, summary: 'Fixture local plan', cost_estimate: { amount: '0' },
  variables: { compose_yaml: 'services:\n  app:\n    image: fixture/app\n', source_sha256: 'a'.repeat(64) } });
const countWrites = () => calls.filter((call) => call.method === 'POST').length;
const selector = (name) => `[data-testid="${name}"]`;
const click = (name) => browser.evaluate(`document.querySelector(${JSON.stringify(selector(name))}).click()`);
const waitHealth = (label) => browser.waitFor(`document.querySelector('[data-testid=readiness]')?.textContent === ${JSON.stringify(label)}`, label);
try {
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: `${base.origin}/api/*` }] });
  browser.onEvent((method, params) => {
    if (method === 'Runtime.exceptionThrown') errors.push(params.exceptionDetails.text);
    if (method !== 'Fetch.requestPaused') return;
    void (async () => {
      const url = new URL(params.request.url);
      const path = url.pathname;
      calls.push({ path, method: params.request.method });
      let body; let code = 200;
      if (path === '/api/onprem/readiness') {
        if (health === 'unreachable') return await browser.send('Fetch.failRequest', { requestId: params.requestId, errorReason: 'ConnectionFailed' });
        body = { status: health, checked_at: new Date().toISOString(), expires_at: new Date(Date.now() + 15000).toISOString(), analysis_mode: 'rules', target: 'local_docker',
          components: Object.fromEntries(['api', 'db', 'docker', 'analyzer', 'worker'].map((name) => [name, { status: name === failedComponent ? health : 'ready' }])),
          reasons: health === 'ready' ? [] : [{ code: 'fixture', message: `${failedComponent} ${health}`, action: '운영자에게 재시작을 요청해 주세요.' }] };
      } else if (path === '/api/projects' && params.request.method === 'POST') body = { id: 'project-1' };
      else if (path === '/api/projects/project-1/analyses/latest') body = { project_id: 'project-1', result: { stack: [{ label: '분석 모드', value: 'rules' }], findings: [{ level: 'info', title: 'DB 접속 정보는 배포가 넣습니다', detail: '공용 MySQL에 앱 전용 DB와 계정을 만들고 DATABASE_URL을 비밀 저장소로 주입합니다.' }, { level: 'warn', title: '보존할 분석 경고', detail: '별도 헬스체크 경로를 확인해 주세요.' }] } };
      else if (path === '/api/projects/project-1/plans') body = [plan(), { ...plan(), id: 'aws-plan', target: 'aws' }];
      else if (path === '/api/plans/plan-1') body = plan();
      else if (path === '/api/plans/plan-1/approve') { assert.equal(JSON.parse(params.request.postData).expected_fingerprint, fingerprint); approved = true; body = plan(); }
      else if (path === '/api/deployments' && params.request.method === 'GET') body = deployed ? [{ plan_id: 'plan-1' }] : [];
      else if (path === '/api/deployments' && params.request.method === 'POST') { deployed = true; body = {}; }
      else if (path === '/api/projects/project-1/status') body = { state: deploymentState, log: ['Fixture Docker build'], url: deploymentState === 'success' ? 'http://127.0.0.1:18081' : null };
      else { errors.push(`Unexpected API ${path}`); code = 404; body = { error: 'Unexpected fixture request' }; }
      await browser.send('Fetch.fulfillRequest', { requestId: params.requestId, responseCode: code,
        responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body: Buffer.from(JSON.stringify(body)).toString('base64') });
    })().catch((error) => errors.push(error.message));
  });
  await browser.send('Page.navigate', { url: new URL('/onprem', base).href });
  await waitHealth('준비됨');
  await browser.evaluate(`(() => { const input = document.querySelector('[data-testid=source-zip]'); const transfer = new DataTransfer(); transfer.items.add(new File(['fixture'], 'sample.zip', {type:'application/zip'})); input.files = transfer.files; input.dispatchEvent(new Event('change', {bubbles:true})); })()`);
  await browser.waitFor("!document.querySelector('[data-testid=analyze]').disabled", 'analysis enabled');
  for (const [state, label, component] of [['starting', '시작 중', 'analyzer'], ['down', '중단', 'docker'], ['stale', '상태 만료', 'worker'], ['unconfigured', '설정 필요', 'analyzer'], ['unreachable', '연결 확인 불가', 'api']]) {
    health = state; failedComponent = component;
    await waitHealth(label);
    assert(await browser.evaluate("document.querySelector('[data-testid=analyze]').disabled"));
    assert.equal(await browser.evaluate("document.querySelector('[data-testid=source-zip]').files[0].name"), 'sample.zip');
    assert.equal(countWrites(), 0, 'Readiness changes cannot submit source');
  }
  health = 'ready'; await waitHealth('준비됨');
  await click('analyze');
  await browser.waitFor("!!document.querySelector('[data-testid=review]')", 'local review');
  assert.equal(countWrites(), 1, 'No approval or deployment before consent');
  const analysisCopy = await browser.evaluate('document.querySelector(".onprem-main").innerText');
  assert(!/공용 MySQL|비밀 저장소|Secrets Manager/.test(analysisCopy), 'Local analysis must not claim shared AWS DB or secrets-manager injection');
  assert(analysisCopy.includes('앱 전용 로컬 MySQL 컨테이너') && analysisCopy.includes('secrets/app.env·secrets/db.env'), 'Local DB and env-file delivery must be explicit');
  assert(analysisCopy.includes('별도 헬스체크 경로를 확인해 주세요.'), 'Unrelated analysis findings must remain intact');
  await click('review-confirm');
  health = 'down'; await waitHealth('중단');
  assert(await browser.evaluate("document.querySelector('[data-testid=approve]').disabled"));
  assert(await browser.evaluate("document.querySelector('[data-testid=review]').textContent.includes('fingerprint-1')"));
  health = 'ready'; await waitHealth('준비됨');
  assert.equal(countWrites(), 1, 'Recovery never auto-approves');
  fingerprint = 'fingerprint-2';
  await click('approve');
  await browser.waitFor("document.querySelector('[role=alert]')?.textContent.includes('바뀌었습니다')", 'stale review rejected');
  assert.equal(countWrites(), 1, 'Changed fingerprint cannot write approval');
  await browser.evaluate("[...document.querySelectorAll('button')].find(b => b.textContent === '계획 다시 조회').click()");
  await browser.waitFor("document.querySelector('[data-testid=review]')?.textContent.includes('fingerprint-2')", 'fresh review');
  assert.equal(await browser.evaluate("document.querySelector('[data-testid=review-confirm]').checked"), false);
  await click('review-confirm'); await click('approve');
  await browser.waitFor("!!document.querySelector('[data-testid=deployment]')", 'deployment registered');
  assert.equal(countWrites(), 3);
  health = 'stale'; await waitHealth('상태 만료');
  const previousPolls = calls.filter((call) => call.path.endsWith('/status')).length;
  await new Promise((resolve) => setTimeout(resolve, 3500));
  assert(calls.filter((call) => call.path.endsWith('/status')).length > previousPolls, 'Deployment polling must continue when service is non-ready');
  deploymentState = 'success';
  await browser.waitFor("document.querySelector('[data-testid=deployment-state]')?.textContent === '앱 HTTP 헬스 확인 완료'", 'HTTP health result');
  assert.equal(countWrites(), 3, 'No duplicate approval/deploy on readiness changes');
  assert(!calls.some((call) => /connections|domain/.test(call.path)), 'Local route must issue zero AWS connection/domain requests');
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert(await browser.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile page must not overflow horizontally');
  assert.deepEqual(errors, []);
  console.log('PASS onprem HTTP-fixture browser: readiness states, preserved inputs/review, stale approval, manual recovery, continuing deployment polling, zero AWS/domain calls, accurate local DB copy, mobile layout');
} finally { await browser.close(); }
