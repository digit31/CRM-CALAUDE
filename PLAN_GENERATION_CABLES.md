# Plan d'exécution — Génération automatique des câbles (APD FO)

> **Statut : EN ATTENTE DE VALIDATION.** Aucune modification de code n'est effectuée avant votre feu vert.
> Décisions validées : (1) découpe **BPE + BTS strict** · (2) attributs **défauts FreeMobile + nommage auto** · (3) direction **ORDRE croissant** · (4) sortie **à l'import via modal → couches livrable**.

## 1. Contexte / besoin

Lors de l'import des couches d'entrée d'un projet, si la couche **CABLES** est vide (ou absente) et qu'il s'agit d'une étude **APD FO**, l'application doit **générer automatiquement les câbles** à partir des couches `SUPPORT`, `BPE` et `BTS`, puis les écrire dans les **couches livrable**.

Principe métier (vérifié sur l'étude MORSCHWILLER-LE-BAS) : le réseau `SUPPORT` est une chaîne de tronçons ordonnés (`ORDRE`). Les **boîtes optiques** (BPE + site BTS) posées sur cette chaîne la découpent : **entre deux boîtes consécutives, tous les tronçons SUPPORT sont fusionnés en un seul câble**. `N` boîtes → `N-1` câbles.

## 2. Algorithme — `gis_handler.construire_cables(...)`

Nouvelle **fonction pure** (aucune dépendance web/BDD, conforme au principe d'indépendance du module) dans [gis_handler.py](CRM SIG/mon_crm_sig/app/gis/gis_handler.py).

```
construire_cables(support_gdf, bpe_gdf, bts_gdf, modele_cables_gdf,
                  params=defaults) -> GeoDataFrame (schéma CABLES)
```

Étapes :
1. **Harmonisation CRS** : tout reprojeté dans le CRS de `SUPPORT` (EPSG:2154 ici). Sortie dans ce même CRS.
2. **Graphe des supports** : nœuds = extrémités des tronçons agglomérées à une tolérance de snapping (défaut **0,5 m**, paramétrable) ; arêtes = tronçons (on conserve `ORDRE`, `EMPRISE`, `TYPE_STRUC`).
3. **Placement des boîtes** : chaque point BPE et le point BTS sont rattachés au **nœud support le plus proche** dans une tolérance **par défaut de 2,0 m** (paramétrable) ; si une boîte tombe en milieu de tronçon (au-delà de la tolérance), le tronçon est **scindé exactement à la projection** de la boîte (option « strict »).
   - *Vérifié sur l'étude* : FM028, FM027, FM029 et le BTS sont **exactement** sur un nœud (0,00 m) ; **FM025 est à 1,39 m** du réseau (boîte terminale décollée) → d'où la tolérance à 2 m pour l'accrocher au nœud terminal (130).
4. **Découpe en câbles** : parcours du réseau ; on marque les nœuds-boîtes comme coupures. Chaque « run » de tronçons reliant deux boîtes consécutives (en passant par des nœuds de degré 2 non-boîte) = **un câble**. Gère aussi les réseaux ramifiés (une boîte de degré ≥ 3 démarre plusieurs câbles).
5. **Fusion + orientation** : pour chaque câble, tronçons **triés par `ORDRE` croissant**, retournés si besoin pour se chaîner tête-à-queue, fusionnés en une seule `LineString` orientée dans le sens des `ORDRE` croissants.
6. **Attributs** (défauts FreeMobile + nommage auto) :
   - `EMPRISE` = emprise dominante des supports du câble (ex. `EUR68_001`)
   - `PROPRIETAI` = `GESTIONNAI` = `FREE MOBILE`
   - `ETAT` = `EN ETUDE` · `CAPACITE` = `48` · `NB_TUBE` = `4`
   - `POSE` = dérivé du `TYPE_STRUC` dominant des supports (`AERIEN`/`SOUTERRAIN`)
   - `LONGUEUR_R` = longueur géométrique (m) arrondie
   - `FABRICANT` / `REFERENCE` = vides (non déductibles)
   - `NOM` = `CODE` = `{CTR|CAD}_{EMPRISE}_{nn}` — **CAD** si le câble touche le **BTS** (adduction), **CTR** sinon ; `nn` = numéro incrémental (01, 02, …) attribué par `ORDRE` minimal croissant du câble.
7. **Sortie** : `GeoDataFrame` aux **colonnes exactes du modèle** (`NOM, CODE, EMPRISE, PROPRIETAI, GESTIONNAI, POSE, ETAT, FABRICANT, REFERENCE, LONGUEUR_R, NB_TUBE, CAPACITE`), CRS = 2154.

**Cas limites gérés** : < 2 boîtes → aucun câble (message clair) ; boîte non rattachable au réseau → ignorée + log WARNING ; `ORDRE` manquant → repli sur l'ordre géométrique du run.

> Note sur votre exemple : en **BPE + BTS strict**, le dernier découpage tombe sur la BPE `FM029` (et non le poteau 50896). Le dernier câble d'adduction = tronçon 21 seul ; le câble précédent = tronçons 13→18 **+ 22**. Écart d'un tronçon vs le tracé manuel fourni — **conforme à votre choix**.

## 3. Intégration backend — [main.py](CRM SIG/mon_crm_sig/app/main.py)

**a) `api_upload_shapefile` → réponse JSON** (au lieu de la redirection 303). Le seul appelant est le formulaire d'import de `map_view.html`.
Réponse : `{ success, nb_couches, cables_vides, peut_generer_cables, redirect_url }`
- `cables_vides` = une couche nommée `CABLES` existe avec 0 entité **ou** aucune couche `CABLES`.
- `peut_generer_cables` = couches `SUPPORT` **et** `BPE` présentes (BTS recommandé).

**b) Nouvel endpoint** `POST /api/projets/{id}/generer-cables` :
1. Récupère les couches `SUPPORT`, `BPE`, `BTS` du projet (recherche par nom, insensible à la casse) et lit leurs shapefiles.
2. Reconstruit le dossier livrable APD (même convention que la génération `shape` existante) :
   `04_Livrables_DOE/APD_FO_{ref}_{date}/APD_HTL_{ref}_02_{date}/SHAPE/` (helper factorisé `_chemins_livrables_apd(projet)`).
