// Real deployment only. Caller owns launcher, sample archive, and exact-resource cleanup.
// node scripts/ci/test_onprem_deploy.mjs --url http://127.0.0.1:15173 --zip /absolute/sample.zip --output /tmp/result.json
import assert from 'node:assert/strict';
import { parseArgs } from 'node:util';
import { writeFile, stat } from 'node:fs/promises';
import { isAbsolute } from 'node:path';
import { launchBrowser } from './browser.mjs';
const { values } = parseArgs({ options: { url: { type: 'string' }, zip: { type: 'string' }, output: { type: 'string' }, 'timeout-seconds': { type: 'string', default: '1800' } } });
const base = new URL(values.url);
assert(base.protocol === 'http:' && ['127.0.0.1', '[::1]'].includes(base.hostname), 'Loopback platform HTTP origin required');
assert(values.zip && isAbsolute(values.zip) && (await stat(values.zip)).isFile(), 'Existing absolute sample ZIP required');
assert(values.output && isAbsolute(values.output), 'Absolute evidence output path required');
const timeoutMs = Number(values['timeout-seconds']) * 1000;
assert(Number.isFinite(timeoutMs) && timeoutMs >= 30000 && timeoutMs <= 3600000, 'Timeout must be 30–3600 seconds');
const browser = await launchBrowser({ timeoutMs });
const calls = [];
const errors = [];
const until = Date.now() + timeoutMs - 5000;
async function wait(expression, label) {
  while (Date.now() < until) {
    if (await browser.evaluate(expression)) return;
    const error = await browser.evaluate("document.querySelector('.onprem-error')?.textContent");
    if (error) throw new Error(`${label}: ${error}`);
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`Timeout: ${label}`);
}
try {
  browser.onEvent((method, params) => {
    if (method === 'Runtime.exceptionThrown') errors.push(params.exceptionDetails.text);
    if (method === 'Network.requestWillBeSent') {
      const url = new URL(params.request.url);
      if (url.origin === base.origin && url.pathname.startsWith('/api/')) calls.push({ path: url.pathname, method: params.request.method });
    }
  });
  await browser.send('Page.navigate', { url: new URL('/onprem', base).href });
  await wait("document.querySelector('[data-testid=readiness]')?.textContent === '준비됨'", 'real service ready');
  const { root } = await browser.send('DOM.getDocument');
  const { nodeId } = await browser.send('DOM.querySelector', { nodeId: root.nodeId, selector: '[data-testid=source-zip]' });
  await browser.send('DOM.setFileInputFiles', { nodeId, files: [values.zip] });
  await wait("!document.querySelector('[data-testid=analyze]').disabled", 'sample ZIP selected');
  await browser.evaluate("document.querySelector('[data-testid=analyze]').click()");
  await wait("!!document.querySelector('[data-testid=review]')", 'real rule analysis and local plan');
  assert(!calls.some((call) => call.path.endsWith('/approve') || (call.path === '/api/deployments' && call.method === 'POST')), 'No approval/deploy before UI consent');
  const reviewShot = await browser.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
  await writeFile(`${values.output}.review.png`, Buffer.from(reviewShot.data, 'base64'));
  const review = await browser.evaluate("document.querySelector('[data-testid=review]').innerText");
  assert(review.includes('docker-compose.yml') && review.includes('local-plan.variables.json'));
  await browser.evaluate("document.querySelector('[data-testid=review-confirm]').click()");
  await wait("!document.querySelector('[data-testid=approve]').disabled", 'review ready for explicit approval');
  await browser.evaluate("document.querySelector('[data-testid=approve]').click()");
  await wait("['앱 HTTP 헬스 확인 완료', '배포 실패'].includes(document.querySelector('[data-testid=deployment-state]')?.textContent)", 'real Docker deployment terminal state');
  const state = await browser.evaluate("document.querySelector('[data-testid=deployment-state]').textContent");
  const body = await browser.evaluate("document.querySelector('.onprem-main').innerText");
  const appUrl = await browser.evaluate("document.querySelector('[data-testid=app-url]')?.href");
  const terminalShot = await browser.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
  await writeFile(`${values.output}.terminal.png`, Buffer.from(terminalShot.data, 'base64'));
  await writeFile(values.output, JSON.stringify({ state, appUrl, review, calls, body, errors }, null, 2));
  assert.equal(state, '앱 HTTP 헬스 확인 완료', body);
  assert(appUrl?.startsWith('http://'), 'Actual app HTTP URL required');
  assert(!calls.some((call) => /connections|domain/.test(call.path)), 'Zero AWS/domain API calls required');
  assert.deepEqual(errors, []);
  console.log(`PASS real onprem UI deployment: ${appUrl}. Evidence: ${values.output}`);
  const sampleTestUrl = new URL(appUrl);
  if (sampleTestUrl.hostname === 'localhost') sampleTestUrl.hostname = '127.0.0.1';
  console.log(`샘플 가입·로그인·저장 검증: node scripts/ci/test_sample_front.mjs --mode real --url ${sampleTestUrl.origin}`);
} finally { await browser.close(); }
