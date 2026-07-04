#!/usr/bin/env node
/**
 * i18n catalog smoke test (Phase 22.e).
 *
 * Verifies every locale catalog under core/i18n/translations/ declares exactly
 * the same set of keys as the canonical Italian catalog (`it.ts`) — no missing,
 * no stray keys — and that placeholders (`{name}`) are consistent across
 * locales for a given key. Run standalone (no test runner needed):
 *
 *   node frontend/scripts/check-i18n.mjs
 *
 * Exit code 0 on success, 1 on any mismatch. Wire into CI alongside the build.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DIR = join(__dirname, '..', 'src', 'app', 'core', 'i18n', 'translations');
const LOCALES = ['it', 'en', 'fr', 'de', 'es'];
const REFERENCE = 'it';

/** Extract the `'key': 'value'` pairs from a catalog .ts file. */
function parseCatalog(locale) {
  const src = readFileSync(join(DIR, `${locale}.ts`), 'utf8');
  const map = {};
  // Match  'some.key': '...'  or  "some.key": "..."  (single-line entries).
  const re = /(['"])([\w.]+)\1\s*:\s*(['"])((?:\\.|(?!\3).)*)\3/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    map[m[2]] = m[4];
  }
  return map;
}

function placeholders(value) {
  return [...value.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
}

const catalogs = Object.fromEntries(LOCALES.map((l) => [l, parseCatalog(l)]));
const refKeys = Object.keys(catalogs[REFERENCE]).sort();

if (refKeys.length === 0) {
  console.error(`✖ reference catalog '${REFERENCE}' parsed 0 keys — parser or file issue`);
  process.exit(1);
}

let failures = 0;
for (const locale of LOCALES) {
  const keys = Object.keys(catalogs[locale]).sort();
  const missing = refKeys.filter((k) => !keys.includes(k));
  const extra = keys.filter((k) => !refKeys.includes(k));
  const badPlaceholders = refKeys.filter(
    (k) =>
      catalogs[locale][k] !== undefined &&
      placeholders(catalogs[REFERENCE][k]).join(',') !==
        placeholders(catalogs[locale][k]).join(','),
  );

  if (missing.length || extra.length || badPlaceholders.length) {
    failures++;
    console.error(`✖ ${locale}: ${keys.length} keys`);
    if (missing.length) console.error(`   missing: ${missing.join(', ')}`);
    if (extra.length) console.error(`   extra:   ${extra.join(', ')}`);
    if (badPlaceholders.length)
      console.error(`   placeholder mismatch: ${badPlaceholders.join(', ')}`);
  } else {
    console.log(`✓ ${locale}: ${keys.length} keys, placeholders consistent`);
  }
}

if (failures) {
  console.error(`\ni18n check FAILED for ${failures} locale(s).`);
  process.exit(1);
}
console.log(`\ni18n check passed: ${LOCALES.length} locales × ${refKeys.length} keys.`);
