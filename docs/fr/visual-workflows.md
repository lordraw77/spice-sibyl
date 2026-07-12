# Workflows visuels en graphe de nœuds (Phase 29)

SpiceSibyl dispose de deux moteurs d'automatisation complémentaires :

- **Workflows agent** (`/workflows`, Phase 18) — vous donnez un *objectif* et un LLM itère
  de façon autonome sur tout le registre d'outils jusqu'à produire une réponse. Puissant,
  mais non déterministe et sans flux de contrôle explicite.
- **Workflows visuels** (`/graph-workflows`, Phase 29) — vous dessinez un *graphe* : un
  **déclencheur** alimente des **nœuds typés** reliés par des connexions. Le moteur exécute
  le graphe de manière **déterministe**, exactement dans la forme conçue. La boucle agent
  reste disponible ici via le nœud `llm.agent`, pour injecter de l'autonomie là où vous le
  souhaitez dans une pipeline déterministe.

![Éditeur de workflows visuels](screenshots/visual-workflow-editor.svg)

## Le canevas

L'éditeur se compose de trois volets :

- **Gauche** — vos workflows et une **palette de nœuds** par catégorie (Déclencheurs ·
  Actions · Logique · Données · IA). Chaque outil intégré, MCP et personnalisé apparaît
  automatiquement comme nœud `tool.<nom>` — aucun code supplémentaire par outil.
- Une barre d'outils au-dessus du canevas propose **Annuler/Rétablir** (`Ctrl+Z` /
  `Ctrl+Maj+Z`, aussi `Ctrl+Y`), **Copier/Coller** un nœud (`Ctrl+C` / `Ctrl+V` — colle un
  duplicata décalé avec le même type et les mêmes paramètres) et **Commentaire** : un nœud
  "post-it" côté client uniquement, sans poignées d'entrée/sortie et jamais câblé au flux —
  le moteur l'enregistre simplement comme `skipped`, sans changement backend. Les
  raccourcis sont ignorés pendant la saisie dans un champ. Une **zone de recherche**
  au-dessus de la palette filtre les nœuds par libellé ou type (et développe
  automatiquement les groupes MCP/personnalisés correspondants pendant la recherche).
- **Centre** — un **canevas SVG** sans dépendance. Glissez les nœuds pour les placer ;
  glissez d'une **sortie** (à droite) vers une **entrée** (à gauche) pour connecter.
  **Cliquez sur une arête** pour l'inspecter : le panneau de droite affiche source → cible,
  les **données qui y ont transité lors de la dernière exécution** et une liste des
  **champs disponibles avec leur chemin d'expression prêt à l'emploi** (p. ex.
  `$node.weather.output.result`) — un clic sur un champ le copie comme expression
  `{{ … }}`. Un bouton supprime la connexion. Quand un nœud échoue, son **message
  d'erreur** apparaît en rouge sous le nœud dans le panneau en direct (et dans le détail
  de la vue Exécutions).
- **Droite** — l'**inspecteur** du nœud sélectionné (ses paramètres, générés depuis le schéma
  du type de nœud) ou, si rien n'est sélectionné, le **panneau exécution et déclencheurs**.

Enregistrez avec **Enregistrer**, basculez **Actif** pour laisser les déclencheurs se
déclencher, et **Exécuter** pour lancer le graphe — les nœuds passent au vert/bleu/rouge/gris
(ok/en cours/erreur/ignoré) en temps réel via SSE. Le panneau d'exécution propose un champ
**payload** optionnel (JSON) : son objet devient le `$trigger` de l'exécution, ce qui permet
de tester à la main les graphes qui lisent `={{ $trigger.<champ> }}`, sans appel webhook.

## Types de nœuds

