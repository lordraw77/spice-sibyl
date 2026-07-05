# Chat web

La page principale de la console. À gauche une **barre latérale légère** avec seulement les contrôles du chat courant (profil, **Modèle**, **Système**, **Paramètres**) et les **interrupteurs ON/OFF** des fonctions ; la conversation est au centre avec le composeur en bas. La liste des conversations s'ouvre dans un **panneau** dédié (bouton *Conversations* ou `Ctrl+K`).

![Conversation avec télémétrie](screenshots/chat-conversazione.png)

## Conversations et streaming

**Ce que ça fait.** Chaque échange est stocké dans SQLite (par profil) avec sa télémétrie complète : fournisseur, latence, temps au premier token, tokens prompt/completion, vitesse (tok/s) — affichés au pied de chaque réponse. Les réponses arrivent en streaming via SSE.

**Comment l'utiliser.**
- **Nouvelle conversation** : bouton **+ Nouvelle** dans la barre latérale ou le panneau Conversations (ou `Alt+N`).
- **Ouvrir/sélectionner une conversation** : bouton **Conversations** dans la barre latérale (ou `Ctrl+K`) → ouvre le **panneau** avec recherche, filtre par étiquette, sélection et suppression ; en choisir une charge la conversation et ferme le panneau.
- **Sélection du modèle** : section **Modèle** de la barre latérale — filtre par capacité (chat, vision, tools, free…), recherche textuelle, un filtre de **fournisseurs visibles** (voir ci-dessous), puis choix dans le menu. Des badges sous le sélecteur montrent fournisseur, état de configuration et capacités.
- **Envoyer** : tapez dans le composeur et validez avec Entrée ; pendant la génération, le bouton d'envoi devient **Stop** et interrompt le flux.
- **Supprimer** : icône corbeille sur l'entrée de conversation, dans le panneau Conversations.

**Filtre des fournisseurs visibles.** Sous le sélecteur de modèle, une rangée de puces (une par fournisseur activé) permet de choisir **quels fournisseurs** apparaissent dans le sélecteur ; le choix est persisté. Pour choisir au contraire **quels modèles individuels** d'un fournisseur apparaissent, utilisez la page [Fournisseurs](providers-and-models.md).

**Indicateurs de chargement.** Une barre animée sous la barre supérieure montre la phase en cours : ambre en attente du modèle (« En attente du modèle… »), bleue pendant l'exécution d'outils (« Exécution des outils… »), rythme standard pendant le streaming (« Génération en cours… »).

## Actions sur les messages

Boutons révélés au survol de chaque message :

| Action | Où | Effet |
|--------|-----|-------|
| 📋 Copier | tous | copie le texte dans le presse-papiers |
| 🔊 TTS | réponses | lit le message à voix haute (Web Speech API, dans la langue active) ; réappuyez pour arrêter |
| 🔁 Régénérer | dernière réponse | demande une nouvelle réponse **en créant une branche** (voir ci-dessous) |
| ✏️ Modifier | dernier message utilisateur | modifier et renvoyer |
| 📌 Épingler | tous | ajoute/retire le message de la barre des épinglés au-dessus du chat (clic pour y sauter) |

## Branches de réponse

**Ce que ça fait.** Régénérer n'écrase pas : les deux réponses sont conservées comme branches parallèles (persistées dans SQLite avec `parent_id` + `branch_index`).

**Comment l'utiliser.** Les réponses avec alternatives montrent des flèches `< 1/3 >` pour naviguer entre branches ; la conversation continue depuis la branche sélectionnée.

## Prompt système, modèles et paramètres

- **Système** (barre latérale) : instructions système persistantes (localStorage), avec actions enregistrer/effacer.
- **Modèles** (page dédiée `/templates`, **Ressources → Modèles** dans la navbar) : bibliothèque de prompts système réutilisables (« Code review », « ELI5 »…). Créez/modifiez/supprimez ; **Appliquer** définit le modèle comme prompt système et vous ramène au chat.
- **Paramètres** (barre latérale) : curseur de **température** (0–2) et champ **max tokens**, envoyés avec chaque requête. L'opt-in des notifications de fin se trouve ici aussi (voir [Interface](interface.md)).

## Tool calling dans le chat

Interrupteur **Tool calling ON/OFF** dans la barre latérale. Activé, le modèle peut invoquer les outils enregistrés (intégrés, personnalisés, MCP) ; appels et résultats apparaissent comme bulles dédiées — avec un spinner sur les appels en attente de résultat. Détails dans [Appel d'outils](tool-calling.md).

## Images et génération d'images

- **Vision (image → texte)** : joignez des images avec le bouton 🖼 du composeur, par glisser-déposer sur la zone de chat (superposition visuelle, `image/*` seulement, 20 Mo max) ou en collant depuis le presse-papiers. Les images sont envoyées en base64 aux modèles compatibles vision (Gemini, Llama-4-Scout sur Groq, …).
- **Génération (texte → image)** : commande `/imagine <prompt>` dans le composeur. Utilise la chaîne de repli `IMAGE_GENERATION_CHAIN` (format `provider:model,...` ; fournisseurs pris en charge : Gemini/Imagen, HuggingFace FLUX.1-schnell, Cloudflare SDXL, Together FLUX.1-schnell-Free). Endpoint direct : `POST /api/v1/images/generations`.

## Saisie vocale

Bouton 🎤 dans le composeur (Web Speech API) : le bouton pulse pendant l'écoute et le texte transcrit atterrit dans le composeur.

## Interrupteurs ON/OFF des fonctions

La section **Fonctions** de la barre latérale a trois interrupteurs, chacun avec un lien **Gérer →** vers sa page :

- **Tool calling ON/OFF** — active l'usage des outils pour le tour de chat (gestion sur `/tools`).
- **Knowledge (RAG) ON/OFF** — activé, les chunks les plus pertinents sont injectés dans le message et les sources apparaissent comme puces de citation sous la réponse (documents sur `/knowledge`). Détails dans [Base de connaissances et RAG](knowledge-rag.md).
- **Mémoire ON/OFF** — ON = les souvenirs du profil sont utilisés ; OFF = chat incognito (souvenirs sur `/memory`). Détails dans [Mémoire et personnalisation](memory-and-personalization.md).

## Recherche dans les conversations

**Ce que ça fait.** Recherche plein texte (SQLite FTS5, index synchronisé par triggers) dans toutes les conversations du profil.

**Comment l'utiliser.** Ouvrez le panneau **Conversations** (bouton de la barre latérale ou `Ctrl+K`) et utilisez la barre « Rechercher dans les conversations… » ; les résultats s'affichent en ligne avec extraits surlignés ; `Échap` efface la recherche. Endpoint : `GET /api/v1/conversations/search?q=...`.

## Organisation : étiquettes

Étiquettes colorées assignables aux conversations via popover, avec une **barre de filtres** dans le panneau Conversations. La **gestion des étiquettes** (créer/modifier/supprimer avec choix de couleur) vit sur la page dédiée `/tags` (**Ressources → Étiquettes** dans la navbar).

## Export et partage

- **Export** : boutons **MD** et **JSON** dans la barre supérieure pour télécharger la conversation courante (`GET /conversations/{id}/export?format=md|json`).
- **Partage** : le bouton **Partager** génère un lien public en lecture seule (`POST /conversations/{id}/share` → jeton unique ; page `/shared/{token}` avec rendu markdown et coloration syntaxique, accessible sans connexion). Le lien est copié dans le presse-papiers.

## Rendu

Markdown via `marked` avec assainissement DOMPurify ; blocs de code avec coloration `highlight.js` selon le langage.
