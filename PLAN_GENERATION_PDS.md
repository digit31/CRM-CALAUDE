# Plan d'exécution — Génération du livrable PDS (plan de soudure Excel)

> **Statut : EN ATTENTE DE VALIDATION.** Aucun code n'est écrit avant votre feu vert.
> Objectif : au clic sur « Générer plan PDS », créer `{reference}_PDS.xlsx` — **un onglet par boîte BPE** (nommé comme la BPE), rempli depuis les couches **BPE, CABLES, SUPPORT, BTS, PT** du projet livrable.

## 1. Analyse de référence (fichier `68218_005_01_PDS.xlsx`)

- **9 onglets** : 4 techniques (en-têtes seuls, laissés vides), **1 gabarit `PDS`** (structure vierge : libellés, 60 cassettes, styles, fusions, validation `F3=OUI/NON`), **1 onglet par BPE**.
- Le gabarit ne contient **aucune** donnée de fibre (couleurs/câbles vides) → le générateur remplit tout le corps.
- Cassette `c` : titre en ligne `7+(c-1)*15`, en-têtes `8+(c-1)*15`, 12 fibres en `9..20 +(c-1)*15`.

### Ce qui CHANGE par onglet vs ce qui est CONSTANT
- **Change** : nom d'onglet, B2 (chambre), B3 (adresse), B4 (boîte), G4 (capacité), J4 (modèle), F2 (commentaire), corps (noms câbles entrant/sortant, état, destination, grille de fibres selon la capacité).
- **Constant** : tous les libellés, la légende `N1:N4`, `G3=AVEC`, `F3=NON` (défaut), `O=SIT_FM`, le code couleur, les 60 cassettes, fusions/styles, les 4 petits onglets, la formule `G1=COUNTIF(H9:H905,"E")`.

## 2. Mapping cellule → source (en-tête, lignes 1‑4)

