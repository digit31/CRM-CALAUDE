# Rapport d'analyse — Application CRM/SIG FTTH (`mon_crm_sig`)

> **Généré le** 2026-07-02 · **Méthode** : analyse multi-agents par sous-système, puis vérification adversariale de chaque constat dans le code réel (58 constats produits, **57 retenus**, 1 réfuté, verdicts *Confirmé / Partiel*). **Analyse en lecture seule — aucune modification du code.**

## 1. Résumé exécutif

`mon_crm_sig` est une application web locale de gestion d'études fibre optique (FTTH), bâtie en **FastAPI + SQLAlchemy (SQLite) + GeoPandas + Jinja2/Leaflet**. Elle orchestre trois modules métier (CRM, SIG, Reporting) autour de projets d'affaires : création d'arborescences de livrables, import de Shapefiles, affichage cartographique, export KMZ/SHP normés et génération de fiches PDF. Le code applicatif est compact (11 fichiers `.py`, 7 templates), l'essentiel du volume (1,9 Mo) étant des données de projets.

**Maturité : prototype fonctionnel, non prêt pour la production.** Trois points dominent :
1. **Sécurité quasi absente** — aucune authentification/autorisation sur aucune route (critique), path traversal en écriture via l'upload de fichiers (critique), et plusieurs XSS DOM côté carte/table attributaire (haut).
2. **Aucune gouvernance du code** — zéro commit git (2 dépôts vides), pas de README/tests/CI, dépendances non épinglées et incomplètes (`simplekml`, `chardet` manquants).
3. **Robustesse fragile** — un bug bloquant confirmé (`NameError` sur la nomenclature client), gestion transactionnelle incohérente, sérialisation GeoJSON/PDF sensible aux données réelles (NaN/Inf, accents Unicode).

Le bind loopback (`127.0.0.1`) atténue l'exposition réseau, mais ne protège ni contre un rebind, ni contre CSRF/DNS-rebinding via navigateur local.

---

## 2. Stack technique & architecture

**Backend :** Python 3, FastAPI (`title="GeoCRM SIG Local"`, v1.0.0), Uvicorn (`127.0.0.1:8000`, `reload=True`), SQLAlchemy ORM.
**Données :** SQLite mono-fichier `crm_sig.db` (~76 Ko), tables créées au démarrage via `Base.metadata.create_all` (`main.py:37`), sans Alembic.
**SIG :** GeoPandas / Shapely / Fiona-GDAL / pyproj, `simplekml` (KMZ), `chardet` (détection encodage DBF).
**Reporting :** `fpdf2`.
**Frontend :** SSR Jinja2, Tailwind CDN, Leaflet 1.9.4 (unpkg), tuiles OSM, JS vanilla inline. Aucun asset local (`static/css`, `static/js` vides).

### Flux applicatif

```
                       Navigateur (Jinja2 + Tailwind CDN + Leaflet)
                                     │  formulaires POST 303  /  fetch JSON
                                     ▼
   run.py  ──uvicorn──►  app/main.py  (FastAPI, ~630 l.)
                              │  Depends(get_db)  [SEULE dépendance — aucune auth]
        ┌─────────────────────┼─────────────────────────────┐
        ▼                     ▼                               ▼
  crm/crm_service.py     gis/gis_handler.py             reporting/
  (projets, clients,     gis/exporter.py                pdf_generator.py
   users, logs,          (SHP→GeoJSON→carte,            (fiche synthèse
   nomenclature,         KMZ, SHP normés)                fpdf2)
   arbo disque)                │                              │
        │                      │ geopandas/fiona/GDAL         │
        ▼                      ▼                              ▼
  database.py (SQLAlchemy) ── crm_sig.db (SQLite)      app/static/projects_data/
                                                        (Shapefiles, KMZ, PDF)
```

Aucun middleware, aucune configuration CORS, aucun gestionnaire d'exception global.

---

## 3. Modèle de données

5 tables, schéma ORM strictement conforme au schéma réel (aucune dérive).

