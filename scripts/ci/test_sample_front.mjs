import assert from 'node:assert/strict';
import { parseArgs } from 'node:util';
import { launchBrowser } from './browser.mjs';

const { values } = parseArgs({ options: { url: { type: 'string' }, mode: { type: 'string' } } });
assert(['mock', 'real'].includes(values.mode), 'Use --mode mock|real');
const base = new URL(values.url);
assert(base.protocol === 'http:' && ['127.0.0.1', '[::1]'].includes(base.hostname)
  && !base.username && !base.password && base.pathname === '/' && !base.search && !base.hash
  && /^http:\/\/(?:127\.0\.0\.1|\[::1\])(?::\d+)?\/?$/.test(values.url),
  '--url must be a loopback HTTP origin (127.0.0.1 or [::1]), without credentials, path, query, or fragment');
const real = values.mode === 'real';
const browser = await launchBrowser();
const errors = [];
const apiResponses = [];
const requests = new Map();
browser.onEvent((method, params) => {
  if (method === 'Runtime.exceptionThrown') errors.push(params.exceptionDetails.exception?.description || params.exceptionDetails.text);
  if (method === 'Runtime.consoleAPICalled' && params.type === 'error') errors.push('Application console.error');
  if (method === 'Network.requestWillBeSent') requests.set(params.requestId, params.request);
  if (method === 'Network.responseReceived') {
    const { response, type, requestId } = params;
    const path = new URL(response.url).pathname;
    if (path.startsWith('/api/')) {
      apiResponses.push({ path, method: requests.get(requestId)?.method, status: response.status, mime: response.mimeType });
      if (real && response.status !== 204 && !response.mimeType.includes('application/json')) errors.push(`Non-JSON API: ${path}`);
      if (real && response.status >= 400 && !(response.status === 401 && ['/api/me', '/api/login'].includes(path))) errors.push(`API failed: ${path} ${response.status}`);
    } else if (['Document', 'Script', 'Stylesheet'].includes(type) && response.status >= 400) {
      errors.push(`Required asset failed: ${path} ${response.status}`);
    }
  }
  if (method === 'Network.loadingFailed' && ['Document', 'Script', 'Stylesheet'].includes(params.type)) errors.push(`Asset network failure: ${params.errorText}`);
});

const q = JSON.stringify;
const evaluate = browser.evaluate;
const wait = browser.waitFor;
async function modeCheck() {
  await wait("document.querySelector('#host code')?.textContent", 'hostname rendered');
  const state = await evaluate(`({ banner: !!document.querySelector('#mock-banner'), host: document.querySelector('#host code').textContent,
    storage: Object.keys(localStorage).some(key => key.startsWith('launchpad-mock-')),
    everMock: sessionStorage.getItem('ci-saw-mock') === 'yes' })`);
  if (real) {
    assert.equal(state.banner, false, 'Real API must not fall back to mock');
    assert.equal(state.everMock, false, 'Mock fallback occurred on an earlier page');
    assert.equal(state.storage, false, 'Real API must not create mock storage');
    assert.notEqual(state.host, 'mock-browser');
  } else {
    assert.equal(state.banner, true, 'MOCK banner must be visible');
    assert.equal(state.host, 'mock-browser');
  }
  assert.deepEqual(errors, [], 'Browser errors');
}
async function board(loggedIn) {
  await wait(`document.body?.dataset.page === 'board' && document.querySelectorAll('#stats dd').length === 3 &&
    ${loggedIn ? "!!document.querySelector('#title')" : "!!document.querySelector('#nav a[href=\"login.html\"]')"}`, 'board initialized');
  await modeCheck();
}
async function auth(page) {
  await wait(`document.body?.dataset.page === ${q(page)} && !!document.querySelector('#nav a')`, `${page} initialized`);
  await modeCheck();
}
async function click(selector) {
  await evaluate(`document.querySelector(${q(selector)}).click()`);
}
async function fill(fields, form) {
  await evaluate(`(() => { for (const [id, value] of Object.entries(${q(fields)})) {
    const input = document.getElementById(id); input.value = value; input.dispatchEvent(new Event('input', { bubbles: true }));
  } document.querySelector(${q(form)}).requestSubmit(); })()`);
}
async function newDocument(method, params = {}) {
  await evaluate('window.ciPreviousDocument = true');
  await browser.send(method, params);
  await wait('!window.ciPreviousDocument && document.readyState === "complete"', 'new document loaded');
}

