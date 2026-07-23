#!/usr/bin/env node
/**
 * copy-docs.mjs — copies the feature docs from the repo-root docs/ tree into
 * frontend/public/docs/ so the Angular build ships them as static assets for
 * the in-app Help page (/help).
 *
 * Layout produced (one folder per UI language, mirroring docs/<lang>/):
 *   public/docs/<lang>/manifest.json   ordered [{slug, file, title}]
 *   public/docs/<lang>/*.md            verbatim copies of docs/<lang>/*.md
 *   public/docs/<lang>/screenshots/    per-language screenshots (docs/<lang>/screenshots/)
 *
 * Languages: it, en, fr, de, es — all five sets are fully translated (matching
 * the app UI). Screenshots are captured per language by
 * frontend/scripts/screenshots.mjs.
 *
 * Inside the Docker builds the repo-root docs/ is NOT in the build context:
 * the Makefile runs this script on the host first, so here we just detect the
 * already-copied output and exit 0. Link/image paths inside the markdown are
 * rewritten at render time by the Help component, not here.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const FRONTEND_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REPO_ROOT = path.resolve(FRONTEND_DIR, '..');
const DEST = path.join(FRONTEND_DIR, 'public', 'docs');

// UI language → source dir of its markdown + screenshots set.
const LANGUAGES = {
  it: path.join(REPO_ROOT, 'docs', 'it'),
  en: path.join(REPO_ROOT, 'docs', 'en'),
  fr: path.join(REPO_ROOT, 'docs', 'fr'),
  de: path.join(REPO_ROOT, 'docs', 'de'),
  es: path.join(REPO_ROOT, 'docs', 'es'),
};

// Display order per slug set. Italian uses translated slugs; the other four
// share the English slug set (en/fr/de/es all use the English filenames).
const ORDER_IT = [
  'README', 'autenticazione-e-profili', 'chat', 'provider-e-modelli', 'tool-calling',
  'mcp-e-agenti', 'guida-workflow', 'visual-workflows', 'knowledge-rag', 'confronto-modelli',
  'statistiche', 'telegram', 'memoria-e-personalizzazione', 'workspace-e-collaborazione',
  'interfaccia', 'internazionalizzazione', 'operazioni',
];
const ORDER_EN = [
  'README', 'authentication-and-profiles', 'chat', 'providers-and-models', 'tool-calling',
  'mcp-and-agents', 'workflow-guide', 'visual-workflows', 'knowledge-rag', 'model-comparison',
  'statistics', 'telegram', 'memory-and-personalization', 'workspaces-and-collaboration',
  'interface', 'internationalization', 'operations',
];
const ORDER_BY_LANG = { it: ORDER_IT, en: ORDER_EN, fr: ORDER_EN, de: ORDER_EN, es: ORDER_EN };

function extractTitle(markdown, fallback) {
  const m = markdown.match(/^#\s+(.+)$/m);
  if (!m) return fallback;
  // Strip inline markdown (links, emphasis, code) from the heading text.
  return m[1]
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[*_`]/g, '')
    .trim();
}

function copyLanguage(lang, srcDir) {
  const destDir = path.join(DEST, lang);
  fs.mkdirSync(destDir, { recursive: true });

  const order = ORDER_BY_LANG[lang] ?? ORDER_EN;
  const files = fs.readdirSync(srcDir).filter((f) => f.endsWith('.md')).sort();
  const rank = (name) => {
    const i = order.indexOf(path.basename(name, '.md'));
    return i === -1 ? order.length : i;
  };
  files.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));

  const docs = files.map((file) => {
    const md = fs.readFileSync(path.join(srcDir, file), 'utf8');
    fs.writeFileSync(path.join(destDir, file), md);
    const base = path.basename(file, '.md');
    const slug = base === 'README' ? 'index' : base;
    return { slug, file, title: extractTitle(md, base) };
  });

  // Per-language screenshots (shared assets removed; each language ships its own).
  const shots = path.join(srcDir, 'screenshots');
  if (fs.existsSync(shots)) {
    fs.cpSync(shots, path.join(destDir, 'screenshots'), { recursive: true });
  }

  fs.writeFileSync(
    path.join(destDir, 'manifest.json'),
    JSON.stringify({ language: lang, docs }, null, 2) + '\n'
  );
  return docs.length;
}

const anySourcePresent = Object.values(LANGUAGES).some((dir) => fs.existsSync(dir));
if (!anySourcePresent) {
  if (fs.existsSync(path.join(DEST, 'it', 'manifest.json'))) {
    console.log('[copy-docs] source docs/ not found but public/docs/ already populated — skipping');
    process.exit(0);
  }
  console.error('[copy-docs] ERROR: neither the source docs/ tree nor public/docs/ exists.');
  console.error('[copy-docs] Run this script from a full repo checkout (node frontend/scripts/copy-docs.mjs).');
  process.exit(1);
}

// Clean then copy so removed docs don't linger.
fs.rmSync(DEST, { recursive: true, force: true });

let total = 0;
for (const [lang, srcDir] of Object.entries(LANGUAGES)) {
  if (!fs.existsSync(srcDir)) {
    console.warn(`[copy-docs] WARN: missing source for "${lang}" (${srcDir}), skipping`);
    continue;
  }
  const n = copyLanguage(lang, srcDir);
  console.log(`[copy-docs] ${lang}: ${n} documents`);
  total += n;
}

console.log(`[copy-docs] done → ${path.relative(REPO_ROOT, DEST)} (${total} docs across ${Object.keys(LANGUAGES).length} languages)`);