| Table | Colonnes clés | Relations / contraintes |
|---|---|---|
| `clients` | `id`, `nom` (index), `email` (UNIQUE, nullable), `nomenclature` (JSON), `date_creation` | `1—N` projets |
| `projets` | `id`, `nom` (index), `reference` (UNIQUE, nullable), `statut`, `chemin_dossier`, `client_id` (FK, nullable), dates | `N—1` client ; `1—N` couches/logs |
| `couches_sig` | `id`, `projet_id` (FK, NOT NULL), `chemin_fichier`, `type_geometrie`, `nb_entites`, `date_import` | cascade `all, delete-orphan` |
| `logs_generation` | `id`, `projet_id` (FK, NOT NULL), `utilisateur_id` (FK), `date_generation` | cascade `all, delete-orphan` |
| `utilisateurs` | `id`, `nom`, `email` (UNIQUE, NOT NULL, index), `role`, `date_creation` | **aucune colonne mot de passe** |

**Points structurels :**
- **`PRAGMA foreign_keys = 0`** (`database.py:27`) : SQLite n'applique **aucune** contrainte FK ; l'intégrité repose entièrement sur l'ORM.
- Cascade `delete-orphan` présente sur `Projet.couches`/`Projet.logs`, **absente** sur `Client.projets` (`models.py:32`) → suppression client = projets orphelins (`client_id → NULL`).
- Tous les horodatages en `datetime.utcnow` naïf (sans fuseau).
- État observé : 4 projets, 56 couches, 15 logs, 0 client, 0 utilisateur ; `integrity_check = ok`.

---

## 4. Cartographie fonctionnelle

**Pages (Jinja2) :** dashboard, map_view (carte + table attributaire éditable), etudes, etudes_global, clients, access.

**API REST (extraits) :**
- Projets : `POST /api/projets` (`main.py:151`), `DELETE /api/projets/{id}` (`:181`), `POST .../statut` (`:168`).
- Clients : `POST /api/clients` (`:190`), `.../delete` (`:204`), `POST .../nomenclature` (`:232`).
- Utilisateurs : `POST /api/access` (`:211`), `.../delete` (`:224`).
- SIG : `POST .../upload-shp` (`:248`), `GET .../geojson` (`:329`), `GET .../attributs` (`:352`), `POST .../sauvegarder-attributs` (`:595`).
- Génération : `POST .../generer-etude/{type}` (`:424`), `POST .../pdf` (`:385`).

**Chaîne SIG import → carte → export :**
1. **Import** — upload multi-fichiers dans `01_Inputs_SHP`, une `CoucheSIG` par `.shp`, palette de 10 couleurs cyclique.
2. **Lecture/reprojection** — `lire_shapefile` (`SHAPE_RESTORE_SHX=YES`), harmonisation vers EPSG:4326.
3. **Carte** — `convertir_en_geojson` → `L.geoJSON` (circleMarker, popups, fitBounds).
4. **Édition** — table attributaire éditable, sauvegarde POST JSON.
5. **Export** — KMZ stylé par couche métier (`simplekml`) ; SHP normés (pattern Strategy `NORME_PAR_DEFAUT`/`NORME_ORANGE_FTTH`, Lambert-93 EPSG:2154).

**Reporting :** `generer_fiche_synthese` produit une fiche PDF (en-tête marque, infos générales, tableau des couches) déposée dans le dossier projet.

---

## 5. Points forts

- **Séparation des responsabilités claire** : `main.py` orchestre, les modules `crm`/`gis`/`reporting` sont découplés et sans dépendance à la couche web ; `pdf_generator` et `gis_handler` ne connaissent ni la BDD ni la carte.
- **Couche SIG soignée** : reprojection systématique pour Leaflet, pattern Strategy pour les exports normés, reconstruction automatique du `.shx` manquant (`gis_handler.py:21`), détection d'encodage DBF via `chardet`.
- **Cohérence schéma ORM / base réelle** : aucune dérive, index et contraintes UNIQUE conformes, cascades ORM correctes sur couches/logs.
- **Logging centralisé** (`app/__init__.py`, logger `crm_sig`, `error.log`) réellement exploité pour le diagnostic.
- **Robustesse ciblée du reporting** : la section couches gère les clés manquantes (`.get(clé,"?")`) et la liste vide.
- **Bind loopback par défaut** (`run.py:6`) : posture réseau prudente hors production.

---

## 6. Constats priorisés

### 🔴 CRITIQUE

