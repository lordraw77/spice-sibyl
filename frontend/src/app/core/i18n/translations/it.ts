import { TranslationMap } from '../locale';

/**
 * Italian catalog — the canonical key set (source of truth).
 * Every other locale mirrors these keys; missing keys fall back here, then to
 * the key string itself (see I18nService.translate).
 */
export const it: TranslationMap = {
  // ── Navbar / navigation ────────────────────────────────────────────────
  'nav.chat': 'Chat',
  'nav.group.models': 'Modelli',
  'nav.providers': 'Providers',
  'nav.discovery': 'Discovery',
  'nav.compare': 'Confronta',
  'nav.stats': 'Statistiche',
  'nav.group.tools': 'Strumenti',
  'nav.tools': 'Strumenti',
  'nav.workflows': 'Workflow',
  'nav.mcp': 'MCP',
  'nav.workspaces': 'Workspace',
  'nav.group.resources': 'Risorse',
  'nav.templates': 'Template',
  'nav.tags': 'Tag',
  'nav.knowledge': 'Knowledge',
  'nav.memory': 'Memoria',
  'nav.group.info': 'Info',
  'nav.help': 'Guida',
  'nav.info': 'Info',
  'nav.ops': 'Ops',
  'navbar.menu': 'Apri menu di navigazione',
  'navbar.accent': 'Colore accento',
  'navbar.accentCustom': 'Colore personalizzato',
  'navbar.theme': 'Tema',
  'navbar.language': 'Lingua',
  'navbar.logout': 'Esci',

  // ── Common actions ─────────────────────────────────────────────────────
  'common.save': 'Salva',
  'common.cancel': 'Annulla',
  'common.delete': 'Elimina',
  'common.remove': 'Rimuovi',
  'common.close': 'Chiudi',
  'common.edit': 'Modifica',
  'common.add': 'Aggiungi',
  'common.create': 'Crea',
  'common.apply': 'Applica',
  'common.search': 'Cerca',
  'common.loading': 'Caricamento…',
  'common.confirm': 'Conferma',
  'common.yes': 'Sì',
  'common.no': 'No',
  'common.enable': 'Attiva',
  'common.disable': 'Disattiva',
  'common.on': 'ON',
  'common.off': 'OFF',
  'common.back': 'Indietro',
  'common.copy': 'Copia',
  'common.copied': 'Copiato',
  'common.reset': 'Reset',
  'common.retry': 'Riprova',
  'common.send': 'Invia',
  'common.stop': 'Ferma',

  // ── Language switcher ──────────────────────────────────────────────────
  'lang.choose': 'Scegli la lingua',

  // ── Chat empty / loading states ────────────────────────────────────────
  'chat.empty.title': 'Inizia una conversazione',
  'chat.empty.subtitle': 'Scrivi un messaggio qui sotto per iniziare.',
  'chat.placeholder': 'Scrivi un messaggio…',
  'chat.loading.model': 'In attesa del modello…',
  'chat.loading.tools': 'Esecuzione tool…',
  'chat.loading.streaming': 'Generazione in corso…',
  'chat.newChat': 'Nuova chat',

  // ── Onboarding tour ────────────────────────────────────────────────────
  'onboarding.skip': 'Salta',
  'onboarding.next': 'Avanti',
  'onboarding.back': 'Indietro',
  'onboarding.done': 'Fine',
  'onboarding.replay': 'Rivedi il tour',
  'onboarding.step.model.title': 'Scegli un modello',
  'onboarding.step.model.body': 'Seleziona il provider e il modello da usare per la conversazione.',
  'onboarding.step.tools.title': 'Strumenti',
  'onboarding.step.tools.body': 'Attiva i tool per dare al modello accesso a calcolatrice, ricerca web e altro.',
  'onboarding.step.system.title': 'Prompt di sistema',
  'onboarding.step.system.body': 'Imposta istruzioni persistenti che guidano ogni risposta.',
  'onboarding.step.commands.title': 'Comandi',
  'onboarding.step.commands.body': 'Usa i comandi slash come /imagine per funzioni rapide.',
  // ── Login / auth (it) ──
  'auth.subtitle': 'Accedi per continuare.',
  'auth.email': 'Email',
  'auth.emailPlaceholder': 'nome@esempio.com',
  'auth.password': 'Password',
  'auth.signIn': 'Accedi',
  'auth.signingIn': 'Accesso…',
  'auth.invalidCredentials': 'Email o password non validi.',
  'auth.failed': 'Accesso fallito.',
};
