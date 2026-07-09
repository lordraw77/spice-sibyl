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
  glissez d'une **sortie** (à droite) vers une **entrée** (à gauche) pour connecter. Cliquez
  sur une arête pour la supprimer.
- **Droite** — l'**inspecteur** du nœud sélectionné (ses paramètres, générés depuis le schéma
  du type de nœud) ou, si rien n'est sélectionné, le **panneau exécution et déclencheurs**.

Enregistrez avec **Enregistrer**, basculez **Actif** pour laisser les déclencheurs se
déclencher, et **Exécuter** pour lancer le graphe — les nœuds passent au vert/bleu/rouge/gris
(ok/en cours/erreur/ignoré) en temps réel via SSE.

## Types de nœuds

| Catégorie | Nœuds |
|-----------|-------|
| **Déclencheur** | `manual`, `schedule`, `webhook`, `event` |
| **Action** | `tool.<nom>` — n'importe quel outil du registre (RSS, read_url, météo, kb_search, http_request, python_exec, MCP, personnalisé…) |
| **Logique** | `if` (branche vrai/faux), `switch` (branches par cas), `merge` (rassemble les entrées) |
| **Données** | `set` (construit un objet), `filter` (garde les éléments qui correspondent), `code` (sandbox Python) |
| **IA** | `llm.completion` (un appel au fournisseur), `llm.agent` (toute la boucle agent de la Phase 18) |

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

## API

Tout ce que fait l'UI est disponible sous `/v1/graph-workflows` (protégé par JWT). Voir le
[guide développeur](../developer-guide.md) pour la référence complète des endpoints.

Paramètres : `GRAPH_WORKFLOW_SCHEDULER_ENABLED` (activé par défaut) active la boucle de polling ;
`GRAPH_WORKFLOW_MAX_NODES` borne la taille d'un graphe.