try {
  // Remember any fallback across navigations, including a transient banner.
  await browser.send('Page.addScriptToEvaluateOnNewDocument', { source: `new MutationObserver(() => {
    if (document.getElementById('mock-banner')) sessionStorage.setItem('ci-saw-mock', 'yes');
  }).observe(document, { childList: true, subtree: true });` });
  await browser.send('Page.navigate', { url: new URL('index.html', base).href });
  await board(false);
  if (await evaluate("!!document.querySelector('.vote')")) {
    await click('.vote');
    await auth('login');
    assert.match(await evaluate("document.querySelector('#flash').textContent"), /로그인/);
  }
  await click('#nav a[href="signup.html"]');
  await auth('signup');
  const suffix = `${Date.now()}-${process.pid}`;
  const email = `ci-${suffix}@example.com`;
  const password = 'CI-only-password-2026';
  await fill({ name: 'CI 사용자', email, password }, '#auth-form');
  await board(true);
  await newDocument('Page.navigate', { url: new URL('login.html', base).href });
  await board(true); // Existing session redirects back from the login page.
  const title = `CI ${suffix} <b>제목</b>`;
  const description = '<img src=x onerror="window.ciInjected=true"> & 내용';
  await fill({ title, description }, '#new-idea form');
  const idea = `[...document.querySelectorAll('#ideas .idea')].find(node => node.querySelector('h3').textContent === ${q(title)})`;
  await wait(`!!(${idea})`, 'created idea visible');
  assert.equal(await evaluate(`(${idea}).querySelector('.idea-body > p').textContent`), description);
  assert.equal(await evaluate(`!!(${idea}).querySelector('h3 b, img') || !!window.ciInjected`), false, 'Input must render as text');
  const vote = `(${idea}).querySelector('.vote')`;
  const initialVotes = await evaluate(`Number((${vote}).lastElementChild.textContent)`);
  await evaluate(`(${vote}).click()`);
  await wait(`(${vote}).getAttribute('aria-pressed') === 'true' && Number((${vote}).lastElementChild.textContent) === ${initialVotes + 1}`, 'vote increment');
  await newDocument('Page.reload');
  await board(true);
  await wait(`!!(${idea}) && (${vote}).getAttribute('aria-pressed') === 'true'`, 'vote persisted after reload');
  await evaluate(`(${vote}).click()`);
  await wait(`(${vote}).getAttribute('aria-pressed') === 'false' && Number((${vote}).lastElementChild.textContent) === ${initialVotes}`, 'vote removed');
  await click('#nav button');
  await board(false);
  await click('#nav a[href="login.html"]');
  await auth('login');
  await fill({ email, password: 'wrong-password' }, '#auth-form');
  await wait("!document.querySelector('#auth-error').hidden && !!document.querySelector('#auth-error').textContent", 'wrong password error');
  await modeCheck();
  await fill({ email, password }, '#auth-form');
  await board(true);
  await newDocument('Page.reload');
  await board(true);
  await wait(`!!(${idea}) && (${vote}).getAttribute('aria-pressed') === 'false'`, 'idea and unvote persisted');
  if (real) {
    for (const [method, path, status] of [['GET', '/api/me', 401], ['GET', '/api/me', 200],
      ['GET', '/api/ideas', 200], ['GET', '/api/info', 200], ['POST', '/api/signup', 201],
      ['POST', '/api/ideas', 201], ['POST', '/api/logout', 204], ['POST', '/api/login', 401], ['POST', '/api/login', 200]]) {
      assert(apiResponses.some(response => response.method === method && response.path === path && response.status === status), `Missing API response: ${method} ${path} ${status}`);
    }
    assert(apiResponses.filter(response => /\/vote$/.test(response.path) && response.status === 200).length >= 2);
  }
  await modeCheck();
  console.log(`PASS sample-front (${values.mode}): signup, session, escaped text, vote/unvote, logout, login errors, reload persistence`);
} finally {
  await browser.close();
}
