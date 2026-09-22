#!/usr/bin/env node
'use strict';

// Internal subprocess adapter (not a public CLI).
// stdin:  one JSON object {url, timeout_ms, selectors}
// stdout: exactly one JSON object
//   {ok, html, axe:{violations,incomplete,passes,inapplicable,testEngine}, run:{...}, errors:[...]}
// The whole run is hard-bounded by timeout_ms (default 30000).

const DEFAULT_TIMEOUT_MS = 30000;
const MIN_TIMEOUT_MS = 250;
const MAX_TIMEOUT_MS = 120000;
// HTTP statuses that justify the single transient retry (plus navigation timeout).
const TRANSIENT_HTTP = new Set([429, 502, 503, 504]);
// Reserve for post-navigation work (readiness, axe, extraction, teardown).
const POST_NAV_RESERVE_MS = 7000;
const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'];

const state = {
  emitted: false,
  browser: null,
  errors: [],
  run: {
    status: 'not-performed',
    started_at: new Date().toISOString(),
    finished_at: null,
    original_url: null,
    final_url: null,
    rendered: false,
    engines: {},
    pages: [],
    states: [],
    untested: ['עמודים אחרים', 'מצבים לאחר התחברות', 'ניווט מקלדת וקורא מסך', 'תפריטים וחלונות שלא נפתחו'],
    observed_selectors: {},
    attempts: [],
  },
  html: '',
  axe: null,
};

let deadline = Date.now() + DEFAULT_TIMEOUT_MS;
let totalBudgetMs = DEFAULT_TIMEOUT_MS;

function remaining() {
  return deadline - Date.now();
}

// Reserves scale down with small budgets so early phases never starve later ones.
function scaledReserve(fraction, capMs) {
  return Math.min(capMs, Math.floor(totalBudgetMs * fraction));
}

function postNavReserve() {
  return scaledReserve(0.35, POST_NAV_RESERVE_MS);
}

// Time available for one phase while keeping reserveMs for later phases.
function phaseBudget(reserveMs, capMs) {
  const budget = Math.min(remaining() - reserveMs, capMs || Infinity);
  return Math.max(budget, 250);
}

function emptyAxe() {
  return { violations: [], incomplete: [], passes: [], inapplicable: [], testEngine: {} };
}

function emit(ok) {
  if (state.emitted) return;
  state.emitted = true;
  state.run.finished_at = new Date().toISOString();
  const payload = {
    ok,
    html: state.html,
    axe: state.axe || emptyAxe(),
    run: state.run,
    errors: state.errors,
  };
  process.stdout.write(JSON.stringify(payload) + '\n');
}

async function shutdown(ok) {
  emit(ok);
  if (state.browser) {
    // Best-effort close; Playwright also kills owned browsers on process exit.
    try {
      await Promise.race([
        state.browser.close(),
        new Promise((resolve) => setTimeout(resolve, 1500)),
      ]);
    } catch (err) {
      /* ignore teardown failures */
    }
  }
  process.exit(0);
}

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => { data += chunk; });
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', reject);
  });
}

function isTransient(outcome) {
  return outcome === 'transient-http' || outcome === 'network-timeout';
}

async function navigateOnce(page, url, attemptNo) {
  const attempt = {
    n: attemptNo,
    url,
    started_at: new Date().toISOString(),
    finished_at: null,
    outcome: 'error',
    http_status: null,
    error: null,
  };
  state.run.attempts.push(attempt);
  try {
    const response = await page.goto(url, {
      waitUntil: 'domcontentloaded',
      timeout: phaseBudget(postNavReserve()),
    });
    attempt.finished_at = new Date().toISOString();
    if (!response) {
      attempt.error = 'no HTTP response (non-HTTP navigation?)';
      return attempt;
    }
    attempt.http_status = response.status();
    if (TRANSIENT_HTTP.has(response.status())) {
      attempt.outcome = 'transient-http';
      attempt.error = 'HTTP ' + response.status();
      return attempt;
    }
    attempt.outcome = 'ok';
    attempt.response = response;
    return attempt;
  } catch (err) {
    attempt.finished_at = new Date().toISOString();
    const message = String(err && err.message ? err.message : err);
    attempt.error = message.split('\n')[0];
    if (err && err.name === 'TimeoutError') {
      attempt.outcome = 'network-timeout';
    }
    return attempt;
  }
}

