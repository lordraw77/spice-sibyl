# Bot Telegram

**Ce que ça fait.** Un bot en polling qui expose la passerelle sur Telegram : historique par chat, réponses en streaming avec édition en direct du message, sélection de modèle, vision, génération d'images, transcription vocale, documents, mémoire personnelle, **base de connaissances (RAG)**, rappels et liaison au profil web.

**Configuration.** `TELEGRAM_BOT_TOKEN` dans `backend/.env` ; liste d'autorisation facultative avec `TELEGRAM_ALLOWED_USERS` (ids séparés par des virgules). Fuseau des rappels avec `TIMEZONE` (défaut `Europe/Rome`). Nécessite l'extra `python-telegram-bot[job-queue]` pour les rappels.

## Commandes

| Commande | Ce qu'elle fait |
|----------|-----------------|
| `/start` | message de bienvenue |
| `/new` | démarre une nouvelle conversation (utilisateurs liés : une nouvelle conversation persistée est créée au message suivant) |
| `/model` | sélection du modèle via un **clavier inline en deux étapes** (fournisseur → modèle, avec retour et ✅ sur le modèle actuel) |
| `/models` | liste les modèles disponibles |
| `/agent` · `/chat` | bascule entre mode agent (orchestrateur Multi-MCP) et chat normal |
| `/imagine <prompt>` | génère une image (`IMAGE_GENERATION_CHAIN`) et l'envoie en photo avec la légende fournisseur/modèle |
| `/history` | **utilisateurs liés :** conversations récentes sur les deux canaux, avec un clavier inline pour reprendre l'une d'elles ; **non liés :** les 20 derniers messages de la session courante |
| `/search <requête>` | recherche plein texte (FTS5) dans toutes les conversations sauvegardées : titres + extraits |
| `/link` · `/unlink` | génère le code pour associer/dissocier le profil web (voir [Authentification et profils](authentication-and-profiles.md)) |
| `/remind` | rappels : `/remind 15:50 Vérifier les sauvegardes` ou relatif `/remind +30m …`, `2h`, `1d` |
| `/reminders` · `/unremind <id>` | liste / annule les rappels en attente |
| `/memory on\|off\|list\|del <id>` | mémoire personnelle sur le profil associé (voir [Mémoire et personnalisation](memory-and-personalization.md)) |
| `/kb list\|del <id>` | gère la base de connaissances du profil associé ; pour ajouter un document, envoyez un fichier avec la **légende `/kb`** (voir ci-dessous) |
| `/rag on\|off` | active/désactive l'injection de la base de connaissances dans ce chat (par chat, **OFF par défaut**) |
| `/tool on\|off` | active/désactive la boucle d'outils pour ce chat (par chat, **OFF par défaut**) |
| `/tools` | liste les outils disponibles (groupés) et l'état actuel du basculement — lecture seule, ne modifie pas l'état |
| `/notify on\|off` | coupe/réactive les notifications Telegram déclenchées par des événements web (par chat, **ON par défaut**) |
| `/lang` · `/lang en\|it\|fr\|de\|es` | langue du bot par chat (clavier inline ou directe) ; persistée dans `telegram_prefs` |

## Gestion des médias

- **Photos** envoyées au bot → décrites automatiquement par le modèle actif via la vision.
- **Messages vocaux/audio** → transcrits avec Groq Whisper (`whisper-large-v3`) ; le bot affiche la transcription, puis diffuse la réponse au texte transcrit.
- **Documents** PDF / TXT / DOCX / MD → le texte est extrait (tronqué à 8 000 caractères) et utilisé comme contexte **ponctuel** pour le modèle, avec l'éventuelle légende. Avec la légende `/kb`, le document est au contraire **ingéré dans la base de connaissances** (voir ci-dessous).

## Historique de conversation partagé (Phase 23.a)

Pour un **profil web associé** (`/link`), Telegram n'est plus un chat en mémoire séparé : chaque échange est persisté comme une conversation de profil ordinaire, de sorte que l'historique est partagé entre les deux canaux.