3. Appelle `gis_handler.construire_cables(...)` (schéma repris de `MODELE_SHAPE_DIR/CABLES.shp`).
4. Écrit `CABLES.shp` dans ce dossier `SHAPE`.
5. Enregistre/rafraîchit une couche **`[Livrable] CABLES`** en base (même logique que la branche `shape`) + `crm_service.enregistrer_log(...)`.
6. Renvoie `{ message, nb_cables, path }`.

**c) Interaction avec le générateur `shape` existant** : pour éviter que « Créer SHP » n'écrase les câbles générés par une couche vide, la génération recopie aussi le `CABLES.shp` produit dans `01_Inputs_SHP/` (il devient l'entrée des générations ultérieures). *(À confirmer — sinon on documente simplement l'ordre des opérations.)*

## 4. Intégration frontend — [map_view.html](CRM SIG/mon_crm_sig/app/templates/map_view.html)

- Le formulaire d'import (`#form-upload`, ligne 59) est intercepté en JS et envoyé via **`fetch` (FormData)**.
- Nouveau **modal `#modal-cables-apd`**, calqué sur le `#modal-conflit-shp` existant :
  > « La couche CABLES est vide. S'agit-il d'une étude **APD FO** ? Générer les câbles depuis SUPPORT + BPE + BTS ? » → **[Oui, générer]** / **[Non, continuer]**
- Logique JS :
  - Upload OK + `cables_vides && peut_generer_cables` → ouvrir le modal.
  - **Oui** → `POST /generer-cables` (spinner), puis redirection vers `/map/{id}`.
  - **Non** → redirection immédiate vers `/map/{id}` (flux actuel inchangé).
  - Sinon (CABLES déjà remplie) → redirection directe comme aujourd'hui.

## 5. Fichiers touchés (récapitulatif)

| Fichier | Nature de la modification |
|---|---|
| `app/gis/gis_handler.py` | **+** `construire_cables(...)` et helpers privés (graphe, snapping, découpe, fusion). Pure GIS. |
| `app/main.py` | `api_upload_shapefile` → JSON ; **+** endpoint `api_generer_cables` ; **+** helper `_chemins_livrables_apd`. |
| `app/crm/crm_service.py` | *(optionnel)* helper `obtenir_couche_par_nom(db, projet_id, nom)` + enregistrement couche livrable. |
| `app/templates/map_view.html` | Upload en `fetch` ; **+** `#modal-cables-apd` ; fonctions JS `genererCablesAuto()` / ouverture-fermeture modal. |

**Aucun changement de schéma BDD** (réutilisation de `CoucheSIG`). **Aucune nouvelle dépendance** (shapely/geopandas déjà présents).

## 6. Vérification (avant livraison)

1. **Test hors-ligne** (script geopandas) sur l'étude fournie : injecter `SUPPORT/BPE/BTS`, exécuter `construire_cables`, vérifier :
   - **4 câbles**, extrémités aux boîtes `{130, 113, 97, FM029, BTS}` (BPE+BTS strict) ;
   - géométries valides, CRS 2154, colonnes = modèle, préfixe **CAD** sur le câble touchant le BTS, **CTR** sinon, `LONGUEUR_R` cohérent.
2. `python -m py_compile` sur les fichiers modifiés.
3. **Test bout-en-bout** : lancer l'app, importer les couches (CABLES vide), vérifier l'apparition du modal, cliquer « Oui », confirmer la création du `CABLES.shp` livrable + de la couche `[Livrable] CABLES` visible sur la carte.
4. Vérifier le cas **« Non »** (flux d'import inchangé) et le cas **CABLES déjà remplie** (aucun modal).

## 7. Hypothèses / limites

- Réseau `SUPPORT` supposé connexe et cohérent (tronçons se touchant aux nœuds à ≤ 0,5 m). Un réseau troué produira des câbles interrompus (signalés en log).
- Nommage/numérotation `CTR/CAD` = schéma simple et déterministe, facilement ajustable ensuite.
- `FABRICANT`/`REFERENCE` laissés vides (non déductibles des entrées).