**[Sécurité] Aucune authentification ni autorisation** — `main.py:59` · *Confirmé*
Aucune route n'impose d'auth ; seule dépendance = `Depends(get_db)`. Création/suppression de projets, clients, utilisateurs, upload et générations accessibles anonymement. Le modèle `Utilisateur` n'a pas de mot de passe ; les rôles (Admin/Editeur/Lecteur) sont purement décoratifs et jamais vérifiés.

**[Sécurité] Path traversal / écriture de fichier arbitraire (upload-shp)** — `main.py:275` · *Confirmé*
`chemin_dest = os.path.join(dossier_input, fichier.filename)` avec `filename` client non assaini, puis `open(..., "wb")`. Deux vecteurs : traversée `..\` et **chemin absolu** (qui ignore totalement `dossier_input`). Permet l'écrasement/création de fichiers arbitraires selon les droits du process → potentielle exécution de code. Correctif : `os.path.basename`, rejet `..`/absolus, vérification `realpath` sous le dossier, whitelist d'extensions.

### 🟠 HAUT

**[Sécurité] Chemin client-contrôlé passé à `lire_shapefile` sans validation** — `main.py:344` · *Confirmé*
`couche.chemin_fichier` (issu de l'upload non assaini) relu/réécrit en confiance par `geojson`/`attributs`/`sauvegarder-attributs`. La branche `[Livrable]` (`:615`) réécrit directement le chemin stocké sans `basename` → écriture arbitraire. La lecture reste contrainte au parsing géospatial mais l'accès disque n'est pas confiné.

**[Bug] `NameError: 'Client' non défini` dans `maj_nomenclature_client`** — `crm_service.py:176` · *Confirmé*
`db.query(Client)...` alors que seul `models` est importé. Chaque appel (route `main.py:236`) lève `NameError`, transformé en HTTP 500 : **la sauvegarde de nomenclature client est totalement inopérante**. Correctif : `models.Client`.

**[Bug] `generer_reference` sujette aux collisions** — `crm_service.py:39` · *Partiel*
Référence = `COUNT(LIKE AFF-annee-%) + 1`. Race condition (2 créations simultanées → même réf → violation UNIQUE au 2e commit) et réémission de suffixe après suppression (hard-delete). Nuance : `unique=True` empêche les doublons silencieux (IntegrityError, pas duplication). Correctif : séquence/compteur atomique ou retry sur IntegrityError.

**[Robustesse] Sérialisation GeoJSON/SHP sans gestion NaN/Inf** — `gis_handler.py:108` · *Partiel*
`to_json()` sérialise NaN → `null` (OK) mais **Inf/-Inf → tokens `Infinity` invalides** ; `JSON.parse` côté front (`map_view.html:316`) échoue sans `.catch` → couche non affichée silencieusement. Correctif ciblé : `gdf.replace([np.inf,-np.inf], np.nan)` avant `to_json()`/`to_file`.

**[Bug] KMZ : anneaux intérieurs des polygones perdus** — `gis_handler.py:304` · *Partiel*
Seul `.exterior.coords` transmis à `newpolygon`, aucun `innerboundaryis` → trous silencieusement comblés (perte de données, **pas** une exception contrairement au titre). Label NaN → `'nan'` écrit tel quel.

**[Sécurité] XSS DOM — attributs de couche via `innerHTML` (table attributaire)** — `map_view.html:499` · *Confirmé*
`nom_couche`, noms de colonnes et valeurs DBF injectés sans échappement dans `innerHTML`. `<img src=x onerror=...>` s'exécute. Correctif : `escapeHtml()` ou `document.createElement`+`textContent`.

**[Sécurité] XSS DOM — popups Leaflet** — `map_view.html:331` · *Confirmé*
`'<b>' + key + '</b>: ' + value` sans échappement dans `bindPopup`. Mêmes vecteurs, déclenché au clic sur l'entité.

**[Sécurité] Aucune authentification côté client/serveur (vue frontend)** — `access.html` (formulaire création lignes 90-105) · *Confirmé*
Rôles décoratifs, header `127.0.0.1:8000` en dur sans effet. Recoupe le constat critique `main.py:59`.

**[Bug/Robustesse] `verifier_projection` sur CRS sans EPSG résolvable** — `gis_handler.py:71` · *Partiel*
`to_epsg()` renvoie `None` → reprojection redondante mais **pas de crash** (titre réfuté). Vrai défaut : `extraire_metadonnees` (`:95`) affiche `crs=None` pour un CRS custom valide. Correctif : comparer les objets CRS ; exposer `to_string()` en repli.

### 🟡 MOYEN

**[Sécurité] Injection HTML dans les descriptions KML** — `gis_handler.py:281` · *Confirmé*
`col`/`row[col]` interpolés bruts. Isolés en CDATA (KML reste valide) mais rendu HTML corrompu / XSS si le KMZ est ouvert dans un viewer web. Correctif : `html.escape`.

**[Sécurité] Détails d'exception renvoyés au client (HTTP 500)** — `main.py:165` (+ :202, :222, :242, :349, :378, :421, :593, :658) · *Confirmé*
`raise HTTPException(500, detail=str(e))` × 9 → fuite de chemins absolus, fragments SQL, noms internes. Correctif : message générique + log serveur. Note connexe : des `HTTPException 404` levés dans un `try` sont recapturés et transformés en 500 (ex. `:238`).

**[Sécurité] Absence de protection CSRF** — `clients.html:47` (+ endpoints mutateurs) · *Partiel*
Aucun jeton anti-CSRF. Endpoints à POST simple (formulaires urlencodés/multipart) trivialement forgeables cross-site ; ceux en JSON (`nomenclature`, `sauvegarder-attributs`) et `DELETE` sont protégés de facto par le préflight CORS (aucun en-tête CORS configuré). Exploitabilité conditionnée à un rebind hors loopback.

**[Sécurité] Nom de projet non assaini (path traversal dossier)** — `crm_service.py:51` · *Confirmé*
`f"{projet_id:04d}_{nom_projet.replace(' ','_')}"` : seuls les espaces filtrés. Un `nom` avec séparateurs + `../` remonte hors de `projects_data`. Caractères Windows interdits → `OSError` mais projet déjà commité (orphelin). Correctif : slugify/whitelist + vérif `realpath`.

**[Robustesse] Chemin absolu codé en dur pour le modèle SHAPE** — `main.py:489` · *Partiel*
`r"C:\Users\ALI\Desktop\CRM SIG\EXEMPLE\INPUT SHAPE APD FO"`. Sur une autre machine, **pas de crash** mais `glob` retourne `[]` → **défaillance silencieuse** (« SHAPE générée (0 fichiers) » en succès apparent). Correctif : externaliser en config + vérifier l'existence.

**[Robustesse] Commit par couche dans la boucle d'upload — état non transactionnel** — `main.py:310` · *Confirmé*
`db.commit()` par couche, aucun rollback global. Une couche en échec laisse les précédentes persistées. Erreurs remontées seulement si `nb_couches_ok == 0` ; en succès partiel, la liste `erreurs` est abandonnée (redirection `/map/{id}`). Correctif : commit unique final + propagation des erreurs.

**[Robustesse] `creer_projet` — dossier créé hors transaction (orphelin possible)** — `crm_service.py:77` · *Confirmé*
Premier `commit` (`:78`, `chemin_dossier=""`), puis `os.makedirs` + second `commit` (`:84`). Un échec après `:78` laisse un projet en base sans dossier ; `rollback` n'annule pas le premier commit. `chemin_dossier=""` pollue ensuite les `os.path.join` en aval.

**[Robustesse] Intégrité référentielle non appliquée par SQLite** — `database.py:27` · *Partiel*
`PRAGMA foreign_keys=0`, jamais réactivé. Le DELETE client via ORM **nullifie** `client_id` (pas de dangling), mais toute écriture hors-ORM ou INSERT avec `client_id` inexistant n'est pas protégée. Correctif : listener `connect` → `PRAGMA foreign_keys=ON`.

**[Bug] Suppression client → projets orphelins** — `crm_service.py:167` · *Partiel*
`Client.projets` sans cascade → SQLAlchemy émet `UPDATE projets SET client_id=NULL` (orphelinage silencieux, déterministe). Pour `Utilisateur`, `LogGeneration.utilisateur_id` reste pendant (vraie dangling reference). Correctif : politique explicite (RESTRICT/SET NULL/CASCADE) + FK actives.

**[Maintenabilité] Gestion transactionnelle incohérente** — `crm_service.py:106` · *Confirmé*
`creer_*` ont try/except+rollback ; `mettre_a_jour_statut`, `supprimer_*`, `maj_nomenclature_client`, `enregistrer_log` font `commit` nu. Un commit en échec laisse la Session sale → `PendingRollbackError` ultérieur. Correctif : uniformiser (context manager/décorateur).

**[Robustesse] Validation métier absente au niveau service** — `crm_service.py:106` · *Confirmé*
Aucun contrôle enum (statut, rôle), ni nom non vide, ni existence du client (`client_id`). Domaines documentés en commentaires seulement. Ni service ni routes ne valident.

**[Config] Icône KML depuis URL Google en dur (HTTP)** — `gis_handler.py:288` · *Confirmé*
`http://maps.google.com/.../placemark_circle.png` codé en dur, non embarqué (`savekmz` sans `addfile`) → dépendance réseau + contenu mixte. Correctif : embarquer un PNG local.