- **Persistance** — chaque tour réussi (texte, voix, photo, document) est enregistré dans la *conversation active* du chat, créée à la volée au premier message avec un titre auto-généré. La conversation est marquée `channel='telegram'` et apparaît dans la **sidebar web avec un badge ✈️** ; inversement, les conversations démarrées sur le web peuvent être reprises depuis Telegram.
- **`/history`** — liste les conversations les plus récentes du profil (les deux canaux) sous forme de clavier inline ; touchez-en une pour la reprendre (l'active est marquée ✅). Le contexte complet est réhydraté pour que le modèle continue là où vous vous étiez arrêté — même après un redémarrage du bot.
- **`/new`** — détache la conversation active ; le message suivant en démarre une nouvelle. Changer de modèle (`/model`) ou de mode (`/agent` / `/chat`) fait de même.
- **Chats non liés** conservent la session en mémoire précédente (40 derniers messages), sans synchronisation inter-canaux — utilisez `/link` pour l'activer.

Implémentation : `telegram_prefs.active_conversation_id` (pré-chargé au démarrage), `conversation_repository.append_messages` et la nouvelle colonne `conversations.channel`.

## Base de connaissances (RAG)

Étend le RAG du profil web (voir [Base de connaissances](knowledge-rag.md)) au canal Telegram. Nécessite un **profil web associé** (`/link`) : toute commande `/kb`/`/rag` et tout envoi avec légende `/kb` invite à associer si aucun profil n'est connecté.

- **Ingestion** — envoyez un fichier **PDF / TXT / DOCX / MD** avec la légende `/kb` : il est ajouté à la base du profil associé en réutilisant le même pipeline que les téléversements web (`rag_service.ingest` : extraction → découpage → embedding), avec détection des doublons par hachage sha256.
- **Gestion** — `/kb list` affiche les documents avec une icône d'état (✅ prêt · ⏳ en attente · ⚠️ erreur), 🔗 pour les documents issus d'URL, et le nombre de chunks ; `/kb del <id>` supprime un document par préfixe d'id.
- **Récupération** — avec `/rag on`, chaque message fait récupérer par `_stream_reply` les chunks les plus pertinents (`rag_service.retrieve`, recherche hybride + rerank facultatif) et les intègre au dernier message utilisateur ; la réponse reçoit un pied 📚 sources (noms de fichiers dédupliqués). L'interrupteur est **par chat**, persisté dans `telegram_prefs.rag` et rechargé au démarrage.

## Outils et MCP (Phase 23.b)

Apporte la **boucle d'outils** du chat web à Telegram : avec `/tool on`, une completion ne se limite plus au streaming — le bot fusionne les outils intégrés, les **outils personnalisés** du profil associé et chaque **outil MCP** découvert (`mcp__<server>__<tool>`, voir [MCP](mcp.md)) dans la requête et exécute la boucle server-side partagée (`ChatService._stream_with_tools`), ainsi le comportement est identique sur les deux canaux.

- **Basculer** — `/tool on|off` bascule directement la boucle d'outils. **Par chat**, **OFF par défaut**, persisté dans `telegram_prefs.tools` et rechargé au démarrage (comme `/rag`). Les outils liés au profil (`kb_search`, `create_reminder`, outils personnalisés) se résolvent sur le profil associé.
- **Énumération** — `/tools` énumère les outils disponibles groupés par type (🧩 intégrés · 🔌 MCP · 🛠 personnalisés) ainsi que l'état actuel du basculement ; c'est une lecture seule qui ne modifie jamais l'état (utilisez `/tool` pour le changer).
- **Progression** — les appels d'outils apparaissent en direct dans la réponse (⚙ *nom de l'outil* pendant l'exécution, retourné en ✅ au résultat).
- **Découverte** — les outils MCP sont re-sondés quand vous exécutez `/tools` (ou quand le cache est froid) et stockés dans `mcp_service`, ainsi les messages ordinaires ne paient pas la latence du sondage.
- **Mode agent** — les modèles `agent/*` orchestrent leurs propres outils ; le basculement `/tool` ne s'y applique pas.

## Actions rapides

Boutons inline après chaque réponse : **Régénérer** (rejoue le dernier tour), **Traduire** (IT↔EN), **Résumer** (points clés), **Continuer**.

## Mode inline

`@nom_du_bot question` dans n'importe quel chat Telegram : une réponse directe sans streaming (max 300 tokens) sous forme d'`InlineQueryResultArticle`, avec un cache de 30 secondes.

## Rappels (inter-canaux, Phase 23.d)

Les rappels sont stockés dans une table `reminders` indépendante du canal et déclenchés par une boucle de sondage dans `reminder_service.py` (intervalle ~20 s) — ils fonctionnent que le bot Telegram soit connecté ou non, et **survivent aux redémarrages**. Les heures utilisent `TIMEZONE` par défaut, ou un fuseau horaire propre à chaque rappel réglable depuis l'interface web.

- **`/remind <quand> <texte>`** — accepte toute l'ancienne syntaxe, plus la récurrence et des formulations en langage naturel :
  - ponctuel : `/remind 15:50 Appeler Mario`, `/remind +30m Vérifier les sauvegardes`, `/remind 2h Réunion`, `/remind 2024-06-01 09:00 Voyage`
  - langage naturel (IT/EN) : `/remind tomorrow at 9 Dentiste`, `/remind domani alle 9 Dentiste`, `/remind tra due ore Rappeler`, `/remind in two hours Rappeler`, `/remind il 15 alle 14:30 Révision`, `/remind dopodomani Relance`, `/remind stasera Arroser les plantes`, ou un simple jour de la semaine comme `/remind monday Sync d'équipe`
  - récurrent : `/remind every day 08:00 Prendre les vitamines`, `/remind every monday Réunion hebdomadaire`, ou une expression cron pour utilisateurs avancés avec `/remind cron:0,8,*,*,1-5 Alarme jours ouvrés` (5 champs séparés par des virgules — `min,heure,jour-mois,mois,jour-semaine` — car Telegram sépare les arguments de commande sur les espaces)
- **`/remindai <quand> <prompt>`** — un **rappel intelligent** : au lieu d'un texte statique, au déclenchement il exécute le prompt dans une petite boucle d'outils bornée (max 4 étapes, avec `fetch_rss` / `get_weather` / `kb_search` / `search_conversations`) et livre ce que le modèle produit, p. ex. `/remindai every day 08:00 résume mes flux RSS`.
- **`/reminders`** · **`/unremind <id>`** — inchangés dans l'esprit, désormais adossés à la table unifiée ; `/reminders` affiche l'étiquette de récurrence (p. ex. `[daily]`, `[weekly:mon]`) à côté de chaque entrée.
- **Reporter / répéter / supprimer** — un rappel déclenché sur Telegram porte un clavier inline : 💤 le reporte de 10 minutes (replanifie `fire_at`, sans toucher à la récurrence), 🔁 relivre immédiatement le même contenu sans toucher à la planification, 🗑 supprime définitivement le rappel (annule aussi toute récurrence future).
- **Gestion depuis le web** — le panneau Rappels de l'interface web (route `/reminders`) permet de créer, modifier, mettre en pause/reprendre et supprimer des rappels, et de définir un fuseau horaire personnel, en s'appuyant sur `GET/POST/PATCH/DELETE /v1/reminders`, `POST /v1/reminders/{id}/snooze` et `POST /v1/reminders/{id}/repeat`. Un rappel créé depuis le web peut cibler le canal de livraison `telegram`, `web` ou `both`, avec les mêmes actions reporter/répéter sur les toasts des événements `reminderFired`.

## Notifications inter-canaux (Phase 23.c)

Pour les **profils web liés**, Telegram et l'interface web se notifient mutuellement les événements pertinents :

- **Web → Telegram** — la fin (ou l'échec) d'un workflow, la fin d'une génération d'image, ou une longue réponse terminée pendant que l'onglet du navigateur était masqué déclenchent un message dans le chat lié.
- **Telegram → Web** — un rappel déclenché ou un document ingéré via `/kb` apparaissent comme un toast/badge dans la sidebar web (livré en direct via un flux SSE, ou récupéré au prochain chargement de la page).
- **`/notify on|off`** — coupe/réactive le côté Telegram du pont pour ce chat (**par chat, ON par défaut**). Le côté web possède sa propre matrice d'opt-in par type d'événement dans le panneau **Notifications** de la sidebar (voir [Chat web](chat.md#notifications-inter-canaux-phase-23c)).

Implémentation : `notification_service.py` (`notify_telegram` / `notify_web`), la table `notification_events` et `telegram_prefs.notify`.