| Cellule | Valeur | Source / règle |
|---|---|---|
| **Nom d'onglet** | `PDB_ARC68_001_FM0xx` | `BPE.NOM` (assaini ≤ 31 car., sans `: \ / ? * [ ]`) |
| **B2** | Nom de la chambre | `PT.NOM` du **PT le plus proche** de la BPE (intersection/plus proche voisin) |
| **B3** | Adresse | `f"{BPE.ADRESSE}\n{BPE.CP} {BPE.VILLE}"` (retour à la ligne, wrap) |
| **B4** | Boîte d'épissure | `BPE.NOM` |
| **F2** | Commentaire | `"Pose d'une BPE et soudures de {N} FO"` (transit) / `"Préparation d'un câble et raccordement de {N} FO"` (boîte d'origine) |
| **F3** | Câble en passage | `"NON"` par défaut (liste OUI/NON conservée) |
| **G4** | Dimension câble entrant | `CABLES.CAPACITE` du câble **entrant** ; si vide → **modal de saisie** (nombre) |
| **H4** | Unité | `"FO"` (constant) |
| **J4** | Modèle BPE | `f"{BPE.MODELE} {BPE.REFERENCE}"` |
| **G1** | Épissures à réaliser | formule `COUNTIF` du gabarit (inchangée) |

## 3. Topologie entrant / sortant (déjà validée par la couche CABLES)

Pour chaque BPE, on récupère les **câbles incidents** (câbles CABLES dont une extrémité touche la BPE) :
- **Boîte d'origine** (1 seul câble incident, la plus éloignée du BTS) : `entrant = sortant` = ce câble.
- **Boîte de transit** (2 câbles incidents) : `sortant` = câble vers le **BTS**, `entrant` = l'autre.

Exemple vérifié : FM025 `CTR_04→CTR_04` · FM027 `CTR_04→CTR_01` · FM028 `CTR_01→CTR_03` · FM029 `CTR_03→CAD_02`. Destination `O` = `BTS.REF_PHFM` (SIT_FM…).

## 4. Algorithme du corps (plan de soudure) — `pds_generator.py`

Pour chaque BPE, après copie du gabarit `PDS` → renommé `BPE.NOM` :
```
capa = CAPACITE(entrant) ; nb_tubes = ceil(capa / 12)
COULEURS = [rouge,bleu,vert,jaune,violet,blanc,orange,gris,marron,noir,turquoise,rose]
pour t in 1..nb_tubes:            # une cassette par tube
    base = 9 + (t-1)*15
    pour p in 1..12:              # 12 fibres
        r = base + (p-1)
        B=entrant, C=t, D=COULEURS[t-1], E=1, F=p, G=COULEURS[p-1]
        si (t==1 et p<=N):        # N FO épissurées (défaut 2)
            H='E' ; I=COULEURS[p-1] ; J=p ; K=1 ; L=COULEURS[t-1] ; M=t ; N_col=sortant
            si première fibre épissurée : O=SIT_FM
        sinon: H='ST'
```
Cassettes restantes (t > nb_tubes) : laissées vides (gabarit). `G1` recalcule les épissures automatiquement.

## 5. Implémentation (fichiers)

| Fichier | Nature |
|---|---|
| `requirements.txt` | **+ `openpyxl`** (dépendance nouvelle) |
| `app/reporting/PDS_template.xlsx` | **nouveau** — le gabarit (votre fichier, onglets-boîtes retirés, gabarit `PDS` + petits onglets conservés). Chemin configurable (`MODELE_PDS`). |
| `app/reporting/pds_generator.py` | **nouveau** — `generer_pds(bpe_gdf, cables_gdf, bts_gdf, pt_gdf, support_gdf, template_path, params) -> chemin_xlsx`. Pur (openpyxl/geopandas, sans web/BDD), sur le modèle de `pdf_generator.py`. |
| `app/main.py` | **+ endpoint** `POST /api/projets/{id}/generer-pds` : lit les couches BPE/CABLES/BTS/PT/SUPPORT (livrables), appelle `generer_pds`, écrit `{ref}_PDS.xlsx` dans `04_Livrables_DOE/.../` + enregistre un log. Reçoit les capacités saisies (modal) en paramètre. |
| `app/templates/etudes.html` | Le bouton **PDS** existant (`genererEtude('pds')`) appelle la vraie génération ; **+ modal capacité** (si un câble n'a pas de `CAPACITE`, demander le nombre) et **+ paramètre N FO**. |
| *(option)* `map_view.html` | Bouton « Générer PDS » côté carte. |

**Aucun changement de schéma BDD.** La génération lit les **couches livrables déjà générées** (BPE/CABLES issus des étapes précédentes).

## 6. Vérification (avant livraison)

1. **Test hors-ligne** sur votre étude : générer le PDS depuis SUPPORT/BPE/BTS/PT + CABLES (4 câbles), comparer aux onglets de référence :
   - 4 onglets nommés `PDB_ARC68_001_FM0xx` ;
   - B3/B4/G4/J4 conformes ; entrant/sortant conformes au tableau §3 ; `O=SIT_FM` ; 2 FO en `E`, reste `ST` ; couleurs correctes.
2. Ouverture du `.xlsx` généré (openpyxl round-trip) + styles/fusions préservés (copie de gabarit).
3. `py_compile` + test endpoint bout-en-bout sur projet jetable (comme pour les câbles).

## 7. Points à valider (voir mes questions)
- Portée du corps (formulaire seul vs plan de soudure complet).
- Origine du **nombre de FO à épissurer** (défaut 2 ?).
- Confirmation des règles Modèle BPE / Commentaire.
- Déclenchement + modal capacité.

## 8. Hypothèses / limites
- « Nom de la chambre » = PT le plus proche (votre règle) ; la référence FM025/FM028 semble contenir des saisies manuelles tronquées (FM029 = `FT1-68218-FT19` valide l'intersection).
- Modèle BPE FM025 (`TENIOPEOC8144FR5`) = exception manuelle vs règle `MODELE + REFERENCE`.
- Cellules `Date de raccordement` / `Configuration Boîtier` laissées vides (non présentes dans les données).
