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
| **Logique** | `if` (branche vrai/faux), `switch` (branches par cas), `merge` (rassemble les entrées) |
| **Données** | `set` (construit un objet), `filter` (garde les éléments qui correspondent), `code` (sandbox Python) |
| **IA** | `llm.completion` (un appel au fournisseur), `llm.agent` (toute la boucle agent de la Phase 18) |

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
- **Notifications** — `notify.telegram` (chat Telegram liée au profil), `notify.email`
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
  devient `$trigger`. Ne se déclenche que si le workflow est Actif.
- **Event** — événements internes (document ingéré, rappel déclenché…).

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
