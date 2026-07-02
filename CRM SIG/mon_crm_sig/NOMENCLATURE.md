# NOMENCLATURE — Proposition automatique de valeurs (NETGEO)

> **But** : à chaque **ajout d'un projet** (import de couches), lorsqu'un **SUPPORT**, un **PT** ou un **CÂBLE** a un **NOM / CODE vide** ou des **champs vides**, l'application **PROPOSE** une valeur conforme à la nomenclature client (Free Mobile / Orange NETGEO). L'utilisateur valide ou corrige — rien n'est écrit sans validation.
>
> Source : *listes de valeurs NETGEO v16 (2024‑04)* — « LISTE DES CHAMPS ATTRIBUTAIRES POUR EXPORT SHAPEFILE ».
> Ce fichier est une **référence de compréhension** ; il ne modifie pas le code.

---

## 0. Règles GLOBALES (toujours appliquées à toute proposition)

- **MAJUSCULES** uniquement, **sans accent**, **sans apostrophe**.
- **Pas de doublon** de nommage (NOM/CODE uniques dans la couche et le projet).
- **Aucun espace ni retour‑chariot** en début / fin de champ (trim systématique).
- **Dates** au format `aaaammjj` (ex. `20260702`).
- **CP** (code postal) = **exactement 5 caractères**.
- **ETAT** ∈ { `EN ETUDE`, `EN SERVICE` } → par défaut sur un nouveau projet : **`EN ETUDE`**.
- **EMPRISE** = `NRAXX_001` (se termine par `001`, cohérente avec le NRA du projet).
- **PROPRIETAIRE / GESTIONNAIRE** :
  - ouvrages **FREE** → `FREE MOBILE` (jamais « FREE INFRA / FREE INFRASTRUCTURE ») ;
  - sinon `FT`, `PRIVE`, ou **OP TIERS** (code opérateur, cf. onglet *liste CODE OP13*).
