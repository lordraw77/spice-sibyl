# Bot Telegram

**Ce que ça fait.** Un bot en polling qui expose la passerelle sur Telegram : historique par chat, réponses en streaming avec édition en direct du message, sélection de modèle, vision, génération d'images, transcription vocale, documents, mémoire personnelle, **base de connaissances (RAG)**, rappels et liaison au profil web.

**Configuration.** `TELEGRAM_BOT_TOKEN` dans `backend/.env` ; liste d'autorisation facultative avec `TELEGRAM_ALLOWED_USERS` (ids séparés par des virgules). Fuseau des rappels avec `TIMEZONE` (défaut `Europe/Rome`). Nécessite l'extra `python-telegram-bot[job-queue]` pour les rappels.

## Commandes

| Commande | Ce qu'elle fait |
|----------|-----------------|
| `/start` | message de bienvenue |
| `/new` | nouvelle conversation (réinitialise le contexte du chat) |
| `/model` | sélection du modèle via un **clavier inline en deux étapes** (fournisseur → modèle, avec retour et ✅ sur le modèle actuel) |
| `/models` | liste les modèles disponibles |
| `/agent` · `/chat` | bascule entre mode agent (orchestrateur Multi-MCP) et chat normal |
| `/imagine <prompt>` | génère une image (`IMAGE_GENERATION_CHAIN`) et l'envoie en photo avec la légende fournisseur/modèle |
| `/history` | les 20 derniers messages de la session courante |
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

## Rappels persistants

Les rappels sont stockés dans `telegram_reminders` et planifiés sur la JobQueue de python-telegram-bot : ils **survivent aux redémarrages** (rechargés au boot). Les heures utilisent `TIMEZONE`, indépendamment de l'horloge du conteneur.

## Notifications inter-canaux (Phase 23.c)

Pour les **profils web liés**, Telegram et l'interface web se notifient mutuellement les événements pertinents :

- **Web → Telegram** — la fin (ou l'échec) d'un workflow, la fin d'une génération d'image, ou une longue réponse terminée pendant que l'onglet du navigateur était masqué déclenchent un message dans le chat lié.
- **Telegram → Web** — un rappel déclenché ou un document ingéré via `/kb` apparaissent comme un toast/badge dans la sidebar web (livré en direct via un flux SSE, ou récupéré au prochain chargement de la page).
- **`/notify on|off`** — coupe/réactive le côté Telegram du pont pour ce chat (**par chat, ON par défaut**). Le côté web possède sa propre matrice d'opt-in par type d'événement dans le panneau **Notifications** de la sidebar (voir [Chat web](chat.md#notifications-inter-canaux-phase-23c)).

Implémentation : `notification_service.py` (`notify_telegram` / `notify_web`), la table `notification_events` et `telegram_prefs.notify`.
