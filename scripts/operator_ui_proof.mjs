#!/usr/bin/env node
/**
 * Browser-driven operator proof for the actual NEXUS command center.
 * Required environment: NEXUS_UI_PROOF_URL, NEXUS_UI_PROOF_EMAIL,
 * NEXUS_UI_PROOF_PASSWORD. The script uses Chromium DevTools only to drive
 * visible product controls; it does not call NEXUS APIs directly.
 */
import { spawn } from 'node:child_process';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const url = process.env.NEXUS_UI_PROOF_URL;
const email = process.env.NEXUS_UI_PROOF_EMAIL;
const password = process.env.NEXUS_UI_PROOF_PASSWORD;
if (!url || !email || !password) throw new Error('NEXUS_UI_PROOF_URL, NEXUS_UI_PROOF_EMAIL, and NEXUS_UI_PROOF_PASSWORD are required');

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const profile = await mkdtemp(join(tmpdir(), 'nexus-ui-proof-'));
const browser = spawn('chromium', ['--headless=new', '--no-sandbox', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=9228', `--user-data-dir=${profile}`, url], { stdio: 'ignore' });

async function waitForTarget() {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch('http://127.0.0.1:9228/json/list');
      const targets = await response.json();
      const page = targets.find((target) => target.type === 'page');
      if (page?.webSocketDebuggerUrl) return page;
    } catch { /* Chromium is still starting. */ }
    await delay(200);
  }
  throw new Error('Chromium DevTools did not start');
}

try {
  const target = await waitForTarget();
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let sequence = 0;
  const pending = new Map();
  socket.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      message.error ? reject(new Error(message.error.message)) : resolve(message.result);
    }
  };
  const evaluate = async (expression) => {
    const id = ++sequence;
    const response = await new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression, awaitPromise: true, returnByValue: true } }));
    });
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.text);
    return response.result?.value;
  };
  const eventually = async (expression, description) => {
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      if (await evaluate(expression)) return;
      await delay(300);
    }
    throw new Error(`Timed out waiting for ${description}`);
  };
  const setInput = (selector, value) => evaluate(`(() => { const input = document.querySelector(${JSON.stringify(selector)}); if (!input) return false; const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value').set; setter.call(input, ${JSON.stringify(value)}); input.dispatchEvent(new Event('input', { bubbles: true })); input.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`);

  await eventually(`Boolean(document.querySelector('input[type="email"]'))`, 'NEXUS login form');
  if (!await setInput('input[type="email"]', email)) throw new Error('Email control unavailable');
  if (!await setInput('input[type="password"]', password)) throw new Error('Password control unavailable');
  await evaluate(`Array.from(document.querySelectorAll('button')).find((button) => button.textContent.includes('Open secure workspace'))?.click()`);
  await eventually(`document.body.innerText.includes('OBJECTIVE')`, 'authenticated command workspace');
  await setInput('input[placeholder="new project"]', 'ui-operator-proof');
  await evaluate(`Array.from(document.querySelectorAll('button')).find((button) => button.textContent.trim() === 'create project')?.click()`);
  await eventually(`Array.from(document.querySelectorAll('select option')).some((option) => option.value === 'ui-operator-proof')`, 'project creation through UI');
  await evaluate(`(() => { const select = document.querySelector('select[aria-label="Active tenant project"]'); select.value = 'ui-operator-proof'; select.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await evaluate(`Array.from(document.querySelectorAll('.capability-row')).filter((row) => row.textContent.includes('Metadata')).forEach((row) => row.querySelector('input')?.click())`);
  await evaluate(`Array.from(document.querySelectorAll('.capability-row')).filter((row) => row.textContent.includes('Local evidence')).forEach((row) => { const input = row.querySelector('input'); if (!input.checked) input.click(); })`);
  await evaluate(`Array.from(document.querySelectorAll('.mode-switch button')).find((button) => button.textContent.includes('Real read'))?.click()`);
  await evaluate(`Array.from(document.querySelectorAll('button')).find((button) => button.textContent.includes('Queue governed mission'))?.click()`);
  await eventually(`document.body.innerText.includes('COMPLETED') && document.body.innerText.includes('VERIFIED')`, 'verified mission state');
  await evaluate('location.reload()');
  await eventually(`document.body.innerText.toLowerCase().includes('mission_completed')`, 'API-derived mission timeline after workspace refresh');
  const body = await evaluate('document.body.innerText');
  const required = ['ui-operator-proof', 'COMPLETED', 'OBSERVED', 'VERIFIED', 'mission_queued', 'mission_executing', 'mission_completed'];
  const normalizedBody = body.toLowerCase();
  const absent = required.filter((value) => !normalizedBody.includes(value.toLowerCase()));
  if (absent.length) throw new Error(`Rendered command-center proof is missing: ${absent.join(', ')}`);
  console.log(JSON.stringify({ status: 'PASSED', url, assertions: required, source: 'rendered NEXUS UI controls and API-derived DOM state' }, null, 2));
  socket.close();
} finally {
  browser.kill('SIGTERM');
}