- **Champ non déductible** → laissé **vide + signalé** (à saisir par l'humain), jamais inventé.

`NRAXX` = identifiant NRA du projet (ex. `NRA68`, ou l'EMPRISE `EUR68_001` selon le dossier).

---

## 1. CÂBLE (couche `CABLES`)

**Champs** : `NOM, CODE, EMPRISE, PROPRIETAI, GESTIONNAI, POSE, ETAT, FABRICANT, REFERENCE, LONGUEUR_R, NB_TUBE, CAPACITE` (+ `SYMBOLISAT`, `FCI`).

### Nommage `NOM` / `CODE` (identiques)
Format : **`CXX_NRAXX_XXX_XX_XX`** où `CXX` = **type de câble** :

| Préfixe | Type de câble |
|---|---|
| `CTR` | Câble de transport |
| `CDI` | Câble de distribution |
| `CAD` | Câble d'adduction (vers le site) |
| `CDD` | Câble de dérivation/desserte |
| `CBM` | Câble … (BM) |
| `FON` | Fibre optique noire (FON) |

**Proposition si NOM vide** : `TYPE_NRAXX_001_ZZ_01` avec **`ZZ` = incrément** du dernier câble de ce type rattaché au NRA (ex. `CTR_NRA68_001_04`). Le type est déduit de la position du câble (adduction touchant le BTS → `CAD`, transport → `CTR`).

### Autres champs — proposition si vide
| Champ | Proposition |
|---|---|
| `EMPRISE` | `NRAXX_001` (celle du projet) |
| `PROPRIETAI` / `GESTIONNAI` | `FREE MOBILE` |
| `ETAT` | `EN ETUDE` |
| `POSE` | date du jour `aaaammjj` (réf. date d'édition de la commande) |
| `LONGUEUR_R` | longueur réelle **numérique** (géométrie arrondie) |
| `NB_TUBE` | ∈ {1, 2, 3, 4} — déduit de la capacité (voir référentiel) |
| `CAPACITE` | valeur du **référentiel** ({12, 24, 36, 48, 72, 144, 288} FO) |
| `FABRICANT` / `REFERENCE` | couple **cohérent** du référentiel (§4) |
| `SYMBOLISAT` | même valeur que le type (CBM/BAG/FON/CDD/CTR/CDI/CAD) |

⚠️ **Cohérence obligatoire** : `REFERENCE` ↔ `CAPACITE` ↔ `NB_TUBE` ↔ `FABRICANT` doivent former un tuple valide du référentiel (§4). Si l'un est renseigné, on **propose les autres** en conséquence.

---

## 2. SUPPORT (couche `SUPPORT`)

**Champs** : `LIBELLE, EMPRISE, TYPE_STRUC, PROPRIETAI, GESTIONNAI, CODE, COMPOSITIO, ORDRE, LGR_REEL` (+ VOIE, COMMUNE, P_VOIRIE… pour le GC créé).

### Nommage `LIBELLE`
Format selon la nature (déduite de `TYPE_STRUC`) :

| Préfixe | Cas |
|---|---|
| `GEC_NRAXX_XXX_XXXX` | câble en **conduite souterraine / tranchée** |
| `AER_NRAXX_XXX_XXXX` | câble en **aérien** |
| `FAC_NRAXX_XXX_XXXX` | câble en **façade** |

**Proposition si LIBELLE vide** : `PREFIXE_NRAXX_001_NNNN` avec `PREFIXE` déduit de `TYPE_STRUC` (`TRANCHEE`→`GEC`, `AERIEN`→`AER`, `FACADE`→`FAC`) et `NNNN` = incrément (peut suivre le champ `ORDRE`).

### Autres champs — proposition si vide
| Champ | Proposition |
|---|---|
| `TYPE_STRUC` | ∈ { `TRANCHEE`, `AERIEN`, `FACADE`, `FORAGE` } — déduit du contexte (aérien vs souterrain) |
| `EMPRISE` | `NRAXX_001` |
| `PROPRIETAI` / `GESTIONNAI` | `FT` / `FREE MOBILE` / `PRIVE` / OP TIERS |
| `LGR_REEL` | longueur réelle numérique (géométrie) |
| `ORDRE` | rang le long du cheminement (séquence) |
| `CODE` | reprise du `LIBELLE` si vide |

> Les supports doivent **se superposer** aux câbles (copier‑coller en point de base) ; EMPRISE se termine par `001`.

---

## 3. PT — POINTS TECHNIQUES (couche `PT` : chambres & poteaux)

**Champs** : `NOM, CODE, ADRESSE, CODE_POSTA, VILLE, TYPE_FONC, TYPE_STRUC, ETAT, MODELE, PROPRIETAI, GESTIONNAI, EMPRISE, DATE_CREAT` (+ CODE_CH1/CH2, REF_CHAMBR).

### Nommage `NOM` / `CODE` selon le propriétaire et la nature
**CHAMBRES**
| Propriétaire | Format | Note |
|---|---|---|
| FREE MOBILE | `PCH_NRA00_000_FM0000` | 6 digits |
| FT | `FT_INSEE_CODE1` | si **FT1**, ajouter le n° de voirie (ex. *14 AV SISLEY* → `FT_INSEE_FT114`) |
| PRIVE | `PRV_INSEE_00` | |
| OP TIERS | `CODEOPTIER_INSEE_NUM` | code opérateur (ENEDIS→EDF, AXIONE→AXIONE…) |
| Autre PT | `PXX_NRA00_000_FM0000` | `XX` = EG (égout/ovoïde), TS (technique spécifique), FA (façade), AS (aéro‑souterrain), BP (branchement particulier), BR (branchement de regard) |

**POTEAUX**
| Propriétaire | Format |
|---|---|
| FREE MOBILE | `PAR_NRA00_000_FM0000` (6 digits) |
| FT | `FT_INSEE_000000` (6 digits ; si FT1 → n° voirie) |
| PRIVE | `PRV_INSEE_000000` |
| OP TIERS | `CODEOPTIER_INSEE_NUM` |

### Autres champs — proposition si vide
| Champ | Proposition |
|---|---|
| `TYPE_FONC` | ∈ { `PASSAGE`, `DERIVATION` } (défaut `PASSAGE`) |
| `TYPE_STRUC` | `CHAMBRE` \| `POTEAU` \| `POTELET` (déduit du modèle/contexte) |
| `MODELE` | chambre : `OHN`, `K2C`, `L0T`… ; poteau : `BOIS`, `METAL`, `COMPOSITE`, `BETON` |
| `ETAT` | `EN SERVICE` (existant) sinon `EN ETUDE` |
| `EMPRISE` | `NRAXX_001` |
| `DATE_CREAT` | `aaaammjj` — **obligatoire** pour les ouvrages FREE MOBILE et chambres tiers |
| `CP` (`CODE_POSTA`) | 5 caractères ; `VILLE`/`ADRESSE` repris du contexte |

---

## 4. RÉFÉRENTIELS (pour proposer des couples cohérents)

### Capacité × Fabricant × Référence (extrait — CÂBLES)
| Capacité | Fabricant | Référence |
|---|---|---|
| 288 FO | DRAKA | 60017042 |
| 144 FO | DRAKA | 60017038 |
| 72 FO | DRAKA | 60017036 |
| 72 FO | FIBRAIN | MDC-FM-072-EM-OXC6CB |
| 48 FO (SOUT) | DRAKA | 60017035 |
| 48 FO (AERIEN) | DRAKA | 60085492 |
| 48 FO | DRAKA | 60099755 |
| 48 FO | FIBRAIN | MDC-FM-48F-4M12F |
| 36 FO | ACOME | N7936A |
| 36 FO (CDD) | DRAKA | 60022931 |
| 24 FO | DRAKA | 60017034 / 60036682 |
| 12 FO | ACOME | N9448C |

> `NB_TUBE` se déduit de la capacité (12 FO/tube) : 48 FO → 4 tubes, 24 FO → 2 tubes, etc.

### Modèle × Référence (BPE — rappel)
`3MT0` → `MFO13280` / `MFO13280-1` · `3MT1` → `N501733A` · `OFDC` → `OFDC-B8-S36-2-NN8` · `TENIO` → `TENIOPEOC8144FR5` · `EDP` → `EDP-BPEO1-144FO` · `ACEMicro` → `563135` · `BLACKBOX` → `10261317` · `FOLAN` → `FIDJI`.

### OP TIERS (codes opérateurs — onglet *liste CODE OP13*)
Table `Opérateur → Code Interop` (ADTIM→`ADTI`, AXIONE, ENEDIS→`EDF`…) — utilisée pour PROPRIETAIRE/GESTIONNAIRE et le nommage des PT tiers.

---

## 5. Ce qui N'EST PAS auto‑proposé (validation humaine requise)

- Le **numéro INSEE** / n° de voirie exact d'une chambre FT (dépend du terrain).
- La **cohérence NOM ↔ MODELE ↔ REFERENCE** d'une BPE quand plusieurs modèles sont possibles.
- Le **choix du type de câble** (CTR/CDI/CAD…) en cas d'ambiguïté topologique.
- Toute **référence FCI / permission de voirie / domanialité** propre au dossier.

Dans ces cas : le champ est **laissé vide et signalé**, avec la (les) valeur(s) candidate(s) proposée(s) à titre indicatif.

---

## 6. Motifs de rejet à éviter (rappel — onglet *Exemple REFUS*)

Fichier manquant / arborescence · nomenclature non respectée · doublon · espace ou retour‑chariot en début/fin · CP ≠ 5 caractères · champ/fichier vide · valeur hors liste (ETAT, TYPE_FONCT, TYPE_STRUC…) · date non conforme · incohérence référence/capacité/nb_tube/fabricant · EMPRISE ne finissant pas par `001` · PROPRIETAIRE ≠ `FREE MOBILE`.

> **Export DOE NETGEO** attendu en **Lambert 2 étendu (EPSG:27572)** — distinct de l'APD en Lambert‑93 (EPSG:2154).
