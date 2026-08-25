/**
 * The single source of truth for UI strings (roadmap v2 § 3, P2 — "i18n a
 * sorgente unica").
 *
 * There used to be five parallel catalogs, one per locale, each a flat map of
 * the same ~1.170 keys. Nothing tied them together: adding a string meant
 * editing five files in five places, and a forgotten one only surfaced at
 * runtime as the Italian fallback (or the raw key).
 *
 * Now every key is declared once with all five translations side by side, and
 * the type is ``Record<Locale, string>`` — leaving a locale out is a
 * compile error, not a silent fallback. The per-locale maps the service needs
 * are projected from this at module load.
 */

import { Locale, LOCALE_CODES, TranslationMap } from './locale';

import { navMessages } from './messages/nav';
import { commonMessages } from './messages/common';
import { authMessages } from './messages/auth';
import { chatMessages } from './messages/chat';
import { workflow_editorMessages } from './messages/workflow-editor';
import { workflowsMessages } from './messages/workflows';
import { knowledgeMessages } from './messages/knowledge';
import { toolsMessages } from './messages/tools';
import { opsMessages } from './messages/ops';
import { workspacesMessages } from './messages/workspaces';
import { remindersMessages } from './messages/reminders';
import { settingsMessages } from './messages/settings';

/** One key's text in every supported locale. */
export type LocalizedMessage = Record<Locale, string>;

/** Dotted key → its translations. */
export type MessageCatalog = Record<string, LocalizedMessage>;

/** Every message in the app, grouped by domain in ./messages. */
export const MESSAGES: MessageCatalog = {
  ...navMessages,  // 41 keys
  ...commonMessages,  // 34 keys
  ...authMessages,  // 25 keys
  ...chatMessages,  // 152 keys
  ...workflow_editorMessages,  // 290 keys
  ...workflowsMessages,  // 199 keys
  ...knowledgeMessages,  // 94 keys
  ...toolsMessages,  // 69 keys
  ...opsMessages,  // 135 keys
  ...workspacesMessages,  // 35 keys
  ...remindersMessages,  // 48 keys
  ...settingsMessages,  // 48 keys
};

/**
 * Project the catalog into the flat per-locale maps the service resolves
 * against. Done once at module load: the shape the runtime wants is a lookup
 * by key, while the shape maintainers want is all locales on one line.
 */
export function buildCatalogs(messages: MessageCatalog = MESSAGES): Record<Locale, TranslationMap> {
  const catalogs = Object.fromEntries(
    LOCALE_CODES.map((code) => [code, {} as TranslationMap]),
  ) as Record<Locale, TranslationMap>;

  for (const [key, translations] of Object.entries(messages)) {
    for (const code of LOCALE_CODES) {
      catalogs[code][key] = translations[code];
    }
  }
  return catalogs;
}

export const CATALOGS: Record<Locale, TranslationMap> = buildCatalogs();
