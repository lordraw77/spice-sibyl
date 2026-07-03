#!/usr/bin/env node
/**
 * copy-docs.mjs — copies the feature docs from the repo-root docs/ tree into
 * frontend/public/docs/ so the Angular build ships them as static assets for
 * the in-app Help page (/help).
 *
 * Layout produced (multi-language-ready; only "it" exists today):
 *   public/docs/it/manifest.json   ordered [{slug, file, title}]
 *   public/docs/it/*.md            verbatim copies of docs/funzionalita/
 *   public/docs/screenshots/*.png  shared across languages
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

// language → source dir of its markdown set (add 'en': docs/features to publish English)
const LANGUAGES = {
  it: path.join(REPO_ROOT, 'docs', 'funzionalita'),
};
const SRC_SCREENSHOTS = path.join(REPO_ROOT, 'docs', 'screenshots');

// Display order (mirrors the README index); unknown files are appended alphabetically.
const ORDER = [
  'README',
  'autenticazione-e-profili',
  'chat',
  'provider-e-modelli',
  'tool-calling',
  'mcp-e-agenti',
  'knowledge-rag',
  'confronto-modelli',
  'statistiche',
  'telegram',
  'memoria-e-personalizzazione',
  'interfaccia',
  'operazioni',
];

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

  const files = fs.readdirSync(srcDir).filter((f) => f.endsWith('.md')).sort();
  const rank = (name) => {
    const i = ORDER.indexOf(path.basename(name, '.md'));
    return i === -1 ? ORDER.length : i;
  };
  files.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));

  const docs = files.map((file) => {
    const md = fs.readFileSync(path.join(srcDir, file), 'utf8');
    fs.writeFileSync(path.join(destDir, file), md);
    const base = path.basename(file, '.md');
    const slug = base === 'README' ? 'index' : base;
    return { slug, file, title: extractTitle(md, base) };
  });

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

if (fs.existsSync(SRC_SCREENSHOTS)) {
  fs.cpSync(SRC_SCREENSHOTS, path.join(DEST, 'screenshots'), { recursive: true });
  const n = fs.readdirSync(path.join(DEST, 'screenshots')).length;
  console.log(`[copy-docs] screenshots: ${n} files`);
} else {
  console.warn('[copy-docs] WARN: docs/screenshots not found, images will be missing');
}

console.log(`[copy-docs] done → ${path.relative(REPO_ROOT, DEST)}`);