**[Robustesse] Encodage d'écriture SHP forcé UTF-8 vs lecture chardet** — `exporter.py:97` · *Partiel*
Incohérence conceptuelle réelle, mais **la corruption d'accents ne se matérialise pas** : GDAL/Fiona génère automatiquement un `.cpg` `UTF-8` (round-trip vérifié OK). Fragilité : dépend de la version GDAL et de la conservation du `.cpg`.

**[Bug] `generer_livrables_shp` — colonnes fragiles + perte du CRS modèle** — `gis_handler.py:196` · *Confirmé*
Dépendance implicite à une colonne géométrie nommée exactement `'geometry'`. Colonne input renommée → `KeyError` capté → livrable **vide silencieux** ; colonne modèle renommée → DataFrame nu → crash à `to_file` (`:211`, hors try). CRS input différent du modèle exporté sans reprojection. Correctif : `gdf.geometry.name` + reprojection explicite.

**[Robustesse] Pas de contrôle de troncature des noms de colonnes à 10 car. (DBF)** — `exporter.py:97` · *Confirmé*
Aucun contrôle avant `to_file` ESRI Shapefile ; collisions de préfixe possibles (écrasement/renommage OGR). Colonnes actuelles de `NORME_ORANGE_FTTH` ≤ 10 car. mais tout ajout futur casse.