async function main() {
  let input;
  try {
    const raw = await readStdin();
    input = JSON.parse(raw);
  } catch (err) {
    state.errors.push('invalid stdin JSON: ' + String(err && err.message ? err.message : err));
    return shutdown(false);
  }

  const url = typeof input.url === 'string' ? input.url.trim() : '';
  const timeoutMs = Math.min(
    Math.max(Number(input.timeout_ms) || DEFAULT_TIMEOUT_MS, MIN_TIMEOUT_MS),
    MAX_TIMEOUT_MS
  );
  const selectors = Array.isArray(input.selectors)
    ? input.selectors.filter((s) => typeof s === 'string' && s.trim())
    : [];
  deadline = Date.now() + timeoutMs;
  totalBudgetMs = timeoutMs;
  // Absolute last-resort stop at the total budget: emit what we have and exit.
  const killer = setTimeout(() => {
    state.errors.push('total budget of ' + timeoutMs + 'ms exhausted');
    if (state.run.status === 'completed') state.run.status = 'partial';
    shutdown(state.run.rendered);
  }, timeoutMs);
  killer.unref();

  state.run.original_url = url;
  let parsed;
  try {
    parsed = new URL(url);
  } catch (err) {
    parsed = null;
  }
  if (!parsed || parsed.username || parsed.password || (parsed.protocol !== 'http:' && parsed.protocol !== 'https:')) {
    state.errors.push('invalid or non-HTTP(S) url: ' + url);
    return shutdown(false);
  }

  let playwright;
  let axeSourcePath;
  try {
    playwright = require('playwright');
    axeSourcePath = require.resolve('axe-core/axe.min.js');
  } catch (err) {
    state.errors.push('runtime dependencies missing (playwright/axe-core): '
      + String(err && err.message ? err.message : err).split('\n')[0]);
    return shutdown(false);
  }

  try {
    // PLAYWRIGHT_BROWSERS_PATH from the environment is honored automatically.
    state.browser = await playwright.chromium.launch({
      headless: true,
      timeout: phaseBudget(scaledReserve(0.5, POST_NAV_RESERVE_MS + 5000), 15000),
    });
  } catch (err) {
    state.errors.push('browser launch failed: '
      + String(err && err.message ? err.message : err).split('\n')[0]);
    return shutdown(false);
  }

  let page;
  try {
    const context = await state.browser.newContext({
      // bypassCSP lets us inject the local axe-core bundle on CSP-strict sites.
      bypassCSP: true,
      locale: 'he-IL',
      viewport: { width: 1280, height: 800 },
    });
    page = await context.newPage();
  } catch (err) {
    state.errors.push('browser context failed: '
      + String(err && err.message ? err.message : err).split('\n')[0]);
    return shutdown(false);
  }

  // Navigation: at most one retry, and only for transient failures.
  let attempt = await navigateOnce(page, url, 1);
  if (attempt.outcome !== 'ok' && isTransient(attempt.outcome) && remaining() > postNavReserve() + 2000) {
    // Recovered failures remain in the attempt history, not operational errors.
    attempt = await navigateOnce(page, url, 2);
  }
  const response = attempt.response;
  delete attempt.response;
  state.run.attempts.forEach((a) => { delete a.response; });
  if (attempt.outcome !== 'ok') {
    state.errors.push('navigation failed: ' + (attempt.error || attempt.outcome));
    return shutdown(false);
  }

  // Validate the final HTTP response before scanning.
  const status = response.status();
  const contentType = String(response.headers()['content-type'] || '').toLowerCase();
  state.run.final_url = page.url();
  state.run.pages = [page.url()];
  state.run.http_status = status;
  state.run.content_type = contentType;
  if (status === 401 || status === 403 || status === 407 || status === 451) {
    state.errors.push('page blocked (HTTP ' + status + '); scan not performed');
    return shutdown(false);
  }
  if (status >= 400) {
    state.errors.push('HTTP error ' + status + '; scan not performed');
    return shutdown(false);
  }
  if (!['text/html', 'application/xhtml+xml'].includes(contentType.split(';')[0].trim())) {
    state.errors.push('non-HTML content-type "' + contentType + '"; scan not performed');
    return shutdown(false);
  }

  // DOM readiness: body visible plus bounded content/readyState wait (never networkidle alone).
  try {
    await page.waitForSelector('body', { state: 'visible', timeout: phaseBudget(scaledReserve(0.3, 6000), 8000) });
  } catch (err) {
    state.errors.push('body never became visible: '
      + String(err && err.message ? err.message : err).split('\n')[0]);
    return shutdown(false);
  }
  try {
    await page.waitForFunction(
      () => document.readyState !== 'loading'
        && document.body
        && (document.body.innerText.trim().length > 0
          || Array.from(document.body.querySelectorAll('img,svg,canvas,video,iframe,input:not([type="hidden"]),button,a[href],textarea,select'))
            .some((element) => element.getClientRects().length > 0)),
      null,
      { timeout: phaseBudget(scaledReserve(0.25, 5000), 6000) }
    );
  } catch (err) {
    state.errors.push('bounded content-readiness wait expired; scan not performed');
    return shutdown(false);
  }
  // Short settle for late-running scripts, only if the budget allows it.
  if (remaining() > postNavReserve()) {
    await page.waitForTimeout(Math.min(500, remaining() - postNavReserve()));
  }

  // Early rejection of pages that cannot be meaningfully audited.
  let probe;
  try {
    probe = await page.evaluate(() => {
      const body = document.body;
      const text = body ? body.innerText.trim() : '';
      return {
        textLength: text.length,
        hasVisual: !!(body && body.querySelector('img,svg,canvas,video,iframe')),
        hasPassword: !!document.querySelector('input[type="password"]'),
        elementCount: body ? body.getElementsByTagName('*').length : 0,
        title: document.title || '',
      };
    });
  } catch (err) {
    state.errors.push('page probe failed: '
      + String(err && err.message ? err.message : err).split('\n')[0]);
    return shutdown(false);
  }
  state.run.page_title = probe.title;
  if (probe.textLength === 0 && !probe.hasVisual && !await page.locator('input:not([type="hidden"]),button,a[href],textarea,select').count()) {
    state.errors.push('page is effectively empty; scan not performed');
    return shutdown(false);
  }
  if (probe.hasPassword && probe.textLength < 400) {
    state.errors.push('page appears to be a login wall; scan not performed');
    return shutdown(false);
  }

  state.run.rendered = true;
  state.run.states = ['initial-render'];

  // Selector observation for baseline comparison; invalid selectors record false.
  for (const sel of selectors) {
    try {
      state.run.observed_selectors[sel] = await page.evaluate(
        (s) => document.querySelector(s) !== null,
        sel
      );
    } catch (err) {
      state.run.observed_selectors[sel] = false;
      state.errors.push('selector check failed (recorded as absent): ' + sel);
    }
  }

  // Rendered HTML for the root's static Hebrew checks.
  try {
    state.html = await page.content();
  } catch (err) {
    state.errors.push('failed to capture rendered HTML: '
      + String(err && err.message ? err.message : err).split('\n')[0]);
    return shutdown(false);
  }
  state.run.status = 'partial';

  // Inject the locally installed axe-core bundle (never a CDN) and run it.
  try {
    await page.addScriptTag({ path: axeSourcePath });
    const axeReady = await page.evaluate(() => typeof window.axe !== 'undefined');
    if (!axeReady) throw new Error('axe global missing after injection');
    const axeTimeout = phaseBudget(2000);
    const results = await Promise.race([
      page.evaluate((tags) => window.axe.run(document, {
        runOnly: { type: 'tag', values: tags },
      }), AXE_TAGS),
      new Promise((_, reject) => setTimeout(
        () => reject(new Error('axe.run exceeded remaining budget')), axeTimeout
      )),
    ]);
    state.axe = {
      violations: results.violations || [],
      incomplete: results.incomplete || [],
      passes: results.passes || [],
      inapplicable: results.inapplicable || [],
      testEngine: results.testEngine || {},
    };
    state.run.engines = {
      axe: (results.testEngine && results.testEngine.version) || require('axe-core/package.json').version,
      playwright: require('playwright/package.json').version,
    };
    state.run.status = 'completed';
  } catch (err) {
    state.run.untested.push('axe-scan');
    state.errors.push('axe scan failed: '
      + String(err && err.message ? err.message : err).split('\n')[0]);
    // Rendered HTML is still usable by the root, so this stays a partial success.
    return shutdown(true);
  }

  return shutdown(true);
}

process.on('uncaughtException', (err) => {
  state.errors.push('uncaught exception: '
    + String(err && err.message ? err.message : err).split('\n')[0]);
  if (state.run.status === 'completed') state.run.status = 'partial';
  shutdown(state.run.rendered);
});
process.on('unhandledRejection', (err) => {
  state.errors.push('unhandled rejection: '
    + String(err && err.message ? err.message : err).split('\n')[0]);
  if (state.run.status === 'completed') state.run.status = 'partial';
  shutdown(state.run.rendered);
});

main();
