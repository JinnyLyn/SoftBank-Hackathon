// Deterministic HTTP fixtures only; this does not prove Docker deployment.
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';
import { launchBrowser } from './browser.mjs';
const { values } = parseArgs({ options: { url: { type: 'string' }, screenshot: { type: 'string' } } });
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
let amount = '0';
const uploads = [];
const plan = () => ({ id: 'plan-1', project_id: 'project-1', target: 'local_docker', module_id: 'local-docker-compose',
  status: approved ? 'approved' : 'awaiting_approval', created_at: '2026-10-11T00:00:00Z',
  fingerprint, terraform_plan_ready: true, summary: '작게 시작 구성: 앱 컨테이너 1개\n자원 한도: CPU 0.5, 메모리 512MB\n배포 위치: 이 PC의 Docker (HTTP)', cost_estimate: { amount },
  variables: { compose_yaml: '# 앱 컨테이너 구성\nservices:\n  app:\n    image: "fixture/app"\n', source_sha256: 'a'.repeat(64) } });
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
      } else if (path === '/api/projects' && params.request.method === 'POST') {
        uploads.push(Object.fromEntries(url.searchParams)); body = { id: 'project-1' };
      } else if (path === '/api/projects/github' && params.request.method === 'POST') {
        uploads.push(JSON.parse(params.request.postData)); body = { id: 'project-1' };
      }
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
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 960, deviceScaleFactor: 1, mobile: false });
  await browser.send('Page.navigate', { url: new URL('/onprem', base).href });
  await waitHealth('준비됨');
  assert.equal(await browser.evaluate("document.querySelector('[data-testid=analyze]').textContent"), '구성 분석 및 로컬 계획 만들기');
  assert.equal(await browser.evaluate("document.querySelectorAll('.onprem-fields select, .onprem-fields textarea, .onprem-fields input[type=number]').length"), 0, 'Local source form must not contain cloud scale/budget controls or dropdowns');
  assert(!await browser.evaluate("/월 예산|예상 사용자|접속 패턴|앱 용도/.test(document.querySelector('.onprem-fields').innerText)"));
  await click('source-github');
  await browser.waitFor("!!document.querySelector('[data-testid=source-url]')", 'GitHub source selected');
  assert.equal(await browser.evaluate("document.querySelector('[data-testid=source-github]').getAttribute('aria-pressed')"), 'true');
  assert(await browser.evaluate("document.querySelector('[data-testid=analyze]').disabled"), 'Empty GitHub URL cannot submit');
  await click('source-zip-choice');
  await browser.waitFor("!!document.querySelector('[data-testid=source-zip]')", 'ZIP source selected');
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
  assert(await browser.evaluate("[...document.querySelectorAll('h2')].some(h => h.textContent === '2. 구성 분석 결과')"));
  const codeStyles = await browser.evaluate(`(() => {
    const review = document.querySelector('[data-testid=review]');
    const code = review.querySelector('.code');
    const style = getComputedStyle(code);
    const background = style.backgroundColor === 'rgba(0, 0, 0, 0)' ? getComputedStyle(review).backgroundColor : style.backgroundColor;
    return { background, fontSize: parseFloat(style.fontSize), lineHeight: parseFloat(style.lineHeight),
      colors: [...review.querySelectorAll('.code, .ln, .tk-string, .tk-comment')].map(el => getComputedStyle(el).color),
      summaryWhitespace: getComputedStyle(review.querySelector('.onprem-review-summary')).whiteSpace };
  })()`);
  const luminance = (color) => color.match(/[\d.]+/g).slice(0, 3).map(Number).map((v) => {
    v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  }).reduce((total, v, i) => total + v * [0.2126, 0.7152, 0.0722][i], 0);
  const bg = luminance(codeStyles.background);
  for (const color of codeStyles.colors) {
    const fg = luminance(color);
    assert((Math.max(bg, fg) + 0.05) / (Math.min(bg, fg) + 0.05) >= 4.5, `Readable code/token/line-number contrast: ${color} on ${codeStyles.background}`);
  }
  assert(codeStyles.fontSize >= 13 && codeStyles.lineHeight / codeStyles.fontSize >= 1.7);
  assert.equal(codeStyles.summaryWhitespace, 'pre-line', 'Preserve summary line breaks');
  if (values.screenshot) {
    await browser.evaluate("document.querySelector('[data-testid=review]').scrollIntoView()");
    const shot = await browser.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    await writeFile(values.screenshot, Buffer.from(shot.data, 'base64'));
  }
  assert.equal(countWrites(), 1, 'No approval or deployment before consent');
  assert.equal(uploads[0].expected_users, '~100');
  assert.equal(uploads[0].traffic_pattern, 'steady');
  assert.equal(uploads[0].monthly_budget_usd, '0');
  assert(!('purpose' in uploads[0]), 'No unused app-purpose field is sent');
  const analysisCopy = await browser.evaluate('document.querySelector(".onprem-main").innerText');
  assert(!/공용 MySQL|비밀 저장소|Secrets Manager/.test(analysisCopy), 'Local analysis must not claim shared AWS DB or secrets-manager injection');
  assert(analysisCopy.includes('앱 전용 로컬 MySQL 컨테이너') && analysisCopy.includes('secrets/app.env·secrets/db.env'), 'Local DB and env-file delivery must be explicit');
  assert(analysisCopy.includes('별도 헬스체크 경로를 확인해 주세요.'), 'Unrelated analysis findings must remain intact');
  for (const invalid of ['1', '-1', 'not-a-number']) {
    amount = invalid;
    await browser.evaluate("[...document.querySelectorAll('button')].find(b => b.textContent === '계획 다시 조회').click()");
    await browser.waitFor("!!document.querySelector('[data-testid=plan-cost-error]')", 'Invalid/nonzero local hosting cost rejected');
    await click('review-confirm');
    assert(await browser.evaluate("document.querySelector('[data-testid=approve]').disabled"));
    assert.equal(countWrites(), 1, 'Invalid local cost cannot trigger approval');
  }
  amount = '0';
  await browser.evaluate("[...document.querySelectorAll('button')].find(b => b.textContent === '계획 다시 조회').click()");
  await browser.waitFor("!!document.querySelector('[data-testid=review]') && !document.querySelector('[data-testid=plan-cost-error]')", 'Zero-cost local plan restored');
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
  health = 'ready'; await waitHealth('준비됨');
  await click('new-analysis');
  await click('source-github');
  await browser.waitFor("!!document.querySelector('[data-testid=source-url]')", 'GitHub input after reset');
  await browser.evaluate(`(() => {
    const input = document.querySelector('[data-testid=source-url]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'https://github.com/example/sample');
    input.dispatchEvent(new Event('input', {bubbles: true}));
  })()`);
  await browser.waitFor("!document.querySelector('[data-testid=analyze]').disabled", 'Valid GitHub source needs no cloud inputs');
  approved = false; deployed = false;
  await click('analyze');
  await browser.waitFor("!!document.querySelector('[data-testid=review]')", 'GitHub local plan');
  assert.equal(uploads[1].repository_url, 'https://github.com/example/sample');
  assert.equal(uploads[1].ref, 'main');
  assert.equal(uploads[1].expected_users, '~100');
  assert.equal(uploads[1].traffic_pattern, 'steady');
  assert.equal(uploads[1].monthly_budget_usd, 0);
  assert(!('purpose' in uploads[1]));
  assert.equal(countWrites(), 4, 'New source cannot auto-approve or redeploy');
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert(await browser.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile page must not overflow horizontally');
  assert.deepEqual(errors, []);
  console.log('PASS onprem HTTP-fixture browser: configuration-analysis labels, readable review/code contrast, source-only form, local defaults/zero-cost gate, readiness, immutable approval, zero AWS/domain calls, mobile layout');
} finally { await browser.close(); }
