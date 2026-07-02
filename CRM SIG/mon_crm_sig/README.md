# GeoCRM SIG — Plateforme CRM / SIG FTTH

Application web locale de **gestion de projets FTTH** (fibre optique) et de **production de livrables** pour les études APD FO / DOE NETGEO (Free Mobile / Orange).
Elle réunit dans une seule interface un **CRM** (clients, affaires, statuts), un **SIG cartographique** (import Shapefile, carte Leaflet, table attributaire éditable) et un **moteur de livrables** (SHP livrables, PDS Excel, KMZ, fiche PDF), avec proposition automatique de **nomenclature** conforme au client.

> Interface en français. Application **mono‑utilisateur locale** (FastAPI + SQLite), pensée pour tourner sur le poste du chargé d'études.

---

## ✨ Fonctionnalités

- **CRM** — clients, projets/affaires (`AFF‑2026‑XXXX`), statuts, journal des générations, création automatique de l'arborescence de dossiers du projet.
- **SIG / Carte interactive** — import de couches Shapefile (drag & drop), affichage Leaflet reprojeté en WGS84, panneau de couches, table attributaire.
- **Édition Livrables** — table éditable des couches `[Livrable]` : modification d'attributs **et suppression d'entités** (ligne complète du SHP), écrites directement dans le SHP livrable.
- **Génération automatique des câbles** (étude APD FO) — si la couche `CABLES` est vide, création d'un câble entre chaque paire de boîtes consécutives (BPE/BTS) en fusionnant les supports par `ORDRE`.
- **Livrables** :
  - **SHP livrables** — alignés sur le schéma modèle NETGEO (`04_Livrables_DOE/.../SHAPE`).
  - **PDS** (plan de soudure) — Excel, un onglet par boîte BPE, à partir d'un **gabarit client** copié sans altération (couleurs, listes déroulantes, boutons préservés).
  - **KMZ** — cartographie exportée depuis les SHP livrables.
  - **PDF** — fiche de synthèse du projet.
- **Nomenclature NETGEO** — à l'import, proposition de valeurs conformes (NOM/CODE/champs vides) pour `SUPPORT` / `PT` / `CABLES`, éditables et validables (cf. [`NOMENCLATURE.md`](NOMENCLATURE.md)).

> 🔒 **Source de vérité** : le **SHP livrable** (`04_Livrables_DOE/APD_FO_*/APD_HTL_*/SHAPE`) est la référence unique. La carte, la table d'édition, le PDS et le KMZ lisent tous ce même fichier ; les corrections de nomenclature et les suppressions y sont répercutées.

---

## 🧱 Pile technique

| Domaine | Technologies |
|---|---|
| Backend | Python, **FastAPI**, Uvicorn |
| Base de données | **SQLite** via **SQLAlchemy** (ORM) |
| SIG | **GeoPandas**, Shapely, Fiona, pyproj |
| Rendu / templates | **Jinja2**, **Leaflet**, Tailwind (CDN) |
| Reporting | **openpyxl** (Excel/PDS), **fpdf2** (PDF) |

CRS : **Lambert‑93 (EPSG:2154)** pour l'APD FO ; export DOE NETGEO attendu en **Lambert 2 étendu (EPSG:27572)**.

---

## 📁 Structure du projet

```
mon_crm_sig/
├── run.py                     # Point d'entrée (uvicorn app.main:app)
├── requirements.txt
├── NOMENCLATURE.md            # Règles de nomenclature NETGEO
├── crm_sig.db                 # Base SQLite (générée — ignorée par git)
└── app/
    ├── main.py                # Routes FastAPI (orchestration)
    ├── database.py            # Config SQLAlchemy / SQLite
    ├── models.py              # Modèles ORM (Client, Projet, CoucheSIG, Log, Utilisateur)
    ├── crm/crm_service.py     # Logique CRM (projets, dossiers, logs)
    ├── gis/
    │   ├── gis_handler.py     # Lecture/écriture SHP, câbles, livrables, KMZ
    │   ├── nomenclature.py    # Moteur de propositions NETGEO
    │   └── exporter.py
    ├── reporting/
    │   ├── pds_generator.py   # Génération du PDS (Excel)
    │   ├── pds_controls.py    # Injection OOXML (boutons/listes du gabarit)
    │   └── pdf_generator.py   # Fiche de synthèse PDF
    ├── templates/             # dashboard, map_view, etudes, clients, access…
    └── static/
        ├── css/  js/
        └── projects_data/     # Données par projet (générées — ignorées par git)
```

