# Tests — GeoCRM

## `test_global_e2e.py` — test global de bout en bout

Simule la création de projets **APD FO** et **DOE FO**, importe des couches SIG,
génère les livrables, provoque des fautes, puis **valide que les sorties
correspondent à l'attendu**. Les projets de test (`ZZ_TEST_*`) sont **créés puis
supprimés automatiquement** — aucune donnée réelle n'est touchée.

### Ce qui est vérifié (17 contrôles)

| Catégorie | Contrôles |
|---|---|
| **Nominal** | SHP livrables, Plan Synoptique (PDF), PDS (XLSX), KMZ, Dossier NETGEO, Synoptique/PDS DOE |
| **Contenu** | 1 onglet PDS par boîte BPE ; FCI + POSE (date TVX) écrits ligne par ligne ; BPE `ETAT=EN SERVICE` |
| **Schéma** | colonnes NETGEO CABLES/BPE comparées au prototype ENSIO *(optionnel)* |
| **Fautes** | Plan APD sans folios → 400 · NETGEO sans date TVX → 400 · PDS sans BPE → 400 · `.prj` manquant → toléré (Lambert-93) · SHP corrompu → pas de crash |

### Lancer

```bash
# 1) le serveur doit tourner (port 8000)
python -c "import os,sys; d=os.path.abspath('.'); sys.path.insert(0,d); import uvicorn; uvicorn.run('app.main:app', host='127.0.0.1', port=8000)"

# 2) dans un autre terminal, depuis mon_crm_sig/
python tests/test_global_e2e.py
```

### Variables d'environnement (optionnelles)

- `CRM_URL` — URL du serveur (défaut `http://localhost:8000`).
- `CRM_REF_PROTO` — dossier `03.3_Shapes` du **prototype ENSIO** de référence ;
  si absent, la comparaison de schéma est simplement sautée.

Le résultat détaillé est écrit en JSON dans le dossier temporaire système
(`crm_test_global_result.json`).
