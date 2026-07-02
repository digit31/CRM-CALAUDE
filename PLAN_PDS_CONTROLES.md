# Plan — Conserver boutons + listes déroulantes dans le PDS généré

> Décision validée : **capacité variable, 2 FO fixes**. Objectif : le PDS généré doit contenir **tout** ce qu'a votre template (boutons, listes déroulantes, couleurs, impression).

## Analyse OOXML du template (`PDS TEMPLATE A COPIER\68218_005_01_PDS.xlsx`)
- **.xlsx pur, sans VBA** (pas de `vbaProject.bin`).
- Chaque onglet-boîte = jeu de parts : `sheetN.xml` + `drawingN.xml` + `vmlDrawingN.vml` + **5 `ctrlProp` (boutons)** + `printerSettings` + rels.
- **5 boutons/onglet** (`Button 1..5`), avec macros `[0]!ThisWorkbook.CABLE_ENTRANT`, `PASSAGE`, … (macros externes ; les boutons restent visibles).
- **2 listes déroulantes/onglet** : `F3` = `OUI,NON` ; `J4:K4` = `"3M T0 MFO13280-1,3M T1 N501733A,OFDC-B8-S36-2-NN8,TENIOPEOC8144FR5"`.
- Boutons ancrés à l'en-tête (lignes 4‑6) → indépendants du nombre de cassettes.

## Cause de la perte
openpyxl **ne sait pas écrire** les contrôles de formulaire : à l'enregistrement il supprime drawings + ctrlProps + VML (boutons) et, via `copy_worksheet`, les validations `J4:K4`.

## Approche (2 couches)
1. **Contenu (openpyxl)** — inchangé/étendu : données + couleurs + cassettes (variable) + **les 2 validations** `F3` **et** `J4:K4`.
2. **Greffe OOXML des boutons** (nouveau module `pds_controls.py`) — après l'enregistrement openpyxl, on ré-injecte dans **chaque onglet-boîte** :
   - copies de `drawing`, `vmlDrawing`, 5 `ctrlProp` (lues du template) ;
   - le bloc `<drawing/><legacyDrawing/><controls>` avant `</worksheet>` ;
   - les entrées de rels + les `Override` dans `[Content_Types].xml`.
   - **IDs uniques par onglet** : `shapeId`/`spid`/`idmap` re-numérotés par onglet (base = `(100+i)*1024`) pour éviter toute collision (sinon Excel « répare » et supprime les boutons).

## Fichiers
| Fichier | Modif |
|---|---|
| `app/reporting/pds_generator.py` | + validation `J4:K4` ; appel de la greffe en fin de `generer_pds` |
| `app/reporting/pds_controls.py` | **nouveau** — `injecter_controles(xlsx, template)` (manip. zip/XML) |

## Vérification (limite honnête)
- XML bien formé (parse de chaque part), parts enregistrées, **IDs uniques**, réouverture openpyxl OK, nb de boutons/validations par onglet.
- ⚠️ Je **ne peux pas ouvrir Excel** ici : je valide la structure OOXML et (si dispo) une conversion LibreOffice headless, mais la non-« réparation » par Excel devra être confirmée par vous à l'ouverture.
