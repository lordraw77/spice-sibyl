#!/usr/bin/env node
/**
 * screenshots.mjs — capture the running web console in every supported UI
 * language and write per-language image sets used by the docs / in-app Help.
 *
 * Output:  docs/<lang>/screenshots/<name>.png   for lang in en/it/fr/de/es
 *
 * Requires the app to be running (default http://localhost:8888) and Playwright
 * with a Chromium build available. Usage:
 *
 *   BASE_URL=http://localhost:8888 \
 *   ADMIN_EMAIL=... ADMIN_PASSWORD=... \
 *   node frontend/scripts/screenshots.mjs [lang ...]
 *
 * Pass one or more locale codes to limit the run (default: all five).
 * Each capture is best-effort: a failure is logged and the run continues so a
 * single flaky page never aborts the whole set.
 */
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Resolve Playwright from local node_modules, else the global install. The
// global package is CommonJS, so named ESM interop doesn't expose `chromium`
// directly — fall back to the default export.
let chromium;
try {
  ({ chromium } = await import('playwright'));
} catch {
  const m = await import(
    pathToFileURL('/usr/lib/node_modules/playwright/index.js').href
  );
  chromium = m.chromium ?? m.default?.chromium ?? (m.default?.default ?? m.default)?.chromium;
}
if (!chromium) throw new Error('Could not resolve Playwright chromium');

const FRONTEND_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REPO_ROOT = path.resolve(FRONTEND_DIR, '..');

const BASE_URL = process.env.BASE_URL || 'http://localhost:8888';
const EMAIL = process.env.ADMIN_EMAIL || 'alessandro.pioli@gmail.com';
const PASSWORD = process.env.ADMIN_PASSWORD || '280977';
const ALL_LANGS = ['en', 'it', 'fr', 'de', 'es'];
const LANGS = process.argv.slice(2).filter((a) => ALL_LANGS.includes(a));
const LOCALES = LANGS.length ? LANGS : ALL_LANGS;

const VIEWPORT = { width: 1440, height: 900 };

// Simple route captures (filename → app route). Admin-only pages included
// because we log in as the bootstrap admin.
const ROUTES = {
  providers: '/providers',
  discovery: '/discovery',
  compare: '/compare',
  stats: '/stats',
  tools: '/tools',
  workflows: '/workflows',
  mcp: '/mcp',
  ops: '/ops',
  workspace: '/workspaces',
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function setLocale(page, locale) {
  await page.evaluate((l) => localStorage.setItem('spicesibyl_locale', l), locale);
}

async function shoot(page, lang, name) {
  const dir = path.join(REPO_ROOT, 'docs', lang, 'screenshots');
  mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${name}.png`);
  await page.screenshot({ path: file });
  console.log(`  ✓ ${lang}/${name}.png`);
}

async function safe(label, fn) {
  try {
    await fn();
  } catch (err) {
    console.warn(`  ⚠ ${label}: ${err.message}`);
  }
}

async function captureLocale(browser, lang) {
  console.log(`[${lang}] ---------------------------------------------`);
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 1,
    locale: lang,
  });
  const page = await context.newPage();

  // ── Logged-out login page (locale set before app bootstrap) ──────────────
  await safe('login', async () => {
    await page.goto(`${BASE_URL}/login`, { waitUntil: 'load' });
    await setLocale(page, lang);
    await page.reload({ waitUntil: 'load' });
    await sleep(400);
    await shoot(page, lang, 'login');
  });

  // ── Log in ───────────────────────────────────────────────────────────────
  await page.fill('input[type=email]', EMAIL);
  await page.fill('input[type=password]', PASSWORD);
  await Promise.all([
    page.waitForURL(/\/(chat|$)/, { timeout: 15000 }).catch(() => {}),
    page.click('button[type=submit]'),
  ]);
  await sleep(1200);

  // ── Profile selection modal (fresh context → no stored profile) ──────────
  await safe('profilo-selezione', async () => {
    const modal = page.locator('.modal, .profile-modal, [class*="profile"]').first();
    if (await modal.isVisible().catch(() => false)) {
      await shoot(page, lang, 'profilo-selezione');
    }
  });

  // Pick / create a profile so the chat becomes usable.
  await safe('select-profile', async () => {
    const pick = page.locator('button, .profile-item, li').filter({ hasText: /.+/ });
    // Try an existing profile entry first; otherwise create one.
    const existing = page.locator('[class*="profile-list"] button, .profile-item').first();
    if (await existing.isVisible().catch(() => false)) {
      await existing.click();
    } else {
      const input = page.locator('input[type=text]').first();
      if (await input.isVisible().catch(() => false)) {
        await input.fill('Demo');
        await page.locator('button[type=submit], button:has-text("Crea"), button:has-text("Create")').first().click().catch(() => {});
      }
    }
    await sleep(1000);
  });

  // ── Onboarding tour (first-run; only if it shows) ────────────────────────
  await safe('onboarding', async () => {
    await page.goto(`${BASE_URL}/chat`, { waitUntil: 'load' });
    await sleep(800);
    const tour = page.locator('.tour-card, .tour-backdrop').first();
    if (await tour.isVisible().catch(() => false)) {
      await shoot(page, lang, 'onboarding');
      // dismiss so it doesn't cover later chat shots
      await page.locator('.tour-skip, button:has-text("Skip"), button:has-text("Salta")').first().click().catch(() => {});
      await sleep(400);
    }
  });

  // ── Chat page (empty) + a conversation (best effort) ─────────────────────
  await safe('chat', async () => {
    await page.goto(`${BASE_URL}/chat`, { waitUntil: 'load' });
    await sleep(700);
    await shoot(page, lang, 'chat');
  });

  await safe('chat-conversazione', async () => {
    // Open the conversation picker and click the first one, if any exist.
    await page.keyboard.press('Control+k').catch(() => {});
    await sleep(600);
    const conv = page.locator('[class*="conversation"] , .conv-item, li').filter({ hasText: /.+/ }).first();
    if (await conv.isVisible().catch(() => false)) {
      await conv.click().catch(() => {});
      await sleep(900);
    } else {
      await page.keyboard.press('Escape').catch(() => {});
    }
    await shoot(page, lang, 'chat-conversazione');
  });

  // ── Straight route captures ──────────────────────────────────────────────
  for (const [name, route] of Object.entries(ROUTES)) {
    await safe(name, async () => {
      await page.goto(`${BASE_URL}${route}`, { waitUntil: 'load' });
      await sleep(900);
      await shoot(page, lang, name);
    });
  }

  // ── Workspace comments (best effort; fallback = workspace page) ───────────
  await safe('workspace-commenti', async () => {
    await page.goto(`${BASE_URL}/workspaces`, { waitUntil: 'load' });
    await sleep(900);
    // Try to reveal a comment thread if the UI exposes one.
    const commentBtn = page.locator('button:has-text("Comment"), button:has-text("Commenti"), [class*="comment"]').first();
    if (await commentBtn.isVisible().catch(() => false)) {
      await commentBtn.click().catch(() => {});
      await sleep(700);
    }
    await shoot(page, lang, 'workspace-commenti');
  });

  await context.close();
}

async function main() {
  console.log(`Capturing ${LOCALES.join(', ')} from ${BASE_URL}`);
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  for (const lang of LOCALES) {
    await captureLocale(browser, lang);
  }
  await browser.close();
  console.log('done.');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
