# CRM-CALAUDE

Espace de travail du projet **GeoCRM SIG** — plateforme CRM / SIG pour les études **FTTH** (fibre optique) et la production de livrables APD FO / DOE NETGEO (Free Mobile / Orange).

## 📂 Contenu du dépôt

```
CRM SIG/
├── mon_crm_sig/     ← l'application (FastAPI + GeoPandas + Leaflet)
│                      voir CRM SIG/mon_crm_sig/README.md pour l'installation et le lancement
└── EXEMPLE/         ← gabarits de référence requis par l'appli
    ├── INPUT SHAPE APD FO/         (schéma SHP modèle NETGEO)
    └── PDS TEMPLATE A COPIER/      (gabarit Excel du PDS)
```

## 🚀 Démarrage rapide

```bash
cd "CRM SIG/mon_crm_sig"
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
python run.py                                       # http://127.0.0.1:8000
```

📖 **Documentation complète de l'application** : [`CRM SIG/mon_crm_sig/README.md`](CRM%20SIG/mon_crm_sig/README.md)
📐 **Règles de nomenclature NETGEO** : [`CRM SIG/mon_crm_sig/NOMENCLATURE.md`](CRM%20SIG/mon_crm_sig/NOMENCLATURE.md)

> Les données générées à l'usage (base SQLite `crm_sig.db`, `app/static/projects_data/`, logs) sont exclues du dépôt via `.gitignore`.