> ⚠️ **Dépendance externe** : les gabarits de référence (SHP modèle + gabarit PDS) sont dans `../EXEMPLE/` (dossier `CRM SIG/EXEMPLE`, voisin de `mon_crm_sig`). Voir [Configuration](#-configuration).

---

## 🚀 Installation

Prérequis : **Python 3.10+**. GeoPandas/Fiona/pyproj embarquent des binaires géospatiaux (GDAL) — sous Windows, `pip` les installe via des wheels.

```bash
# depuis mon_crm_sig/
python -m venv .venv
# Windows :  .venv\Scripts\activate
# Linux/mac : source .venv/bin/activate

pip install -r requirements.txt
```

## ▶️ Lancement

```bash
python run.py
# ou :  uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Puis ouvrir **http://127.0.0.1:8000**. La base SQLite (`crm_sig.db`) et les tables sont créées automatiquement au premier démarrage.

---

## 🔄 Flux de travail type

1. **Créer un projet** (tableau de bord) → l'arborescence de dossiers est générée.
2. **Carte interactive** → glisser les Shapefiles (`.shp` + `.dbf`/`.shx`/`.prj`).
3. Si des champs `NOM/CODE` sont vides → **modal Nomenclature** : cocher/éditer les propositions, **Appliquer**.
4. Si `CABLES` vide (APD FO) → proposition de **génération automatique des câbles**.
5. **Créer / Régénérer SHP Livrables** → produit les SHP livrables (`04_Livrables_DOE/.../SHAPE`).
6. **Édition Livrables** → ajuster les attributs, **supprimer les entités** en trop.
7. **Études** → générer le **PDS**, le **KMZ**, la fiche **PDF**.

---

## 🗺️ API (principaux points d'entrée)

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/` | Tableau de bord CRM |
| `GET` | `/map/{projet_id}` | Vue cartographique |
| `GET` | `/projets/{projet_id}/etudes` | Écran Études (livrables) |
| `POST` | `/api/projets` | Créer un projet |
| `POST` | `/api/projets/{id}/upload-shp` | Importer un Shapefile |
| `POST` | `/api/projets/{id}/generer-cables` | Générer les câbles automatiquement |
| `POST` | `/api/projets/{id}/generer-pds` | Générer le PDS (Excel) |
| `POST` | `/api/projets/{id}/generer-etude/{type}` | Générer `shape` / `kmz` / … |
| `POST` | `/api/projets/{id}/pdf` | Fiche de synthèse PDF |
| `GET` | `/api/projets/{id}/couches/{cid}/geojson` | GeoJSON (carte) |
| `GET` | `/api/projets/{id}/couches/{cid}/attributs` | Table attributaire |
| `GET/POST` | `.../{cid}/propositions` · `.../appliquer-propositions` | Nomenclature |
| `POST` | `.../{cid}/sauvegarder-attributs` | Sauver les attributs du livrable |
| `DELETE` | `.../{cid}/entites/{ligne}` | **Supprimer une entité** du SHP livrable |

---

## ⚙️ Configuration

Variables d'environnement (optionnelles) :

| Variable | Défaut | Rôle |
|---|---|---|
| `CRM_SIG_MODELE_SHAPE` | `../EXEMPLE/INPUT SHAPE APD FO` | Dossier des SHP modèles NETGEO (schéma des livrables) |
| `CRM_SIG_PDS_TEMPLATE` | `../EXEMPLE/PDS TEMPLATE A COPIER/68218_005_01_PDS.xlsx`, sinon `app/reporting/PDS_template.xlsx` | Gabarit Excel du PDS |

Si le dossier `EXEMPLE` n'est pas déployé à côté du projet, définissez `CRM_SIG_MODELE_SHAPE` (indispensable à la génération des SHP livrables). Le PDS dispose d'un gabarit **embarqué** de secours (`app/reporting/PDS_template.xlsx`).

---

## 🗃️ Modèle de données

- **Client** — donneur d'ordres (+ `nomenclature` JSON par client).
- **Projet** — affaire (`reference`, `statut`, `chemin_dossier`), rattachée à un client.
- **CoucheSIG** — un `.shp` d'un projet (type géométrie, EPSG, nb entités, couleur).
- **LogGeneration** — journal des livrables générés.
- **Utilisateur** — accès (Admin / Editeur / Lecteur).

---

## 📝 Notes

- Application **locale mono‑utilisateur** ; SQLite sans authentification stricte.
- `crm_sig.db`, `app/static/projects_data/` et les `*.log` sont **générés à l'usage** et exclus du dépôt (voir `.gitignore`).
- Documentation métier complémentaire : [`NOMENCLATURE.md`](NOMENCLATURE.md).