**[Performance] Boucle `iterrows` non vectorisée (KMZ)** — `gis_handler.py:270` (fonction déf. `:218`) · *Confirmé*
`iterrows()` × boucle colonnes par entité, HTML par concaténation, O(n·m) Python pur, aucun cache ; pénalisant pour CABLES/SUPPORT. Aggravé par duplication multi-part et `get_encoding` (15000 octets par DBF).

**[Robustesse] Données dynamiques non nettoyées → exception police Helvetica (PDF)** — `pdf_generator.py:39` · *Partiel*
Caractère hors Latin-1 (`'`, `—`, emoji) → `FPDFUnicodeEncodingException` (pas `UnicodeEncodeError`) levée dès `cell()` (pas à `output()`) → RuntimeError, **aucun PDF produit**. Correctif : `add_font(uni=True)` ou translittération.

**[Robustesse] Débordement de cellule PDF (largeur fixe)** — `pdf_generator.py:98` · *Confirmé*
`cell(80,...)` pour le nom de couche (`:120`) sans wrap → chevauchement sur la colonne « Type Géométrie » (x=90). Correctif : `multi_cell()`.

**[Robustesse] Dépendance CDN sans SRI** — `base.html:10` · *Confirmé*
Tailwind (`:10`) et Google Fonts (`:38`) sans SRI ; Unsplash `<img>` (`etudes_global.html:14`, SRI inapplicable aux `<img>`) ; seul Leaflet a `integrity`. Tuiles OSM également non maîtrisées → app inutilisable hors-ligne.

### 🟢 FAIBLE

**[Robustesse] Lecture Shapefile échouée faute de `.shx`** — `error.log:1` · *Partiel* — Incident 2026-06-30 sur `SUPPORT.shp` (0003_Q). Le correctif proposé (`SHAPE_RESTORE_SHX=YES`) est **déjà présent** (`gis_handler.py:21`) ; la vraie piste est de comprendre pourquoi la restauration n'a pas opéré / mieux gérer les fichiers compagnons manquants.

