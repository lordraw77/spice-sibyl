# Mémoire et personnalisation

Fonctionnalités de la phase 19 : mémoire persistante par profil, titres automatiques, cache des réponses, feedback sur les réponses et page Informations.

## Mémoire persistante par profil

**Ce que ça fait.** SpiceSibyl retient des faits vous concernant à travers les conversations (préférences, faits personnels, projets en cours, instructions permanentes). Après chaque échange persisté, un appel LLM asynchrone à faible coût (`MEMORY_EXTRACTION_MODEL`, défaut = `DEFAULT_MODEL`) extrait les informations notables et les consolide dans la table `profile_memories` (déduplication automatique, plafonnée à `MEMORY_MAX_ITEMS` souvenirs). Quand la mémoire est active, les souvenirs activés sont compactés dans un bloc `<user_memory>` ajouté au prompt système (budget de `MEMORY_MAX_CHARS` caractères, les plus récents d'abord).

**Comment l'utiliser.**
- Page dédiée **Mémoire 🧠** (`/memory`, **Ressources → Mémoire** dans la barre de navigation, ou le lien *Gérer →* à côté de l'interrupteur Mémoire dans la barre latérale) : liste des souvenirs avec catégorie (⭐ préférence, 💡 fait, 📁 projet, 📌 instruction), ajout manuel avec choix de catégorie, activation/désactivation ou suppression par souvenir, **Tout oublier**. La case **extraction automatique des souvenirs (profil)** — l'interrupteur *au niveau du profil* (OFF = ni extraction ni injection pour tout le profil) — se trouve ici aussi.
- L'interrupteur **Mémoire ON/OFF** dans la section **Fonctions** de la barre latérale est l'interrupteur *par chat* (incognito) : OFF = les nouvelles requêtes n'utilisent ni n'alimentent la mémoire.
- Les réponses personnalisées par la mémoire affichent la puce **🧠 mémoire** sous le message.

**Depuis Telegram.** `/memory on|off` bascule la mémoire dans le chat courant (persistée dans `telegram_prefs`) ; `/memory list` affiche les souvenirs du profil web associé via `/link` ; `/memory del <id>` en oublie un. Injection et extraction ne fonctionnent que pour les utilisateurs associés.

**Configuration.**

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MEMORY_ENABLED` | `true` | Interrupteur global de la fonctionnalité |
| `MEMORY_EXTRACTION_MODEL` | *(vide = `DEFAULT_MODEL`)* | Modèle de l'appel d'extraction asynchrone |
| `MEMORY_MAX_CHARS` | `2000` | Budget en caractères du bloc injecté |
| `MEMORY_MAX_ITEMS` | `100` | Souvenirs max par profil |

API : `GET/POST /v1/memories`, `PATCH/DELETE /v1/memories/{id}`, `DELETE /v1/memories` (tout oublier), `GET/PUT /v1/memories/settings`.

## Titres automatiques (auto-titling LLM)

**Ce que ça fait.** Après le premier échange persisté d'une conversation, une tâche d'arrière-plan génère un titre concis (max 6 mots, dans la langue de la conversation) remplaçant l'ancienne heuristique « 60 premiers caractères du premier message ». La liste des conversations se rafraîchit d'elle-même quelques secondes plus tard.

**Configuration.** `AUTO_TITLE_ENABLED` (défaut `true`), `TITLE_MODEL` (vide = `MEMORY_EXTRACTION_MODEL`, puis `DEFAULT_MODEL`).

## Cache des réponses

**Ce que ça fait.** Les réponses terminées vont dans un cache LRU en mémoire, indexé exactement sur modèle + messages + température + max tokens. Une requête identique dans le TTL saute complètement le fournisseur : la réponse est rejouée d'un coup avec la puce **⚡ cache** et une latence nulle. Les requêtes avec outils, modèles `agent/*` et contenu multimodal (images) ne sont jamais mises en cache.

**Configuration.** `RESPONSE_CACHE_ENABLED` (défaut `true`), `RESPONSE_CACHE_TTL_SECONDS` (défaut `600`), `RESPONSE_CACHE_MAX_ENTRIES` (défaut `256`). Les stats hit/miss sont visibles sur la page **Informations**.

## Cache sémantique des réponses

**Ce que ça fait.** Étend le cache à correspondance exacte avec une correspondance *approximative*. En cas de miss exact, le dernier message utilisateur est vectorisé (via la même chaîne d'embedding que le RAG) et comparé par similarité cosinus aux réponses récentes en cache dans le même bucket modèle + température + max tokens. Une correspondance au-dessus du seuil rejoue la réponse stockée avec la puce **⚡~ cache** — ainsi des paraphrases comme « Comment réinitialiser mon mot de passe ? » et « Comment puis-je réinitialiser mon mot de passe ? » réutilisent une seule réponse sans appel au fournisseur. Les mêmes exclusions s'appliquent (outils, `agent/*`, multimodal) et le système retombe silencieusement sur la seule correspondance exacte lorsqu'aucun fournisseur d'embedding n'est joignable.

**Configuration.** `SEMANTIC_CACHE_ENABLED` (défaut `false`), `SEMANTIC_CACHE_THRESHOLD` (cosinus, défaut `0.92`), `SEMANTIC_CACHE_MAX_ENTRIES` (fenêtre de balayage, défaut `256`). Les compteurs hit/miss sémantiques apparaissent à côté des exacts dans les stats de cache de la page **Informations**.

## Feedback sur les réponses (👍/👎)

**Ce que ça fait.** Chaque réponse persistée de l'assistant peut être notée pouce haut/bas (note facultative sur 👎). Les évaluations alimentent un jeu de données exportable pour l'évaluation hors ligne des modèles.

**Comment l'utiliser.**
- Survolez une réponse : 👍 et 👎 apparaissent parmi les actions. Recliquer l'icône active efface la note.
- Exportez le jeu de données depuis `GET /v1/feedback/export` : chaque réponse notée est associée au prompt qui l'a générée (id du message, modèle, fournisseur, note, commentaire).
- Harnais de régression : `backend/scripts/eval_regression.py` rejoue les prompts notés 👍 contre la passerelle et signale les réponses qui s'écartent trop de celles approuvées.

```bash
python backend/scripts/eval_regression.py dataset.json \
  --base-url http://localhost:8800/api/v1 \
  --email admin@example.com --password ... [--model groq/llama-3.1-8b-instant]
```

## Page Informations

**Ce que ça fait.** L'entrée **Info** de la barre de navigation ouvre une page avec : version de l'interface web (du `package.json` au moment du build), version/environnement/uptime du backend (`GET /v1/info`), modèle par défaut, base de données (chemin et taille), endpoints API utilisés (URL de base, health, readiness, métriques, lien vers la doc OpenAPI), état READY/DEGRADED en direct et la liste des fonctionnalités activées avec les statistiques du cache.

**Configuration.** La version du backend vient de `APP_VERSION` (défaut aligné sur la release) ; les builds Docker l'estampillent automatiquement depuis le tag de release (`make release VERSION=v1.9.0`).