| Catégorie | Nœuds |
|-----------|-------|
| **Déclencheur** | `manual`, `schedule`, `webhook`, `event` |
| **Action** | `tool.<nom>` — n'importe quel outil du registre (RSS, read_url, météo, kb_search, http_request, python_exec, MCP, personnalisé…) · `http.request` (appel HTTP générique) · `subworkflow` (exécute un autre workflow en ligne) |
| **Logique** | `if` (branche vrai/faux), `switch` (branches par cas), `merge` (rassemble les entrées), `wait` (attend N secondes ou jusqu'à un instant précis) |
| **Données** | `set` (construit un objet), `filter` (garde les éléments qui correspondent), `code` (sandbox Python), `aggregate` (réduit un tableau — sum/avg/min/max/count/concat sur un champ), `batch` (découpe un tableau en blocs de taille fixe) |
| **IA** | `llm.completion` (un appel au fournisseur), `llm.agent` (toute la boucle agent de la Phase 18) |

> **Chaînes de secours** — `llm.completion` et `llm.agent` exposent un menu **Failover
> chain**, alimenté par les listes de modèles nommées définies dans Réglages → Modèles →
> Chaînes de secours LLM. Si elle est définie, un échec d'appel sur le `model` du nœud
> réessaie dans l'ordre les modèles restants de la chaîne ; la sortie du nœud porte alors
> `_failover: { tried: [...], used: "<model>" }`.

### Requêtes HTTP, composition et gestion d'erreurs

- **`http.request`** — appelle n'importe quelle API HTTP externe (`method`, `url`,
  `query`/`headers`, `body`, `timeout` ≤ 120 s). Sortie : `{ status, ok, headers, json, text }`.
  Par défaut une réponse non-2xx lève une erreur (les retries et la politique *En cas
  d'erreur* s'appliquent donc) ; `allow_errors` renvoie la réponse quel que soit le statut.
