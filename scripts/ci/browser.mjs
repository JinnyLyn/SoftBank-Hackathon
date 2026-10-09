import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

// A single owned Chrome process and tab; no npm/browser driver dependency.
export async function launchBrowser() {
  const profile = await mkdtemp(join(tmpdir(), 'launchpad-ci-'));
  let child;
  let socket;
  let launchError;
  let stderr = '';
  const pending = new Map();
  const listeners = new Set();
  let sequence = 0;
  let closing;
  const stop = async (reason, code) => {
    console.error(reason);
    try { await close(); } finally { process.exit(code); }
  };
  const interrupt = () => { void stop('Browser smoke interrupted (SIGINT)', 130); };
  const terminate = () => { void stop('Browser smoke interrupted (SIGTERM)', 143); };
  process.once('SIGINT', interrupt);
  process.once('SIGTERM', terminate);
  const overallDeadline = setTimeout(() => { void stop('Browser smoke exceeded 120 seconds', 1); }, 120000);
  function close() {
    closing ??= cleanup();
    return closing;
  }
  async function cleanup() {
    socket?.close();
    for (const { reject, timer } of pending.values()) {
      clearTimeout(timer);
      reject(new Error('Browser closed'));
    }
    pending.clear();
    if (child?.pid && child.exitCode === null && child.signalCode === null) {
      child.kill('SIGTERM');
      for (let i = 0; i < 30 && child.exitCode === null && child.signalCode === null; i++) await delay(100);
      if (child.exitCode === null && child.signalCode === null) {
        await new Promise((resolve) => {
          child.once('exit', resolve);
          child.kill('SIGKILL');
        });
      }
    }
    await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
    clearTimeout(overallDeadline);
    process.removeListener('SIGINT', interrupt);
    process.removeListener('SIGTERM', terminate);
  }
  try {
    const candidates = process.env.CHROME_BIN ? [process.env.CHROME_BIN] : ['google-chrome', 'chromium', 'chromium-browser'];
    for (const binary of candidates) {
      launchError = undefined;
      child = spawn(binary, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
        '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
      child.stderr.on('data', (data) => { stderr = (stderr + data).slice(-4000); });
      child.on('error', (error) => { launchError = error; });
      await new Promise((resolve) => { child.once('spawn', resolve); child.once('error', resolve); });
      if (!launchError) break;
    }
    if (launchError) throw launchError;
    let port;
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      if (child.exitCode !== null || child.signalCode !== null) throw new Error(`Chrome exited: ${stderr}`);
      try { port = Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]); } catch { /* Chrome is starting. */ }
      if (port) break;
      await delay(100);
    }
    if (!port) throw new Error(`Chrome readiness timeout: ${stderr}`);
    const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(5000) })).json();
    const target = targets.find((entry) => entry.type === 'page');
    if (!target) throw new Error('Chrome did not create a page');
    socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Chrome WebSocket timeout')), 5000);
      socket.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
      socket.addEventListener('error', () => { clearTimeout(timer); reject(new Error('Chrome WebSocket failed')); }, { once: true });
    });
    socket.addEventListener('message', ({ data }) => {
      const message = JSON.parse(data);
      if (message.id) {
        const request = pending.get(message.id);
        if (!request) return;
        pending.delete(message.id);
        clearTimeout(request.timer);
        if (message.error) request.reject(new Error(message.error.message));
        else request.resolve(message.result);
      } else {
        for (const listener of listeners) listener(message.method, message.params);
      }
    });
    const send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 10000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    });
    const evaluate = async (expression) => {
      const response = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
      return response.result.value;
    };
    const waitFor = async (expression, label) => {
      const deadline = Date.now() + 10000;
      while (Date.now() < deadline) {
        try { if (await evaluate(expression)) return; } catch (error) {
          if (!/context.*destroyed|Cannot find context/i.test(error.message)) throw error;
        }
        await delay(50);
      }
      throw new Error(`Timed out: ${label}`);
    };
    await send('Page.enable');
    await send('Runtime.enable');
    await send('Network.enable');
    return { send, evaluate, waitFor, close, onEvent: (listener) => listeners.add(listener) };
  } catch (error) {
    await close();
    throw error;
  }
}