**[Robustesse] Erreurs récurrentes « Out of range float » (attributs)** — `error.log:3` · *Confirmé* — 9 occurrences (couches #14, #22), route `attributs` (`main.py:377`), cause `to_dict(orient='records')` sans nettoyage NaN/Inf (`gis_handler.py:113-119`).

**[Robustesse] `date_creation`/`date_modification` en `datetime.utcnow` naïf** — `models.py:29` · *Confirmé* — 6 colonnes/5 tables ; templates affichent l'UTC brut → décalage 1-2 h. Correctif : `datetime.now(timezone.utc)`.

**[Bug] `generer_reference` en UTC en fin/début d'année** — `crm_service.py:37` · *Partiel* — Scénario **inversé** dans l'énoncé : le risque réel est le 1er janvier 00h00-00h59 locale (UTC encore 31/12) → réf année précédente. Fenêtre 1-2 h.

**[Robustesse] `supprimer_client`/`supprimer_utilisateur` sans gestion des dépendances** — `crm_service.py:167` · *Partiel* — Client → nullification déterministe ; Utilisateur → log orphelin/dangling. Pas « non déterministe » comme énoncé.

**[Maintenabilité] Duplication du pattern CRUD** — `crm_service.py:167` · *Partiel* — `obtenir_*` identiques, `supprimer_*` sans rollback ; nuances : `supprimer_projet` a un `logger.info` en plus, `creer_projet` est nettement plus riche (pas « quasi identique »).

**[Robustesse] `SHAPE_RESTORE_SHX` défini globalement via `os.environ`** — `gis_handler.py:21` · *Confirmé* — Affecte tout le process (y compris `gpd.read_file` de `main.py:509/635`). Correctif : `fiona.Env(...)` local + WARNING quand un `.shx` est reconstruit.

**[Robustesse] `get_encoding` — bare `except` et fallback silencieux** — `gis_handler.py:232` · *Confirmé* — `except:` → `'latin-1'` sans log, échantillon 15000 octets peu fiable (en-tête DBF ASCII). Attrape aussi `KeyboardInterrupt`.

**[Performance] Absence totale de cache** — `gis_handler.py:99` · *Confirmé* — `read_file`/`to_json`/`copy` recalculés à chaque requête d'affichage. Nuance : `to_crs` no-op si déjà en 4326. Piste : `lru_cache` sur (chemin, mtime).

**[Robustesse] `gdf.copy()` double l'empreinte mémoire** — `exporter.py:71` · *Partiel* — Pic ~2x (pas systématiquement 3x, `to_crs` conditionnel). La vraie optimisation : supprimer le `copy()` explicite redondant avant reprojection.

**[Robustesse] Nom de fichier PDF non assaini / collision** — `pdf_generator.py:130` · *Confirmé* — Caractères Windows interdits non filtrés ; `projet_id` non incorporé → écrasement silencieux entre projets de même référence/nom.

**[Robustesse] Filet/largeurs de tableau PDF codés en dur (200, 80+50+40)** — `pdf_generator.py:40` · *Partiel* — Dépendance implicite A4 portrait ; désalignement existant (tableau finit à x=180, filet à x=200).

**[Robustesse] Réponses `fetch` sans vérifier `r.ok`** — `etudes.html:468` (+ `map_view.html:567/614`, `dashboard.html:299`) · *Confirmé* — `r.json()` sur réponse non-JSON → alerte opaque ; `supprimerProjet` échoue silencieusement si `!r.ok`.

**[Maintenabilité] JS inline et dupliqué** — `etudes.html:406` · *Partiel* — Modal `modal-conflit-shp` + `fermerModalConflit` réellement dupliqués ; `switchTab` est **homonyme, pas dupliqué** (corps divergents). Templates ~35 Ko chacun.

**[Maintenabilité] Accessibilité (a11y)** — `map_view.html:112` · *Confirmé* — `aria-label`=0, `role=dialog`=0, gestion clavier=0. Boutons suppression (`clients.html:48`, `access.html:59`) sans libellé accessible (cas critiques).

### ℹ️ INFO

- **Dépendances importées absentes de `requirements.txt`** (`simplekml`, `chardet`) — `requirements.txt:1` · *Confirmé*. `pandas` couvert transitivement par geopandas ; l'export KMZ (`main.py:536`) échoue dès `import simplekml`.
- **`requirements.txt` liste les transitifs (shapely/fiona/pyproj) mais pas les imports directs** — `gis_handler.py:223` · *Confirmé*.
- **`reload=True` + bind loopback : profil développement** — `run.py:6` · *Confirmé*. Aucune valeur (host/port/reload) configurable.
- **Imports en profondeur de fonction** — `main.py:497` · *Partiel*. Seul `from app import models` (`:498`) est redondant ; `datetime`/`geopandas` sont des imports différés légitimes.
- **`reference` supposée non nulle + upload sans validation d'extensions** — `main.py:439` · *Partiel*. `AttributeError` latent (non atteignable par l'IHM) ; erreurs pyogrio brutes remontées.
- **Helpers CRUD sans docstring** — `crm_service.py:163` · *Confirmé*.
- **PDF : pas de validation des types consommés** — `pdf_generator.py:119` · *Confirmé*. `statut` sans fallback → « None » cosmétique.

