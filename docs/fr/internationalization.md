# Internationalisation (i18n)

**Ce que ça fait.** SpiceSibyl parle cinq langues sur les deux canaux — **anglais, français, allemand, italien et espagnol** — avec un sélecteur de langue à runtime dans la console web et une langue par chat dans le bot Telegram. Sur le web, changer de langue ne nécessite ni recompilation ni rechargement de la page.

> Versions : [English](../en/internationalization.md) · [Italiano](../it/internazionalizzazione.md)

## Console web

- **Sélecteur de langue.** Un bouton 🌐 dans la navbar (à côté des contrôles de thème/accent) ouvre un menu des cinq langues ; celle active est surlignée. Le changement redessine l'interface à l'instant.
- **Détection automatique.** À la première visite — quand l'utilisateur n'a pas encore choisi de langue — la langue du navigateur (`navigator.languages`) est comparée à celles prises en charge ; si aucune ne correspond, la valeur par défaut historique (**italien**) est utilisée, préservant le comportement d'origine pour les utilisateurs existants.
- **Persistance.** Le choix est stocké dans `localStorage` (immédiat, hors ligne) **et** sur le profil actif via `PATCH /api/v1/profiles/{id}` (`{ "locale": "fr" }`), de sorte qu'il suit l'utilisateur entre appareils. La langue enregistrée sur le profil est adoptée à la connexion/sélection *sauf* si le navigateur contient déjà un choix explicite local.
- **Couverture.** Navbar et menus, les tooltips langue/thème/accent, les indicateurs de chargement du chat, les pages (fournisseurs, statistiques, outils, workflows, MCP, espaces, etc.), la fenêtre de profil, les toasts et le tour d'onboarding sont localisés. La saisie vocale (Web Speech API) et le TTS suivent le tag BCP-47 de la langue active (ex. `fr-FR`) au lieu de l'ancien `it-IT` fixe.

### Architecture

Une couche i18n à runtime légère et sans dépendance (dans l'esprit minimaliste du projet — voir le catalogue Telegram et le client MCP sans SDK) :

| Composant | Fichier |
|-----------|---------|
| Métadonnées de langue (codes, libellés natifs, tags BCP-47) | [`core/i18n/locale.ts`](../../frontend/src/app/core/i18n/locale.ts) |
| Catalogues (une map plate `clé → chaîne` par langue) | [`core/i18n/translations/*.ts`](../../frontend/src/app/core/i18n/translations/) |
| `I18nService` (signal de langue active, détection, persistance, `translate()`, formateurs) | [`core/i18n/i18n.service.ts`](../../frontend/src/app/core/i18n/i18n.service.ts) |
| `TranslatePipe` (`\| t`) — impure, pour réagir aux changements de langue | [`core/i18n/translate.pipe.ts`](../../frontend/src/app/core/i18n/translate.pipe.ts) |
| Pipes de formatage localisé (`\| localeNumber`, `\| localeCost`, `\| localeDate`) | [`core/i18n/format.pipes.ts`](../../frontend/src/app/core/i18n/format.pipes.ts) |

Utilisation dans un template :

```html
{{ 'nav.chat' | t }}
{{ 'chat.providerSwitch' | t: { from: 'groq', to: 'ollama' } }}   <!-- les {placeholders} sont remplis depuis params -->
{{ message.estimated_cost | localeCost:6 }}
```

Ordre de résolution d'une clé : **catalogue de la langue active → catalogue par défaut (`it`) → la clé elle-même.** Ajouter une langue = ajouter un fichier catalogue, l'enregistrer dans `I18nService.CATALOGS` et ajouter une entrée à `SUPPORTED_LOCALES`.

### Formatage localisé

Nombres, coûts et dates sont rendus selon la langue active via l'API `Intl`, indexée sur le tag BCP-47 de la langue :

- `localeNumber` → `Intl.NumberFormat` (séparateurs de milliers et de décimales)
- `localeCost` → `Intl.NumberFormat` avec `style: 'currency', currency: 'USD'` (position du symbole selon la langue : `$1.23` vs `1,23 $`)
- `localeDate` → `Intl.DateTimeFormat`

## Bot Telegram

- **Langue par chat.** `/lang` affiche un clavier inline avec les cinq langues ; `/lang en|fr|de|es|it` la définit directement. Le choix persiste dans `telegram_prefs` et est pré-chargé au démarrage.
- **Catalogue.** Toute la sortie des commandes, les claviers inline, les actions rapides, les rappels et les messages d'erreur vivent dans [`backend/app/telegram/i18n.py`](../../backend/app/telegram/i18n.py) (`MESSAGES[locale][key]`), avec une chaîne de repli `locale → défaut (it) → clé`.
- **Confirmations localisées.** Les heures des rappels utilisent un ordre de date localisé (l'anglais utilise mois/jour, les autres langues jour/mois) dans le `TIMEZONE` du chat.

## Documentation et captures d'écran

Les documents et leurs captures sont par langue : `docs/<lang>/*.md` + `docs/<lang>/screenshots/*.png` pour chacune de `en`, `it`, `fr`, `de`, `es`. La **Aide in-app** (`/help`) charge le jeu de documents correspondant à la langue active de l'UI, avec repli sur l'anglais si un jeu manque ; les captures pointent vers `docs/<lang>/screenshots/`. Les captures sont générées avec Playwright par [`frontend/scripts/screenshots.mjs`](../../frontend/scripts/screenshots.mjs).

## Configuration

Aucune configuration n'est requise — les cinq langues sont incluses dans l'application. La langue par défaut historique est l'italien ; changez-la en modifiant `DEFAULT_LOCALE` dans `core/i18n/locale.ts` (web) et `i18n.py` (Telegram).