- **`subworkflow`** — exécute un autre workflow du même profil comme run enfant et renvoie
  `{ run_id, workflow_id, status, output }` (`output` = sortie du nœud terminal de l'enfant).
  Le `payload` devient le `$trigger` de l'enfant. Imbrication limitée à 5 niveaux.
- **En cas d'erreur** (inspecteur, section Avancé) — après épuisement des retries :
  **arrêter l'exécution** (défaut), **continuer sur la branche principale** avec `{ error }`,
  ou **router vers la branche d'erreur** : le nœud gagne une sortie **`error`** dédiée et
  `{ error, input }` suit cette branche pendant que `main` est ignorée — un try/catch
  dessiné sur le canevas.
- **Notifications** — `notify.telegram` (chat Telegram liée au profil ; `parse_mode`
  optionnel `Markdown`/`MarkdownV2`/`HTML` pour rendre le formatage — `**gras**` CommonMark
  est normalisé en `*gras*` à un astérisque, propre à Telegram ; les messages de plus de
  4096 caractères sont automatiquement découpés en plusieurs messages), `notify.email`
  (SMTP via `SMTP_*`), `notify.webhook` (Slack/Discord/ntfy/…), `notify.inapp`
  (cloche de la web UI, aucune configuration).
- **Vue Exécutions** — `/graph-workflows/runs` : le registre de toutes les exécutions du
  profil (statut, déclencheur, durée, résultats par nœud, suivi SSE en direct), séparé du
  designer ; l'éditeur se rattache à l'exécution en cours quand on rouvre son workflow.

## Expressions

Tout paramètre peut être un littéral **ou** une expression, distinguée par son préfixe :

- `={{ … }}` — une **mini-expression sûre**, analysée et évaluée sur une liste blanche
  (**pas de `eval`/`exec`**). Vous pouvez naviguer dans le contexte d'exécution et appeler un
  ensemble fixe de fonctions pures :

  ```
  ={{ $node.rss.output.result }}          # sortie d'un autre nœud
  ={{ $trigger.count }}                    # charge utile du déclencheur
  ={{ upper($json.title) }}                # fonction autorisée
  ={{ default($trigger.name, 'monde') }}
  ={{ $trigger.count > 3 }}                # comparaisons → if/switch
  Bonjour ={{ $trigger.name }} !           # interpolation de chaîne
  ```

  Contexte : `$node.<id>.output.<chemin>`, `$json` (entrée principale du nœud), `$trigger`,
  `$env` (variables d'environnement préfixées WF_), `$now`. Fonctions : `default`, `upper`,
  `lower`, `trim`, `len`, `join`, `slice`, `first`, `last`, `get`, `keys`, `values`, `round`, …

- `=py: …` — une **porte de sortie** vers la sandbox `python_exec` pour de la vraie logique.
  `ctx`, `input`, `node`, `trigger` sont disponibles ; la dernière expression (ou une variable
  `result`) devient la valeur.

Tout ce qui ne commence pas par `=` est un littéral.

## Déclencheurs

Depuis le panneau d'exécution :

- **Schedule** — cron / RRULE / langage naturel (« chaque jour à 9:00 »), interprété par le
  même moteur que les rappels. Une boucle de polling exécute les planifications dues et
  recalcule la prochaine échéance. (Ne se déclenche que si le workflow est **Actif**.)
- **Webhook** — une URL publique à jeton (`POST /api/v1/wf/hooks/{token}`). Le corps JSON
  devient `$trigger`. Ne se déclenche que si le workflow est Actif. Vous pouvez le protéger
  avec un secret partagé : `POST /v1/graph-workflows/triggers/{tid}/rotate-secret` en génère
  un (affiché une seule fois) ; ensuite la requête doit porter l'en-tête
  `X-Signature: sha256=<hmac-sha256 hexadécimal du corps brut>`, sinon elle est rejetée (401)
  avant même d'être interprétée.
- **Event** — événements internes. Réglez `config.event` sur le nom de l'événement (vide ou
  `*` pour tous les capter). Deux événements sont câblés aujourd'hui : `document.ingested`
  (après l'ingestion d'un document/URL — payload `{doc_id, filename, profile_id}`) et
  `chat.message.created` (après la persistance d'un échange de chat — payload
  `{conversation_id, profile_id}`).

Les déclencheurs **schedule** et **event** suivent un compteur d'échecs consécutifs
(`fail_count`/`last_error`) : après `GRAPH_WORKFLOW_TRIGGER_MAX_FAILURES` (5 par défaut)
échecs d'affilée, le déclencheur se désactive automatiquement et une notification in-app
est levée. Le réactiver (`POST /triggers/{tid}/enable`) remet le compteur à zéro.

### Vue Planifications — aperçu des déclencheurs multi-workflows

`/graph-workflows/schedules` (Phase 30.e, même groupe de navbar et feature flag) liste
**une ligne par déclencheur** sur tous les workflows du profil : nom du workflow, type de
déclencheur, prochaine exécution (déclencheurs schedule), statut/heure de la dernière
exécution, compteur d'échecs consécutifs et un interrupteur activer/désactiver — pour tout
voir en un coup d'œil sans ouvrir chaque workflow, ainsi que **Lancer** et **Supprimer**.
Backend : `GET /v1/graph-workflows/schedules`.

> **Un déclencheur ne se déclenche que si son *workflow* est Actif** — l'activation du
> déclencheur est indépendante du drapeau Actif du workflow (à changer depuis le
> concepteur, ou via la pastille Actif/Inactif à côté du nom du workflow ici). Un
> déclencheur parfaitement configuré et activé sur un workflow Inactif ne se déclenchera
> jamais ; le formulaire **+ Nouveau déclencheur** avertit et propose une activation en un
> clic quand le workflow choisi est Inactif — c'est la cause la plus fréquente d'une
> planification fraîchement créée qui ne fait rien en silence.

**Créer un déclencheur** (Phase 30.f) — le panneau **+ Nouveau déclencheur** choisit un
workflow et un type (`schedule`/`webhook`/`event`) ; pour `schedule` il expose un motif
structuré au lieu du langage naturel libre : **Quotidien** (une heure HH:MM),
**Hebdomadaire** (un ou plusieurs jours + heure), **Cron** (préréglages comme "toutes les
15 min"/"toutes les heures"/"minuit"/"jours ouvrés à 9h" qui remplissent un **champ cron
libre à 5 champs**, toujours modifiable, validé avec `croniter`), **Une fois** (date
optionnelle + heure). Les déclencheurs `event` prennent un nom d'événement libre
(`document.ingested` et `chat.message.created` sont câblés aujourd'hui) ; les `webhook` ne
nécessitent aucune config ici — le secret de signature se génère/renouvelle depuis le
concepteur après création.

### Production : concurrence, usage de tokens, alertes

- **Plafond de concurrence** — un sémaphore `GRAPH_WORKFLOW_MAX_CONCURRENT_NODES` (8 par
  défaut) limite le nombre de nœuds indépendants exécutés en parallèle dans une même run.
- **Usage de tokens** — la sortie des nœuds `llm.completion` et `llm.agent` inclut une clé
  `_usage` (`{tokens_in, tokens_out, tokens_total}`, cumulée sur les étapes de l'agent)
  quand le fournisseur la renvoie ; `null` sinon. Le coût n'est pas estimé — aucune table
  de tarifs par modèle n'existe encore dans le projet.
- **Alerte sur échecs récurrents** — après `GRAPH_WORKFLOW_RUN_FAILURE_ALERT_THRESHOLD`
  (3 par défaut) échecs consécutifs du même workflow, une notification in-app est levée
  une seule fois (pas à chaque échec suivant).
- **Cache de réponses** — `llm.completion` et chaque étape de `llm.agent` réutilisent le
  même cache de réponses que le chat (`RESPONSE_CACHE_ENABLED`, `RESPONSE_CACHE_TTL_SECONDS`,
  `RESPONSE_CACHE_MAX_ENTRIES`, plus la couche floue `SEMANTIC_CACHE_*` de la Phase 26). Une
  requête `(model, messages, temperature, max_tokens)` identique évite entièrement le
  fournisseur ; la sortie du nœud porte `_cache: "hit" | "semantic" | "miss"` à côté de
  `_usage`. Les étapes `llm.agent` avec appels d'outils ne sont jamais mises en cache (même
  règle que le chat : une requête avec `tools` n'obtient jamais de clé de cache).

## Versions et exécutions

Chaque enregistrement crée une version immuable ; vous pouvez lister les versions et revenir
en arrière. Chaque exécution stocke le graphe exécuté, le contexte résolu et un enregistrement
par nœud (entrée, sortie, erreur, durée) inspectable après coup.

Comme chaque valeur est persistée, l'éditeur n'a pas besoin d'une exécution en direct pour
afficher des données : à l'ouverture d'un workflow il charge **la dernière sortie
enregistrée de chaque nœud sur toutes les exécutions passées** (`GET /{id}/node-outputs`) ;
cliquer sur une flèche montre donc les champs et le payload qui y ont transité
historiquement — avec la note « données d'une exécution passée » et son horodatage. Une
nouvelle exécution remplace simplement ces valeurs par les données en direct.

**Export** : le bouton *Exporter* (ou `GET /{id}/export`) télécharge le workflow en
snapshot JSON portable (`{ kind, schema_version, name, description, graph, … }`) ; le même
corps est ré-importable via `POST /v1/graph-workflows`.

**Import** : le bouton 📥 à côté de **Nouveau** (en haut de la liste des workflows) ouvre
un fichier `.workflow.json` depuis le disque — exactement le fichier produit par
**Exporter** — et crée un nouveau workflow à partir de celui-ci, ouvert immédiatement
pour édition. Seuls `name`, `description` et `graph` sont lus ; les champs propres à
l'export (`kind`, `schema_version`, `exported_at`, …) sont acceptés et ignorés. Un fichier
JSON invalide ou qui n'est pas un workflow est rejeté côté client avec un message
d'erreur, sans être envoyé au serveur.

## API

Tout ce que fait l'UI est disponible sous `/v1/graph-workflows` (protégé par JWT). Voir le
[guide développeur](../developer-guide.md) pour la référence complète des endpoints.

Paramètres : `GRAPH_WORKFLOW_SCHEDULER_ENABLED` (activé par défaut) active la boucle de polling ;
`GRAPH_WORKFLOW_MAX_NODES` borne la taille d'un graphe.