---

## 7. Dette & code mort

- **Aucun versionnement** — 2 dépôts git (`CRM CALAUDE` racine + `mon_crm_sig` imbriqué), branche `master`, **0 commit**, 0 fichier suivi, pas de `.gitmodules`. Tout le travail est non sauvegardé. Un `git add .` stagerait 644 artefacts (`.pyc`, `crm_sig.db`, shapefiles/PDF). Réf : `.git/config:1`.
- **Fichiers de gouvernance absents** — pas de README, `.gitignore`, Dockerfile, config, ni tests. Dépendances non épinglées (aucun `==`) → reproductibilité fragile de la pile GDAL. Réf : `requirements.txt:1`.
- **Pas de stratégie de migration** — `create_all` uniquement (`main.py:37`, `checkfirst=True`, jamais d'`ALTER`) → drift silencieux ORM/base dès qu'un modèle évolue. Alembic absent. Migration PostgreSQL/PostGIS évoquée en commentaire seulement.
- **Code mort / paramètres inutilisés** :
  - `projet_id` requis mais jamais utilisé dans `generer_fiche_synthese` (`pdf_generator.py:52`) — *Confirmé*.
  - Dossiers `static/css` et `static/js` créés au démarrage mais **vides** (aucun asset servi).
- **Duplication** : modal SHP + fonctions de génération dupliqués entre `etudes.html` et `map_view.html`.

---

## 8. Questions ouvertes / décisions à prendre

1. **Modèle d'exposition** — l'application reste-t-elle strictement mono-poste loopback, ou est-elle destinée à être exposée (réseau/proxy) ? Le choix conditionne l'urgence de l'auth, du CSRF et de `reload=False`/host configurable.
2. **Authentification** — quel niveau viser (login + mot de passe hashé, application effective des rôles Admin/Editeur/Lecteur, ou simple annuaire) ? Le modèle `Utilisateur` n'a aucune notion d'identité aujourd'hui.
3. **Backend de données** — rester sur SQLite (avec FK activées + Alembic) ou concrétiser la migration PostgreSQL/PostGIS évoquée en commentaire ?
4. **Politique d'intégrité référentielle** — que doit faire la suppression d'un client ayant des projets : interdiction (RESTRICT), désassociation (SET NULL, comportement actuel implicite) ou cascade ?
5. **Confiance dans les chemins stockés** — faut-il confiner tous les `chemin_fichier`/`chemin_dossier` sous la racine projet et rejouer une validation à chaque lecture, ou traiter la seule sanitisation à l'upload comme suffisante ?
6. **Génération d'études « shape »** — le dossier modèle doit-il être une donnée de configuration externe, embarquée dans le dépôt, ou relative au projet ? Faut-il transformer la défaillance silencieuse (0 fichier) en erreur explicite ?
7. **Versionnement & données** — quels chemins ignorer (`.pyc`, `crm_sig.db`, `projects_data`, `error.log`) avant le premier commit, et où stocker les données de projets (dans le dépôt vs stockage externe) ?
8. **Rendu Unicode PDF** — enregistrer une police TTF Unicode (accents, apostrophes typographiques) ou imposer une translittération en amont des données saisies ?

---

*Fichiers clés cités :* `CRM SIG/mon_crm_sig/app/main.py`, `app/database.py`, `app/models.py`, `app/crm/crm_service.py`, `app/gis/gis_handler.py`, `app/gis/exporter.py`, `app/reporting/pdf_generator.py`, `app/templates/{base,map_view,access,clients,etudes,etudes_global,dashboard}.html`, `run.py`, `requirements.txt`, `error.log`.
