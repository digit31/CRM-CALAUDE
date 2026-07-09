"""
main.py - Point d'entrée de l'application web locale.

Ce fichier orchestre les routes (URLs) de l'application.
Il connecte l'interface web (les templates HTML) avec les modules métier
(CRM, SIG, Reporting) sans contenir lui-même de logique métier.

Routes principales :
  GET  /                -> Tableau de bord CRM (liste des projets)
  POST /api/projets     -> Créer un nouveau projet
  GET  /map/{id}        -> Vue cartographique d'un projet
  POST /api/projets/{id}/upload-shp  -> Importer un Shapefile
  GET  /api/projets/{id}/couches/{couche_id}/geojson -> GeoJSON pour Leaflet
  POST /api/projets/{id}/pdf         -> Générer la fiche de synthèse PDF
  POST /api/projets/{id}/statut      -> Mettre à jour le statut
  DELETE /api/projets/{id}           -> Supprimer un projet
"""

import os
import shutil
import logging
from fastapi import FastAPI, Request, Depends, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.database import engine, Base, get_db, PROJECTS_DATA_DIR
from app import models
from app.crm import crm_service
from app.gis import gis_handler
from app.reporting import pdf_generator

logger = logging.getLogger("crm_sig.main")

# Création des tables dans la base de données au démarrage
Base.metadata.create_all(bind=engine)


def _migrer_schema_sqlite():
    """create_all() CRÉE les tables manquantes mais n'ALTER jamais une table
    existante. Sur une base antérieure à l'ajout d'une colonne au modèle (ex.
    Client.nomenclature), la requête planterait (« no such column »). On ajoute
    donc les colonnes manquantes au démarrage (ADD COLUMN, sans contrainte)."""
    from sqlalchemy import inspect as _inspect, text as _text
    try:
        insp = _inspect(engine)
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existantes = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existantes:
                    continue
                try:
                    coltype = col.type.compile(dialect=engine.dialect)
                    with engine.begin() as conn:
                        conn.execute(_text(
                            f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}'))
                    logger.info(f"Migration schéma : colonne {table.name}.{col.name} ajoutée.")
                except Exception as e:
                    logger.warning(f"Migration {table.name}.{col.name} impossible : {e}")
    except Exception as e:
        logger.warning(f"Migration de schéma ignorée : {e}")


_migrer_schema_sqlite()

app = FastAPI(title="GeoCRM SIG Local", version="1.0.0")

# --- Configuration des fichiers statiques et templates ---
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

# Dossier "modèle" contenant la structure SHAPE de référence (EXEMPLE/INPUT SHAPE APD FO).
# Il se trouve dans le dossier de travail "CRM SIG", au même niveau que "mon_crm_sig".
# Le chemin est calculé relativement à ce fichier pour rester portable (plus de chemin
# codé en dur vers un Bureau précis) ; il peut être surchargé via la variable
# d'environnement CRM_SIG_MODELE_SHAPE.
_WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELE_SHAPE_DIR = os.environ.get(
    "CRM_SIG_MODELE_SHAPE",
    os.path.join(_WORKSPACE_DIR, "EXEMPLE", "INPUT SHAPE APD FO"),
)

# Gabarit des 7 couches NETGEO du DOE FO (01-BPE … 07-NRO, .prj inclus) :
# copié dans DOE_NETGEO puis rempli depuis les SHP d'entrée.
DOE_FO_TEMPLATE_DIR = os.environ.get(
    "CRM_SIG_DOE_FO_TEMPLATE",
    os.path.join(_WORKSPACE_DIR, "EXEMPLE", "COUCHE TEMPLATE DOE FO"),
)

# Gabarit Excel du livrable PDS : on utilise EN PRIORITÉ le fichier de référence
# fourni par l'utilisateur (EXEMPLE/PDS TEMPLATE A COPIER), sinon la copie embarquée.
_PDS_EXEMPLE = os.path.join(_WORKSPACE_DIR, "EXEMPLE", "PDS TEMPLATE A COPIER", "68218_005_01_PDS.xlsx")
_PDS_EMBED = os.path.join(os.path.dirname(__file__), "reporting", "PDS_template.xlsx")
PDS_TEMPLATE = os.environ.get(
    "CRM_SIG_PDS_TEMPLATE",
    _PDS_EXEMPLE if os.path.exists(_PDS_EXEMPLE) else _PDS_EMBED,
)

# Gabarit PowerPoint du livrable APD (plan global) : copié puis rempli par projet.
APD_PPTX_TEMPLATE = os.environ.get(
    "CRM_SIG_APD_PPTX",
    os.path.join(_WORKSPACE_DIR, "EXEMPLE", "TEMPLATE APD PLAN GLOBAL", "APD_HTL_NOM PROJET.pptx"),
)

os.makedirs(os.path.join(STATIC_DIR, "css"), exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "js"), exist_ok=True)
os.makedirs(PROJECTS_DATA_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

logger.info("Application GeoCRM SIG demarree avec succes.")


# =====================================================================
# ROUTES D'INTERFACE (Pages HTML)
# =====================================================================

@app.get("/")
def page_dashboard(request: Request, db: Session = Depends(get_db)):
    """Page d'accueil : Tableau de bord CRM avec la liste des projets."""
    projets = crm_service.lister_projets(db)
    clients = crm_service.lister_clients(db)
    # Type d'étude (APD FO / DOE FO) par projet, pour l'afficher dans le tableau.
    types_etude = {p.id: _type_etude_projet(p) for p in projets}
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "projets": projets,
        "clients": clients,
        "types_etude": types_etude,
    })


@app.get("/map/{projet_id}")
def page_carte(request: Request, projet_id: int, db: Session = Depends(get_db)):
    """Page cartographique : Affichage des couches SIG d'un projet."""
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    
    shp_genere = any(c.nom.startswith("[Livrable]") for c in projet.couches)
    # Affichage carte : couches INPUT tant qu'aucun livrable n'existe ; dès que le
    # SHP livrable d'une couche est généré, on affiche la version « Édition
    # Livrables » (source de vérité) à sa place — supprime le doublon
    # input/livrable. Une couche input SANS jumeau livrable reste affichée
    # (aucune donnée perdue).
    bases_liv = _bases_livrables(projet)

    def _affichee(c):
        if (c.nom or "").strip().lower().startswith("[livrable]"):
            return True  # les livrables (source de vérité) sont toujours affichés
        base = (c.nom or "").upper().replace("[LIVRABLE]", "").strip()
        return base not in bases_liv  # input affiché seulement sans jumeau livrable

    couches_affichees = [c for c in projet.couches if _affichee(c)]
    # Quelles couches portent les étiquettes (une seule par base : livrable si
    # présent, sinon input) — pour n'afficher le bouton toggle que là où il agit.
    couches_etiquettes = {
        c.id: _couche_porte_etiquettes(projet, c, bases_liv) for c in couches_affichees
    }
    return templates.TemplateResponse("map_view.html", {
        "request": request,
        "projet": projet,
        "shp_genere": shp_genere,
        "couches_affichees": couches_affichees,
        "couches_etiquettes": couches_etiquettes,
        "fond_opacite": _fond_opacite_projet(projet),
        "type_etude": _type_etude_projet(projet),
    })


@app.get("/projets/{projet_id}/folios", response_class=HTMLResponse)
def page_folios(request: Request, projet_id: int, db: Session = Depends(get_db)):
    """Éditeur de folios : dessiner à la main ou auto-générer la grille des plans."""
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    bases_liv = _bases_livrables(projet)

    def _affichee(c):
        if (c.nom or "").strip().lower().startswith("[livrable]"):
            return True
        base = (c.nom or "").upper().replace("[LIVRABLE]", "").strip()
        return base not in bases_liv

    couches_affichees = [c for c in projet.couches if _affichee(c)]
    return templates.TemplateResponse("folios.html", {
        "request": request,
        "projet": projet,
        "couches_affichees": couches_affichees,
    })


@app.get("/projets/{projet_id}/etudes", response_class=HTMLResponse)
def page_etudes(request: Request, projet_id: int, db: Session = Depends(get_db)):
    """Page listant les études et livrables générables pour ce projet."""
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
        
    shp_genere = any(c.nom.startswith("[Livrable]") for c in projet.couches)
    
    # Rechercher si un dossier de livrables a déjà été généré localement
    base_doe_dir = os.path.join(projet.chemin_dossier, "04_Livrables_DOE")
    dossier_existant = None
    if os.path.exists(base_doe_dir):
        dirs = [d for d in os.listdir(base_doe_dir) if os.path.isdir(os.path.join(base_doe_dir, d))]
        if dirs:
            # Récupérer le dossier modifié le plus récemment
            dirs.sort(key=lambda x: os.path.getmtime(os.path.join(base_doe_dir, x)), reverse=True)
            dossier_existant = os.path.join(base_doe_dir, dirs[0])
    
    return templates.TemplateResponse("etudes.html", {
        "request": request,
        "projet": projet,
        "shp_genere": shp_genere,
        "dossier_existant": dossier_existant,
        "shapes_a_maj": _shapes_a_maj(projet),
        "type_etude": _type_etude_projet(projet),
    })


# =====================================================================
# CONSOLE ÉTUDE : saisie des caractéristiques de la liaison (rapport APD)
# =====================================================================

def _chemin_console_json(projet) -> str:
    """Fichier de persistance des données saisies dans la console (par projet)."""
    dossier = os.path.join(projet.chemin_dossier, "02_Traitement")
    os.makedirs(dossier, exist_ok=True)
    return os.path.join(dossier, "console_etude.json")


# --- Drapeau « Shapes à mettre à jour » -----------------------------------
# Posé dès qu'une modification est enregistrée dans « Édition Livrables »
# (attributs / suppression d'entité). Tant qu'il est présent, la page Études
# affiche « MAJ Shapes » en avant et désactive les autres générations ; il est
# effacé quand la Cartographie SHP est (re)générée.
def _chemin_flag_maj(projet) -> str:
    dossier = os.path.join(projet.chemin_dossier, "02_Traitement")
    os.makedirs(dossier, exist_ok=True)
    return os.path.join(dossier, "shapes_a_maj.flag")


def _marquer_shapes_a_maj(projet):
    try:
        with open(_chemin_flag_maj(projet), "w", encoding="utf-8") as f:
            f.write("1")
    except Exception as e:
        logger.warning(f"Drapeau MAJ shapes non posé (projet {projet.id}) : {e}")


def _effacer_shapes_a_maj(projet):
    try:
        p = _chemin_flag_maj(projet)
        if os.path.exists(p):
            os.remove(p)
    except Exception as e:
        logger.warning(f"Drapeau MAJ shapes non effacé (projet {projet.id}) : {e}")


def _shapes_a_maj(projet) -> bool:
    return os.path.exists(_chemin_flag_maj(projet))


@app.get("/projets/{projet_id}/console", response_class=HTMLResponse)
def page_console(request: Request, projet_id: int, db: Session = Depends(get_db)):
    """Console Étude : écran de saisie des données du rapport (par projet)."""
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    return templates.TemplateResponse("console.html", {
        "request": request,
        "projet": projet,
        "type_etude": _type_etude_projet(projet),
    })


@app.get("/api/projets/{projet_id}/console/donnees")
def api_console_donnees(projet_id: int, recalc: int = 0, db: Session = Depends(get_db)):
    """
    Renvoie les données de la console : défauts auto-calculés depuis les SHP
    livrables, fusionnés avec la dernière saisie (sauf ?recalc=1 = valeurs SHP
    fraîches en écrasant les champs numériques déductibles).
    """
    from datetime import datetime
    from app.reporting import apd_generator
    import json as _json

    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")

    dossier_shape = _trouver_dossier_shape(projet)
    date_str = datetime.utcnow().strftime("%d/%m/%Y")
    ref = projet.reference or f"AFF_{projet.id}"
    try:
        natures = _natures_appuis_projet(projet, dossier_shape)
        defauts = apd_generator.calculer_synthese(dossier_shape, ref_projet=ref,
                                                  date_str=date_str, natures_appuis=natures)
    except Exception as e:
        logger.warning(f"Console : calcul synthèse impossible ({e})")
        defauts = {"cartouche": {"code_projet": ref.replace("-", "_"), "date_real": date_str,
                                 "version": "V1", "type_etude": "APD HTL"},
                   "souterrain": {}, "aerien": {}, "appuis": {}, "boites": {}, "infos": ""}

    sauvegarde = {}
    p = _chemin_console_json(projet)
    if os.path.exists(p) and not recalc:
        try:
            with open(p, "r", encoding="utf-8") as f:
                sauvegarde = _json.load(f)
        except Exception as e:
            logger.warning(f"Console : lecture sauvegarde impossible ({e})")

    # Valeurs AUTO toujours recalculées depuis le SHP livrable (reflète l'état
    # courant même après modification), saisies manuelles préservées.
    donnees = apd_generator.fusionner_console(defauts, sauvegarde)
    return JSONResponse({"donnees": donnees, "recalcule": bool(recalc),
                         "a_sauvegarde": bool(sauvegarde)})


@app.post("/api/projets/{projet_id}/console")
async def api_console_sauver(projet_id: int, request: Request, db: Session = Depends(get_db)):
    """Sauvegarde les données saisies dans la console (JSON par projet)."""
    import json as _json
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    try:
        donnees = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Corps JSON invalide.")
    try:
        with open(_chemin_console_json(projet), "w", encoding="utf-8") as f:
            _json.dump(donnees, f, ensure_ascii=False, indent=2)
        return JSONResponse({"message": "Données de la console enregistrées."})
    except Exception as e:
        logger.error(f"Console : sauvegarde impossible projet {projet_id} ({e})")
        raise HTTPException(status_code=500, detail=str(e))


def _dossier_apd_assets(projet):
    d = os.path.join(projet.chemin_dossier, "02_Traitement", "apd_assets")
    os.makedirs(d, exist_ok=True)
    return d


@app.post("/api/projets/{projet_id}/apd-assets/plan_masse")
async def api_apd_upload_plan_masse(projet_id: int, fichier: UploadFile = File(...),
                                    db: Session = Depends(get_db)):
    """Upload (drag&drop) du plan de masse BTS (image PNG/JPG ou PDF -> PNG),
    intégré en slide 3 du livrable APD."""
    import glob as _glob
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    dossier = _dossier_apd_assets(projet)
    for f in _glob.glob(os.path.join(dossier, "plan_masse.*")):
        try:
            os.remove(f)
        except Exception:
            pass
    ext = os.path.splitext(fichier.filename or "")[1].lower()
    data = await fichier.read()
    try:
        if ext == ".pdf":
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            cible = os.path.join(dossier, "plan_masse.png")
            doc.load_page(0).get_pixmap(dpi=200).save(cible)
        elif ext in (".png", ".jpg", ".jpeg"):
            cible = os.path.join(dossier, "plan_masse" + ext)
            with open(cible, "wb") as f:
                f.write(data)
        else:
            raise HTTPException(status_code=400,
                                detail="Format non supporté (PNG, JPG ou PDF).")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"APD plan de masse upload projet {projet_id} : {e}")
        raise HTTPException(status_code=500, detail=str(e))
    rel = os.path.relpath(cible, STATIC_DIR).replace("\\", "/")
    url = "/static/" + rel if not rel.startswith("..") else None
    return JSONResponse({"message": "Plan de masse enregistré.", "url": url})


@app.delete("/api/projets/{projet_id}/apd-assets/plan_masse")
def api_apd_clear_plan_masse(projet_id: int, db: Session = Depends(get_db)):
    """Retire le plan de masse (l'APD sera généré sans slide 3)."""
    import glob as _glob
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    n = 0
    for f in _glob.glob(os.path.join(_dossier_apd_assets(projet), "plan_masse.*")):
        try:
            os.remove(f)
            n += 1
        except Exception:
            pass
    return JSONResponse({"message": "Plan de masse retiré.", "supprimes": n})


# ---------------------------------------------------------------------------
# Annexes C6 / C7 (appuis Orange) : upload + jointure à la couche PT
# ---------------------------------------------------------------------------

def _dossier_annexes(projet):
    d = os.path.join(projet.chemin_dossier, "02_Traitement", "annexes")
    os.makedirs(d, exist_ok=True)
    return d


def _chemin_annexe(projet, typ):
    """Chemin du fichier annexe c6/c7 s'il existe (xlsx), sinon None."""
    d = _dossier_annexes(projet)
    for ext in (".xlsx", ".xlsm", ".xls"):
        p = os.path.join(d, f"{typ}{ext}")
        if os.path.exists(p):
            return p
    return None


def _natures_appuis_projet(projet, dossier_shape):
    """{NOM_poteau: nature} depuis les annexes C6/C7 du projet (ou {} si absentes)."""
    c6, c7 = _chemin_annexe(projet, "c6"), _chemin_annexe(projet, "c7")
    if not c6 and not c7:
        return {}
    try:
        from app.reporting import annexe_appuis as _aa
        annexes = _aa.charger_annexes(c6, c7)
        pt = gis_handler.lire_shapefile(os.path.join(dossier_shape, "PT.shp")) \
            if os.path.exists(os.path.join(dossier_shape, "PT.shp")) else None
        if pt is None or not annexes:
            return {}
        return _aa.natures_par_nom(pt, annexes)
    except Exception as e:
        logger.warning(f"Natures appuis (annexes) projet {projet.id} : {e}")
        return {}


def _dossier_folios(projet):
    d = os.path.join(projet.chemin_dossier, "02_Traitement", "folios")
    os.makedirs(d, exist_ok=True)
    return d


def _fond_opacite_projet(projet) -> float:
    """Opacité (0.15–1.0) du fond de carte des folios, réglée depuis la carte
    interactive du CRM. Défaut 1.0 (fond plein)."""
    p = os.path.join(projet.chemin_dossier, "02_Traitement", "fond.json")
    try:
        if os.path.exists(p):
            import json as _json
            with open(p, "r", encoding="utf-8") as f:
                v = float((_json.load(f) or {}).get("opacite", 1.0))
                return max(0.15, min(1.0, v))
    except Exception:
        pass
    return 1.0


def _doe_fo_params(projet, dossier_input):
    """(date_tvx aaaammjj, {cable_nom: fci}) pour le DOE FO. Priorité à la saisie
    Console (02_Traitement/doe.json) ; à défaut, date depuis le nom du .txt DATETVX."""
    from app.reporting import doe_fo_generator as _dfo
    import glob as _glob, json as _json
    date_tvx, fci = "", {}
    p = os.path.join(projet.chemin_dossier, "02_Traitement", "doe.json")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = _json.load(f) or {}
            date_tvx = str(d.get("date_tvx", "") or "").strip()
            fci = {str(k): v for k, v in (d.get("fci") or {}).items() if v}
        except Exception:
            pass
    if not date_tvx:                       # repli : date depuis le fichier DATETVX
        for t in (_glob.glob(os.path.join(dossier_input, "*DATETVX*.txt")) +
                  _glob.glob(os.path.join(projet.chemin_dossier, "**", "*DATETVX*.txt"),
                             recursive=True)):
            date_tvx = _dfo._date_tvx_depuis_txt(t)
            if date_tvx:
                break
    return date_tvx, fci


def _sauver_doe_exclus(projet, exclus):
    """Mémorise les NOM des BPE/PT existants exclus du DOE (affichage grisé)."""
    import json as _json
    d = os.path.join(projet.chemin_dossier, "02_Traitement")
    os.makedirs(d, exist_ok=True)
    try:
        with open(os.path.join(d, "doe_exclus.json"), "w", encoding="utf-8") as f:
            _json.dump(exclus, f, ensure_ascii=False)
    except Exception:
        pass


def _doe_exclus_projet(projet):
    """{'BPE':[…], 'PT':[…]} des éléments exclus du DOE (pour l'Édition Livrables)."""
    import json as _json
    p = os.path.join(projet.chemin_dossier, "02_Traitement", "doe_exclus.json")
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                d = _json.load(f) or {}
                return {"BPE": list(d.get("BPE", [])), "PT": list(d.get("PT", []))}
    except Exception:
        pass
    return {"BPE": [], "PT": []}


# colonnes DOE renseignées, par couche livrable (pour l'affichage Édition Livrables)
# NB : SUPPORT.DATE_CONST n'est PAS mis à jour (on conserve la valeur d'entrée).
_DOE_CHAMPS_LIVRABLE = {
    "BPE": ["ETAT", "DATE_DE_CR"],
    "PT": ["DATE_CREAT"],
    "CABLES": ["POSE", "FCI"],
}


def _maj_shp_livrables_doe(projet, date_tvx, fci, exclus):
    """Répercute les champs DOE (dates TVX, ETAT, FCI) dans les SHP LIVRABLES
    (source de vérité lue par l'Édition Livrables / PDS / KMZ), afin que les
    valeurs saisies apparaissent bien sur les couches. Les lignes exclues
    (BPE/PT existants) restent INCHANGÉES (elles s'affichent grisées).

    Renvoie le nombre de couches livrables mises à jour."""
    from app.reporting import doe_fo_generator as dfo
    dossier = _trouver_dossier_shape(projet)
    if not dossier or not os.path.isdir(dossier):
        return 0
    exclus = exclus or {}
    n = 0
    for base, champs_doe in _DOE_CHAMPS_LIVRABLE.items():
        p = os.path.join(dossier, f"{base}.shp")
        if not os.path.exists(p):
            continue
        try:
            g = gis_handler.lire_shapefile(p)
            for col in champs_doe:                     # garantir la présence des colonnes DOE
                if col not in g.columns:
                    g[col] = None
            # PT : DATE_CREAT reste le placeholder « AAAAMMJJ » (jamais la date TVX)
            # pour TOUS les PT -> exclus_base=[] (valeur uniforme). BPE : les
            # existants gardent leur DATE_DE_CR (exclusion conservée).
            exclus_base = [] if base == "PT" else exclus.get(base, [])
            dfo.appliquer_champs_doe(g, base, date_tvx, fci, exclus_noms=exclus_base)
            g.to_file(p, encoding="utf-8")
            n += 1
        except Exception as e:
            logger.warning(f"MAJ livrable {base} (DOE) échouée : {e}")
    return n


def _type_etude_projet(projet) -> str:
    """Type d'étude du projet : « APD FO » (défaut) ou « DOE FO » (choix création)."""
    import json as _json
    p = os.path.join(projet.chemin_dossier, "02_Traitement", "projet.json")
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return str((_json.load(f) or {}).get("type_etude", "APD FO")) or "APD FO"
    except Exception:
        pass
    return "APD FO"


def _sauver_type_etude_projet(projet, type_etude):
    import json as _json
    t = "DOE FO" if "DOE" in str(type_etude or "").upper() else "APD FO"
    d = os.path.join(projet.chemin_dossier, "02_Traitement")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "projet.json"), "w", encoding="utf-8") as f:
        _json.dump({"type_etude": t}, f, ensure_ascii=False)


def _folios_shp_projet(projet, dossier_shape):
    """Chemin du FOLIO_LIVRABLES.shp du projet (dessiné/importé), sinon celui du
    dossier SHAPE, sinon None (déclenche l'auto-génération)."""
    for p in (os.path.join(_dossier_folios(projet), "FOLIO_LIVRABLES.shp"),
              os.path.join(dossier_shape, "FOLIO_LIVRABLES.shp")):
        if os.path.exists(p):
            return p
    return None


def _bbox_3857_vers_wgs(b):
    """(x0,y0,x1,y1) en 3857 -> {west,south,east,north} en WGS84."""
    import geopandas as gpd
    from shapely.geometry import box
    g = gpd.GeoSeries([box(*b)], crs=3857).to_crs(4326).iloc[0].bounds
    return {"west": g[0], "south": g[1], "east": g[2], "north": g[3]}


@app.get("/api/projets/{projet_id}/folios")
def api_folios_get(projet_id: int, db: Session = Depends(get_db)):
    """Emprise réseau + folios existants (WGS84) pour l'éditeur."""
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    from app.reporting import plan_generator as pg
    dossier_shape = _trouver_dossier_shape(projet)
    couches = pg._charger_couches_folio(dossier_shape)
    extent = None
    try:
        extent = _bbox_3857_vers_wgs(pg._emprise(couches))
    except Exception:
        pass
    folios = []
    shp = _folios_shp_projet(projet, dossier_shape)
    if shp:
        try:
            for fid, b in pg._charger_folios(shp, couches):
                folios.append({"id": fid, **_bbox_3857_vers_wgs(b)})
        except Exception as e:
            logger.warning(f"Lecture folios projet {projet_id} : {e}")
    return JSONResponse({"extent": extent, "folios": folios})


@app.post("/api/projets/{projet_id}/folios/auto")
def api_folios_auto(projet_id: int, db: Session = Depends(get_db)):
    """Propose une grille de folios auto (WGS84), à retoucher dans l'éditeur."""
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    from app.reporting import plan_generator as pg
    couches = pg._charger_couches_folio(_trouver_dossier_shape(projet))
    try:
        recs = pg._folios_auto(couches)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Auto-génération impossible : {e}")
    return JSONResponse({"folios": [{"id": fid, **_bbox_3857_vers_wgs(b)} for fid, b in recs]})


@app.post("/api/projets/{projet_id}/folios")
async def api_folios_save(projet_id: int, request: Request, db: Session = Depends(get_db)):
    """Enregistre les folios (liste de bbox WGS84) -> FOLIO_LIVRABLES.shp (Lambert-93)."""
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Corps JSON invalide.")
    folios = body.get("folios", []) or []
    import glob as _glob
    dossier = _dossier_folios(projet)
    for f in _glob.glob(os.path.join(dossier, "FOLIO_LIVRABLES.*")):
        try:
            os.remove(f)
        except Exception:
            pass
    if not folios:
        return JSONResponse({"message": "Folios effacés.", "nb": 0})
    try:
        import geopandas as gpd
        from shapely.geometry import box
        geoms, ids = [], []
        for i, f in enumerate(folios, 1):
            geoms.append(box(float(f["west"]), float(f["south"]),
                             float(f["east"]), float(f["north"])))
            ids.append(int(f.get("id", i)))
        gdf = gpd.GeoDataFrame({"id": ids}, geometry=geoms, crs="EPSG:4326").to_crs(2154)
        gdf.to_file(os.path.join(dossier, "FOLIO_LIVRABLES.shp"), encoding="utf-8")
    except Exception as e:
        logger.error(f"Enregistrement folios projet {projet_id} : {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return JSONResponse({"message": f"{len(geoms)} folio(s) enregistré(s).", "nb": len(geoms)})


@app.post("/api/projets/{projet_id}/fond-opacite")
async def api_fond_opacite(projet_id: int, request: Request, db: Session = Depends(get_db)):
    """Enregistre l'opacité du fond de carte des folios (valeur utilisée à la
    génération) — réglée via le slider de la carte interactive du CRM."""
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    try:
        body = await request.json()
        op = max(0.15, min(1.0, float(body.get("opacite", 1.0))))
    except Exception:
        raise HTTPException(status_code=400, detail="Valeur d'opacité invalide.")
    d = os.path.join(projet.chemin_dossier, "02_Traitement")
    os.makedirs(d, exist_ok=True)
    import json as _json
    with open(os.path.join(d, "fond.json"), "w", encoding="utf-8") as f:
        _json.dump({"opacite": op}, f)
    return JSONResponse({"message": "Opacité du fond enregistrée.", "opacite": op})


@app.get("/api/projets/{projet_id}/doe-fo")
def api_doe_fo_get(projet_id: int, db: Session = Depends(get_db)):
    """Paramètres DOE FO : date TVX + liste des câbles de l'input avec leur FCI."""
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    dossier_input = os.path.join(projet.chemin_dossier, "01_Inputs_SHP")
    date_tvx, fci = _doe_fo_params(projet, dossier_input)
    cables = []
    p = os.path.join(dossier_input, "CABLES.shp")
    if os.path.exists(p):
        try:
            g = gis_handler.lire_shapefile(p)
            for _, r in g.iterrows():
                nom = str(r.get("NOM") or r.get("CODE") or "").strip()
                if nom and not any(c["nom"] == nom for c in cables):
                    cables.append({"nom": nom, "fci": str(fci.get(nom) or "")})
        except Exception:
            pass
    return JSONResponse({"date_tvx": date_tvx, "cables": cables})


@app.post("/api/projets/{projet_id}/doe-fo")
async def api_doe_fo_save(projet_id: int, request: Request, db: Session = Depends(get_db)):
    """Enregistre les paramètres DOE FO (date TVX aaaammjj + FCI par câble) -> doe.json."""
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Corps JSON invalide.")
    date_tvx = "".join(ch for ch in str(body.get("date_tvx", "")) if ch.isdigit())[:8]
    # FCI = numéro par câble (valeur libre) ; on ne garde que les câbles renseignés
    fci = {str(k): str(v).strip() for k, v in (body.get("fci") or {}).items() if str(v).strip()}
    d = os.path.join(projet.chemin_dossier, "02_Traitement")
    os.makedirs(d, exist_ok=True)
    import json as _json
    with open(os.path.join(d, "doe.json"), "w", encoding="utf-8") as f:
        _json.dump({"date_tvx": date_tvx, "fci": fci}, f, ensure_ascii=False)
    return JSONResponse({"message": "Paramètres DOE FO enregistrés.",
                         "date_tvx": date_tvx, "nb_fci": len(fci)})


def _enrichir_pt_depuis_annexes(projet, dossier_shape):
    """Met à jour NOM + CODE du PT livrable depuis les annexes C6/C7 (appuis
    détectés par numéro ou géolocalisation). Le schéma du SHP est préservé.
    Renvoie le nombre de poteaux mis à jour."""
    c6, c7 = _chemin_annexe(projet, "c6"), _chemin_annexe(projet, "c7")
    if not c6 and not c7:
        return 0
    p_pt = os.path.join(dossier_shape, "PT.shp")
    if not os.path.exists(p_pt):
        return 0
    try:
        from app.reporting import annexe_appuis as _aa
        annexes = _aa.charger_annexes(c6, c7)
        if not annexes:
            return 0
        pt = gis_handler.lire_shapefile(p_pt)
        assoc = _aa.associer_pt(pt, annexes)
        n = _aa.enrichir_pt_nom_code(pt, assoc)
        if n:
            pt.to_file(p_pt, encoding="utf-8")
            logger.info(f"PT enrichi depuis annexes (projet {projet.id}) : "
                        f"{n} appui(s) NOM/CODE mis à jour")
        return n
    except Exception as e:
        logger.warning(f"Enrichissement PT depuis annexes projet {projet.id} : {e}")
        return 0


def _detecter_annexe(data):
    """Devine c6/c7 depuis les feuilles du classeur Excel."""
    try:
        import io, openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
        sheets = [s.lower() for s in wb.sheetnames]
        wb.close()
        if any("export" in s or "saisie" in s for s in sheets):
            return "c6"
        if any("commande" in s or "restitution" in s for s in sheets):
            return "c7"
    except Exception:
        pass
    return None


def _apercu_annexes(projet, dossier_shape):
    """Aperçu de la correspondance C6/C7 ↔ PT du projet (appuis concordants +
    natures) pour le récapitulatif affiché à l'utilisateur."""
    c6, c7 = _chemin_annexe(projet, "c6"), _chemin_annexe(projet, "c7")
    from app.reporting import annexe_appuis as _aa
    annexes = _aa.charger_annexes(c6, c7)
    ap = {"a_c6": bool(c6), "a_c7": bool(c7), "total": len(annexes),
          "matched": [], "non_matched": list(annexes.keys()),
          "counts": {"remplacer": 0, "recaler": 0, "renforcer": 0}}
    p_pt = os.path.join(dossier_shape, "PT.shp")
    if not annexes or not os.path.exists(p_pt):
        return ap
    try:
        pt = gis_handler.lire_shapefile(p_pt)
        assoc = _aa.associer_pt(pt, annexes)
        for idx, num in assoc.items():
            d = annexes.get(num) or {}
            ap["matched"].append({"num": num, "nature": d.get("nature", ""),
                                  "nom_pt": str(pt.at[idx, "NOM"]) if "NOM" in pt.columns else num})
        ap["counts"] = _aa.compter_natures(annexes, assoc)
        pris = set(assoc.values())
        ap["non_matched"] = [n for n in annexes if n not in pris]
    except Exception as e:
        logger.warning(f"Aperçu annexes projet {projet.id} : {e}")
    return ap


@app.post("/api/projets/{projet_id}/annexes/{typ}")
async def api_annexe_upload(projet_id: int, typ: str, fichier: UploadFile = File(...),
                            db: Session = Depends(get_db)):
    """Upload d'une annexe appuis C6/C7 (Excel). `typ` = c6, c7 ou auto (détection).
    Requiert que les couches livrables existent déjà. Pose le verrou « MAJ Shapes »
    et renvoie l'aperçu des appuis concordants avec le PT du projet."""
    typ = (typ or "").lower()
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    # Gating : les SHP livrables doivent déjà exister.
    if not any((c.nom or "").startswith("[Livrable]") for c in projet.couches):
        raise HTTPException(
            status_code=400,
            detail="Générez d'abord les SHP livrables : les couches livrables "
                   "doivent être présentes avant d'ajouter les annexes C6/C7.")
    ext = os.path.splitext(fichier.filename or "")[1].lower()
    if ext not in (".xlsx", ".xlsm", ".xls"):
        raise HTTPException(status_code=400, detail="Format non supporté (Excel .xlsx).")
    data = await fichier.read()
    if typ not in ("c6", "c7"):
        typ = _detecter_annexe(data) or ("c7" if "c7" in (fichier.filename or "").lower() else "c6")
    import glob as _glob
    dossier = _dossier_annexes(projet)
    for f in _glob.glob(os.path.join(dossier, f"{typ}.*")):
        try:
            os.remove(f)
        except Exception:
            pass
    with open(os.path.join(dossier, f"{typ}{ext}"), "wb") as f:
        f.write(data)
    _marquer_shapes_a_maj(projet)  # annexes ajoutées -> régénération SHP requise
    apercu = _apercu_annexes(projet, _trouver_dossier_shape(projet))
    return JSONResponse({"message": f"Annexe {typ.upper()} enregistrée.",
                         "type": typ, "apercu": apercu})


@app.delete("/api/projets/{projet_id}/annexes/{typ}")
def api_annexe_clear(projet_id: int, typ: str, db: Session = Depends(get_db)):
    """Retire une annexe C6 / C7."""
    typ = (typ or "").lower()
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    import glob as _glob
    n = 0
    for f in _glob.glob(os.path.join(_dossier_annexes(projet), f"{typ}.*")):
        try:
            os.remove(f)
            n += 1
        except Exception:
            pass
    return JSONResponse({"message": f"Annexe {typ.upper()} retirée.", "supprimes": n})


@app.post("/api/projets/{projet_id}/type-etude")
async def api_maj_type_etude(projet_id: int, request: Request, db: Session = Depends(get_db)):
    """Change le type d'étude d'une affaire (« APD FO » / « DOE FO ») : détermine
    le gabarit des SHP livrables générés (NETGEO pour le DOE FO). Permet aussi de
    typer les affaires créées avant l'ajout du choix au modal."""
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    try:
        body = await request.json()
    except Exception:
        body = {}
    _sauver_type_etude_projet(projet, body.get("type_etude", "APD FO"))
    return JSONResponse({"type_etude": _type_etude_projet(projet)})


@app.post("/api/projets/{projet_id}/ouvrir-dossier")
def api_ouvrir_dossier(projet_id: int, db: Session = Depends(get_db)):
    """Ouvre le dossier d'enregistrement du projet dans l'explorateur de fichiers.
    L'application tournant en LOCAL, l'ouverture se fait sur la machine de l'utilisateur."""
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    dossier = projet.chemin_dossier
    if not dossier or not os.path.isdir(dossier):
        raise HTTPException(status_code=404, detail="Dossier du projet introuvable sur le disque.")
    try:
        if hasattr(os, "startfile"):            # Windows
            os.startfile(dossier)               # noqa: S606 (appli locale, chemin maîtrisé)
        else:                                   # macOS / Linux
            import subprocess, sys as _sys
            subprocess.Popen(["open" if _sys.platform == "darwin" else "xdg-open", dossier])
        return JSONResponse({"message": "Dossier ouvert.", "path": dossier})
    except Exception as e:
        logger.error(f"Ouverture du dossier du projet {projet_id} : {e}")
        raise HTTPException(status_code=500, detail=f"Impossible d'ouvrir le dossier : {e}")


# Longueur (mètres, Lambert-93) par couche linéaire.
#   · SUPPORT.LGR_REEL   = longueur GÉOMÉTRIQUE du tronçon  -> recalculable/écrasable.
#   · CABLES.LONGUEUR_R  = longueur RÉELLE du câble (inclut le mou : lovES, descentes,
#                          coils) -> volontairement > longueur 2D : on VÉRIFIE l'écart
#                          mais on N'ÉCRASE PAS (sinon on détruit la longueur réelle).
_LONGUEUR_CHAMP = {"CABLES": "LONGUEUR_R", "SUPPORT": "LGR_REEL"}
_LONGUEUR_ECRIRE = {"CABLES": False, "SUPPORT": True}


@app.post("/api/projets/{projet_id}/maj-longueurs")
def api_maj_longueurs(projet_id: int, db: Session = Depends(get_db)):
    """Vérifie les longueurs des couches linéaires du livrable par rapport à la
    géométrie (projection Lambert-93 = mètres).

    · SUPPORT (LGR_REEL = longueur géométrique) : recalculé et écrit dans le SHP.
    · CÂBLES (LONGUEUR_R = longueur réelle, avec mou) : on signale l'écart réel/projeté
      SANS écraser la valeur ; une longueur déclarée < tracé est marquée « anomalie ».

    Active le verrou « MAJ Shapes » uniquement si des longueurs ont réellement été
    écrites. Renvoie un rapport détaillé par couche."""
    import geopandas as gpd
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    dossier = _trouver_dossier_shape(projet)
    if not dossier or not os.path.isdir(dossier):
        raise HTTPException(status_code=400,
                            detail="Aucun SHP livrable : générez d'abord les livrables.")
    TOL = 0.5   # tolérance en mètres (sous laquelle une longueur est considérée conforme)
    rapport = {"couches": [], "nb_ecrit": 0, "nb_anomalie": 0, "nb_total": 0}
    for base, champ in _LONGUEUR_CHAMP.items():
        p = os.path.join(dossier, f"{base}.shp")
        if not os.path.exists(p):
            continue
        ecrire = _LONGUEUR_ECRIRE.get(base, False)
        try:
            g = gis_handler.lire_shapefile(p)          # géométries d'origine (2154 attendu)
        except Exception as e:
            rapport["couches"].append({"couche": base, "erreur": str(e)})
            continue
        # copie en CRS métrique pour mesurer en mètres (sans toucher aux géométries écrites)
        g_m = g
        try:
            if g.crs is None:
                g_m = g.set_crs(epsg=2154)
            elif (g.crs.to_epsg() or 0) != 2154:
                g_m = g.to_crs(epsg=2154)
        except Exception:
            g_m = g
        if champ not in g.columns:
            g[champ] = None
        ecarts, n_ecrit, n_anom = [], 0, 0
        for i, row in g_m.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            # longueur uniquement pour les géométries linéaires (un point aurait length=0
            # et écraserait à tort une valeur métier -> on ignore le non-linéaire)
            if geom.geom_type not in ("LineString", "MultiLineString"):
                continue
            L = round(float(geom.length), 1)
            anc = g.at[i, champ]
            try:
                anc_f = None if anc in (None, "") else round(float(anc), 1)
            except (TypeError, ValueError):
                anc_f = None
            if anc_f is not None and abs(anc_f - L) <= TOL:
                continue                               # conforme
            nom = (str(g.at[i, "NOM"]) if "NOM" in g.columns and g.at[i, "NOM"] not in (None, "")
                   else str(g.at[i, "LIBELLE"]) if "LIBELLE" in g.columns and g.at[i, "LIBELLE"] not in (None, "")
                   else f"#{i + 1}")
            ec = {"nom": nom, "ancienne": anc_f, "projetee": L}
            if ecrire:
                g.at[i, champ] = L                     # SUPPORT : on écrit la longueur géométrique
                ec["nouvelle"] = L
                n_ecrit += 1
            else:
                # CÂBLE : signalement seul. Anomalie si la longueur déclarée est
                # ABSENTE ou PLUS COURTE que le tracé (physiquement impossible).
                ec["anomalie"] = (anc_f is None) or (anc_f < L - TOL)
                if ec["anomalie"]:
                    n_anom += 1
            ecarts.append(ec)
        if ecrire and n_ecrit:
            g.to_file(p, encoding="utf-8")
        rapport["couches"].append({
            "couche": base, "champ": champ, "nb": int(len(g)),
            "ecrire": ecrire, "nb_ecrit": n_ecrit,
            "nb_ecart": len(ecarts), "nb_anomalie": n_anom,
            "ecarts": ecarts[:300],
        })
        rapport["nb_ecrit"] += n_ecrit
        rapport["nb_anomalie"] += n_anom
        rapport["nb_total"] += int(len(g))
    # « conforme » = rien à écrire ET aucune anomalie câble (le mou câble > tracé est normal)
    rapport["conforme"] = (rapport["nb_ecrit"] == 0 and rapport["nb_anomalie"] == 0)
    if rapport["nb_ecrit"] > 0:
        _marquer_shapes_a_maj(projet)                  # MAJ Shapes obligatoire (livrables modifiés)
    logger.info(f"MAJ longueurs projet {projet_id} : {rapport['nb_ecrit']} support(s) écrit(s), "
                f"{rapport['nb_anomalie']} anomalie(s) câble sur {rapport['nb_total']} entités.")
    return JSONResponse(rapport)


@app.get("/clients", response_class=HTMLResponse)
def page_clients(request: Request, db: Session = Depends(get_db)):
    """Page CRM : Gestion des clients."""
    clients = crm_service.lister_clients(db)
    return templates.TemplateResponse("clients.html", {
        "request": request,
        "clients": clients,
    })


@app.get("/etudes", response_class=HTMLResponse)
def page_etudes_globales(request: Request, db: Session = Depends(get_db)):
    """Page CRM : Vue globale de toutes les études."""
    projets = crm_service.lister_projets(db)
    logs = crm_service.lister_logs(db, limit=20)
    clients = crm_service.lister_clients(db)
    return templates.TemplateResponse("etudes_global.html", {
        "request": request,
        "projets": projets,
        "logs": logs,
        "clients": clients
    })


@app.get("/access", response_class=HTMLResponse)
def page_access(request: Request, db: Session = Depends(get_db)):
    """Page CRM : Gestion des utilisateurs et accès."""
    utilisateurs = crm_service.lister_utilisateurs(db)
    return templates.TemplateResponse("access.html", {
        "request": request,
        "utilisateurs": utilisateurs,
    })


# =====================================================================
# API REST : GESTION DES PROJETS (CRM)
# =====================================================================

@app.post("/api/projets")
def api_creer_projet(
    nom: str = Form(...),
    description: str = Form(""),
    client_id: str = Form(""),
    type_etude: str = Form("APD FO"),
    db: Session = Depends(get_db)
):
    """Crée un nouveau projet et redirige vers le tableau de bord."""
    try:
        cid = int(client_id) if client_id else None
        projet = crm_service.creer_projet(db, nom=nom, description=description, client_id=cid)
        try:                                   # type d'étude (APD FO / DOE FO)
            _sauver_type_etude_projet(projet, type_etude)
        except Exception:
            pass
        # NB : les SHP livrables ne sont PAS créés ici. Ils sont générés à la
        # demande — pour le DOE FO, via le bouton « Dossier NETGEO (SHP/KMZ/DWG) »
        # (schéma NETGEO), depuis les couches importées.
        return RedirectResponse(url="/", status_code=303)
    except Exception as e:
        logger.error(f"Erreur API creer_projet: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/projets/{projet_id}/statut")
def api_mettre_a_jour_statut(
    projet_id: int,
    statut: str = Form(...),
    db: Session = Depends(get_db)
):
    """Met à jour le statut d'un projet."""
    projet = crm_service.mettre_a_jour_statut(db, projet_id, statut)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/projets/{projet_id}/resume")
def api_resume_projet(projet_id: int, db: Session = Depends(get_db)):
    """Résumé (consultation seule) d'une affaire pour le tableau de bord : infos
    déduites des SHP LIVRABLES (site NRA/BTS/NRO, adresse, commune, nb BPE,
    câbles + capacités, appuis, chambres) + date de création."""
    import glob as _glob
    from app.reporting import apd_generator as ag
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")

    resume = {
        "reference": projet.reference or f"#{projet.id:04d}",
        "nom": projet.nom,
        "description": projet.description or "",
        "client": projet.client.nom if projet.client else None,
        "statut": projet.statut,
        "date_creation": (projet.date_creation.strftime("%d/%m/%Y")
                          if projet.date_creation else None),
        "a_livrable": False,
        "site": None, "adresse": "", "cp": "", "commune": "",
        "bpe": 0, "cables": 0, "capacites": [], "appuis": 0, "chambres": 0,
    }

    dossier = _trouver_dossier_shape(projet)
    if not (dossier and os.path.isdir(dossier)
            and _glob.glob(os.path.join(dossier, "*.shp"))):
        return JSONResponse(resume)  # pas encore de SHP livrable généré
    resume["a_livrable"] = True

    # Site / adresse / commune / câbles : via la synthèse (mêmes règles que l'APD).
    try:
        synth = ag.calculer_synthese(dossier, ref_projet=(projet.reference or f"AFF_{projet.id}"))
        cart = synth.get("cartouche", {})
        site = cart.get("site") or ""
        resume["site"] = {"type": site, "code": cart.get("code_projet", "")} if site else None
        resume["adresse"] = cart.get("adresse", "")
        resume["cp"] = cart.get("cp", "")
        resume["commune"] = cart.get("commune", "")
        resume["cables"] = synth.get("cables", {}).get("nombre", 0)
        resume["capacites"] = synth.get("cables", {}).get("capacites", [])
    except Exception as e:
        logger.warning(f"Résumé projet {projet_id} : synthèse partielle ({e})")

    # Comptages directs depuis les SHP livrables.
    import geopandas as gpd

    def _lire(nom):
        p = os.path.join(dossier, f"{nom}.shp")
        if not os.path.exists(p):
            return None
        try:
            g = gpd.read_file(p)
            return g if len(g) else None
        except Exception:
            return None

    bpe = _lire("BPE")
    resume["bpe"] = 0 if bpe is None else len(bpe)
    pt = _lire("PT")
    if pt is not None:
        ch = ap = 0
        for _, r in pt.iterrows():
            if ag._est_poteau(str(r.get("TYPE_STRUC") or "")):
                ap += 1
            else:
                ch += 1
        resume["chambres"] = ch
        resume["appuis"] = ap
    return JSONResponse(resume)


@app.delete("/api/projets/{projet_id}")
def api_supprimer_projet(projet_id: int, db: Session = Depends(get_db)):
    """Supprime un projet de la base de données."""
    success = crm_service.supprimer_projet(db, projet_id)
    if not success:
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    return JSONResponse({"message": "Projet supprimé"})


@app.post("/api/clients")
async def api_creer_client(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    nom = form.get("nom")
    email = form.get("email")
    telephone = form.get("telephone")
    adresse = form.get("adresse")
    try:
        crm_service.creer_client(db, nom, email, telephone, adresse)
        return RedirectResponse(url="/clients", status_code=303)
    except Exception as e:
        msg = str(e)
        logger.error(f"Erreur API creer_client: {msg}")
        # Doublon d'email : message clair (400) au lieu d'une erreur serveur (500).
        if "UNIQUE constraint failed: clients.email" in msg:
            raise HTTPException(
                status_code=400,
                detail=f"Un client utilise déjà l'adresse e-mail « {email} ». "
                       "Utilisez une autre adresse ou laissez le champ vide.")
        raise HTTPException(status_code=500, detail=msg)

@app.post("/api/clients/{client_id}/delete")
def api_supprimer_client(client_id: int, db: Session = Depends(get_db)):
    success = crm_service.supprimer_client(db, client_id)
    if not success:
        raise HTTPException(status_code=404, detail="Client non trouvé")
    return RedirectResponse(url="/clients", status_code=303)

@app.post("/api/access")
async def api_creer_utilisateur(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    nom = form.get("nom")
    email = form.get("email")
    role = form.get("role")
    try:
        crm_service.creer_utilisateur(db, nom, email, role)
        return RedirectResponse(url="/access", status_code=303)
    except Exception as e:
        logger.error(f"Erreur API creer_utilisateur: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/access/{user_id}/delete")
def api_supprimer_utilisateur(user_id: int, db: Session = Depends(get_db)):
    success = crm_service.supprimer_utilisateur(db, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
    return RedirectResponse(url="/access", status_code=303)


@app.post("/api/clients/{client_id}/nomenclature")
async def api_sauvegarder_nomenclature(client_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        data = await request.json()
        client = crm_service.maj_nomenclature_client(db, client_id, data)
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouvé")
        return {"success": True, "message": "Nomenclature mise à jour"}
    except Exception as e:
        logger.error(f"Erreur API nomenclature: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# =====================================================================
# API REST : GESTION SIG (Import / GeoJSON)
# =====================================================================

@app.post("/api/projets/{projet_id}/upload-shp")
async def api_upload_shapefile(
    projet_id: int,
    fichiers: list[UploadFile] = File(...),
    db: Session = Depends(get_db)
):
    """
    Importe un ensemble de fichiers Shapefile dans le projet.
    Supporte le multi-couches : si l'utilisateur glisse plusieurs .shp
    de noms différents (ex: SUPPORT.shp, CABLE.shp, ZONE.shp),
    chaque .shp sera enregistré comme une couche distincte.
    """
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")

    # Dossier de destination pour les fichiers source
    dossier_input = os.path.join(projet.chemin_dossier, "01_Inputs_SHP")
    os.makedirs(dossier_input, exist_ok=True)

    # Couleurs distinctes pour différencier les couches sur la carte
    COULEURS = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6",
                "#1abc9c", "#e67e22", "#34495e", "#e91e63", "#00bcd4"]

    # 1. Sauvegarder TOUS les fichiers uploadés sur le disque d'abord
    chemins_shp = []
    dossier_input_abs = os.path.abspath(dossier_input)
    for fichier in fichiers:
        # Assainir le nom (anti path-traversal) : on ne garde que le nom de base,
        # ce qui neutralise « ../ », « ..\ » et les chemins absolus.
        nom_fichier = os.path.basename((fichier.filename or "").replace("\\", "/")).strip()
        if not nom_fichier or nom_fichier in (".", ".."):
            continue
        chemin_dest = os.path.join(dossier_input, nom_fichier)
        if not os.path.abspath(chemin_dest).startswith(dossier_input_abs + os.sep):
            logger.warning(f"Upload refusé (nom de fichier suspect) : {fichier.filename!r}")
            continue
        with open(chemin_dest, "wb") as f:
            contenu = await fichier.read()
            f.write(contenu)
        logger.info(f"Fichier sauvegardé : {chemin_dest}")

        # Collecter tous les .shp trouvés
        if nom_fichier.lower().endswith(".shp"):
            chemins_shp.append(chemin_dest)

    # 2. Pour CHAQUE .shp trouvé, créer une couche dans la BDD
    erreurs = []
    nb_couches_ok = 0
    couches_existantes = db.query(models.CoucheSIG).filter(
        models.CoucheSIG.projet_id == projet_id
    ).count()

    for i, chemin_shp in enumerate(chemins_shp):
        try:
            gdf = gis_handler.lire_shapefile(chemin_shp)
            metadonnees = gis_handler.extraire_metadonnees(gdf)

            nom_couche = os.path.splitext(os.path.basename(chemin_shp))[0]
            type_geom = metadonnees["types_geometrie"][0] if metadonnees["types_geometrie"] else "Inconnu"
            crs = str(metadonnees["crs"]) if metadonnees["crs"] else "4326"
            # Ré-import de la MÊME couche (même nom) : mise à jour au lieu de créer
            # un doublon (sinon couche affichée en double + comptes faussés).
            existante = db.query(models.CoucheSIG).filter(
                models.CoucheSIG.projet_id == projet_id,
                models.CoucheSIG.nom == nom_couche,
            ).first()
            if existante is not None:
                existante.type_geometrie = type_geom
                existante.chemin_fichier = chemin_shp
                existante.systeme_projection = crs
                existante.nb_entites = metadonnees["nb_entites"]
            else:
                db.add(models.CoucheSIG(
                    nom=nom_couche, type_geometrie=type_geom, chemin_fichier=chemin_shp,
                    systeme_projection=crs, nb_entites=metadonnees["nb_entites"],
                    couleur=COULEURS[(couches_existantes + nb_couches_ok) % len(COULEURS)],
                    projet_id=projet_id,
                ))
            db.commit()
            nb_couches_ok += 1
            logger.info(f"Couche SIG enregistrée : {nom_couche} ({metadonnees['nb_entites']} entités)")

        except Exception as e:
            nom_fichier = os.path.basename(chemin_shp)
            logger.error(f"Erreur traitement '{nom_fichier}': {str(e)}")
            erreurs.append(f"{nom_fichier}: {str(e)}")

    if erreurs and nb_couches_ok == 0:
        return JSONResponse(
            {"error": f"Erreur(s) d'analyse : {'; '.join(erreurs)}"},
            status_code=422
        )

    # Détection : couche CABLES vide/absente + possibilité de la générer (étude APD FO)
    couches = db.query(models.CoucheSIG).filter(
        models.CoucheSIG.projet_id == projet_id
    ).all()

    def _couche_input(nom):
        for c in couches:
            if c.nom.upper().startswith("[LIVRABLE]"):
                continue
            if c.nom.upper() == nom:
                return c
        return None

    couche_cables = _couche_input("CABLES")
    couche_support = _couche_input("SUPPORT")
    couche_bpe = _couche_input("BPE")
    cables_vides = (couche_cables is None) or (couche_cables.nb_entites or 0) == 0
    peut_generer_cables = (
        couche_support is not None and (couche_support.nb_entites or 0) > 0
        and couche_bpe is not None and (couche_bpe.nb_entites or 0) > 0
    )

    # Couches SUPPORT/PT/CABLES (input) à compléter -> propositions de nomenclature
    couches_a_completer = _couches_a_completer(projet, db, livrable=False)

    return JSONResponse({
        "success": True,
        "nb_couches": nb_couches_ok,
        "erreurs": erreurs,
        "cables_vides": cables_vides,
        "peut_generer_cables": peut_generer_cables,
        "couches_a_completer": couches_a_completer,
        "redirect_url": f"/map/{projet_id}",
    })


def _chemins_livrables_apd(projet) -> str:
    """Dossier SHAPE des livrables (source de vérité). Nommé selon le TYPE d'étude :
    ``DOE_FO/DOE_HTL`` pour une étude DOE FO, ``APD_FO/APD_HTL`` sinon — pour ne
    plus ranger un livrable DOE FO dans un dossier « APD_FO »."""
    from datetime import datetime
    date_str = datetime.utcnow().strftime("%y%m%d")
    ref_propre = (projet.reference or f"AFF_{projet.id}").replace("-", "_")
    base_doe = os.path.join(projet.chemin_dossier, "04_Livrables_DOE")
    pref = "DOE" if _type_etude_projet(projet).upper().startswith("DOE") else "APD"
    dossier_global = os.path.join(base_doe, f"{pref}_FO_{ref_propre}_{date_str}")
    dossier_carto = os.path.join(dossier_global, f"{pref}_HTL_{ref_propre}_02_{date_str}")
    return os.path.join(dossier_carto, "SHAPE")


def _shape_dir_valide(dossier) -> bool:
    """True si le dossier SHAPE contient au moins un .shp RÉEL (non vide).

    Un livrable mock (``doe_global``) crée des .shp de 0 octet : un en-tête ESRI
    valide fait au moins 100 octets. On écarte ainsi les dossiers vides qui
    « masqueraient » un vrai dossier livrable plus ancien."""
    import glob
    for shp in glob.glob(os.path.join(dossier, "*.shp")):
        try:
            if os.path.getsize(shp) > 100:
                return True
        except OSError:
            continue
    return False


def _trouver_dossier_shape(projet) -> str:
    """Dossier SHAPE des livrables : renvoie le dossier VALIDE le plus récent
    (source de vérité pour PDS/KMZ), en IGNORANT les dossiers vides/mock ;
    sinon le chemin par défaut (date du jour)."""
    import glob
    base_doe = os.path.join(projet.chemin_dossier, "04_Livrables_DOE")
    # APD FO (APD_HTL) ET DOE FO (DOE_HTL) — pas le livrable NETGEO (DOE_NETGEO/03.3_Shapes)
    cands = [c for c in (
        glob.glob(os.path.join(base_doe, "APD_FO_*", "APD_HTL_*", "SHAPE"))
        + glob.glob(os.path.join(base_doe, "DOE_FO_*", "DOE_HTL_*", "SHAPE"))
    ) if os.path.isdir(c)]
    if cands:
        cands.sort(key=os.path.getmtime, reverse=True)
        for c in cands:
            if _shape_dir_valide(c):
                return c
        return cands[0]   # aucun valide : le plus récent (comportement historique)
    return _chemins_livrables_apd(projet)


def _resoudre_shp_livrable(projet, couche) -> str:
    """Chemin canonique du SHP à LIRE/écrire pour une couche.

    Pour une couche `[Livrable]`, cible le SHP LIVRABLE (04_.../SHAPE) — source
    de vérité pour la carte / PDS / KMZ — s'il existe ; sinon le propre chemin
    de la couche. Fonction PURE : aucune écriture BDD (utilisable dans les GET)."""
    if not couche.nom or "[Livrable]" not in couche.nom:
        return couche.chemin_fichier
    base = couche.nom.upper().replace("[LIVRABLE]", "").strip()
    if not base:
        return couche.chemin_fichier
    p_liv = os.path.join(_trouver_dossier_shape(projet), f"{base}.shp")
    return p_liv if os.path.exists(p_liv) else couche.chemin_fichier


def _bases_livrables(projet) -> set:
    """Noms de base (sans préfixe) des couches disposant d'une version [Livrable]."""
    s = set()
    for c in projet.couches:
        if (c.nom or "").strip().lower().startswith("[livrable]"):
            s.add(c.nom.upper().replace("[LIVRABLE]", "").strip())
    return s


def _couche_porte_etiquettes(projet, couche, bases_liv=None) -> bool:
    """True si cette couche est la PROPRIÉTAIRE des étiquettes carte pour sa base.

    Une couche input et son jumeau [Livrable] portent la même géométrie/mêmes
    libellés : pour éviter le double affichage (et un toggle qui ne masque qu'un
    des deux), seule une couche « porte » les étiquettes — la [Livrable] (source
    de vérité) si elle existe, sinon la couche input elle-même."""
    from app.gis import symbologie as _symb
    nom = couche.nom or ""
    if not _symb.est_stylee(nom):
        return False
    if nom.strip().lower().startswith("[livrable]"):
        return True
    base = nom.upper().replace("[LIVRABLE]", "").strip()
    if bases_liv is None:
        bases_liv = _bases_livrables(projet)
    return base not in bases_liv


def _chemin_shp_edition(db, projet, couche) -> str:
    """Comme `_resoudre_shp_livrable`, mais RECALE (persiste) le pointeur de la
    couche sur le SHP livrable. À réserver aux endpoints d'ÉCRITURE
    (sauvegarde/suppression) — jamais dans un simple GET."""
    chemin = _resoudre_shp_livrable(projet, couche)
    if chemin and os.path.abspath(chemin) != os.path.abspath(couche.chemin_fichier or ""):
        couche.chemin_fichier = chemin
        try:
            db.commit()
        except Exception:
            db.rollback()
    return chemin


@app.post("/api/projets/{projet_id}/generer-cables")
def api_generer_cables(projet_id: int, db: Session = Depends(get_db)):
    """
    Génère automatiquement la couche CABLES (étude APD FO) à partir des couches
    SUPPORT + BPE (+ BTS) importées, puis l'écrit dans les livrables et les inputs.
    """
    import geopandas as gpd

    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")

    couches = db.query(models.CoucheSIG).filter(
        models.CoucheSIG.projet_id == projet_id
    ).all()

    def _couche_input(nom):
        for c in couches:
            if c.nom.upper().startswith("[LIVRABLE]"):
                continue
            if c.nom.upper() == nom:
                return c
        return None

    couche_support = _couche_input("SUPPORT")
    couche_bpe = _couche_input("BPE")
    couche_bts = _couche_input("BTS")
    couche_cables = _couche_input("CABLES")

    if not couche_support or not couche_bpe:
        raise HTTPException(
            status_code=400,
            detail="Les couches SUPPORT et BPE sont requises pour générer les câbles."
        )

    try:
        support_gdf = gis_handler.lire_shapefile(couche_support.chemin_fichier)
        bpe_gdf = gis_handler.lire_shapefile(couche_bpe.chemin_fichier)
        bts_gdf = gis_handler.lire_shapefile(couche_bts.chemin_fichier) if couche_bts else None
        modele_cables = gpd.read_file(os.path.join(MODELE_SHAPE_DIR, "CABLES.shp"))

        cables = gis_handler.construire_cables(support_gdf, bpe_gdf, bts_gdf, modele_cables)

        if len(cables) == 0:
            return JSONResponse({
                "message": "Aucun câble généré (moins de 2 boîtes détectées sur le réseau SUPPORT).",
                "nb_cables": 0
            })

        # 1. Écrire dans les livrables (04_Livrables_DOE/.../SHAPE)
        dossier_sortie = _chemins_livrables_apd(projet)
        os.makedirs(dossier_sortie, exist_ok=True)
        chemin_livrable = os.path.join(dossier_sortie, "CABLES.shp")
        cables.to_file(chemin_livrable, encoding="utf-8")

        # 2. Recopier dans les inputs pour que "Créer SHP" ne l'écrase pas par du vide
        dossier_input = os.path.join(projet.chemin_dossier, "01_Inputs_SHP")
        os.makedirs(dossier_input, exist_ok=True)
        chemin_input = os.path.join(dossier_input, "CABLES.shp")
        cables.to_file(chemin_input, encoding="utf-8")

        epsg = str(cables.crs.to_epsg()) if cables.crs else "2154"

        # 3. Mettre à jour / créer la couche CABLES (input) affichée sur la carte
        if couche_cables:
            couche_cables.nb_entites = len(cables)
            couche_cables.type_geometrie = "LineString"
            couche_cables.chemin_fichier = chemin_input
        else:
            db.add(models.CoucheSIG(
                nom="CABLES", type_geometrie="LineString", chemin_fichier=chemin_input,
                systeme_projection=epsg, nb_entites=len(cables),
                couleur="#ff22aa", projet_id=projet_id,
            ))

        # Couche livrable dédiée
        nom_livrable = "[Livrable] CABLES"
        couche_liv = next((c for c in couches if c.nom == nom_livrable), None)
        if couche_liv:
            couche_liv.nb_entites = len(cables)
            couche_liv.chemin_fichier = chemin_livrable
        else:
            db.add(models.CoucheSIG(
                nom=nom_livrable, type_geometrie="LineString", chemin_fichier=chemin_livrable,
                systeme_projection=epsg, nb_entites=len(cables),
                couleur="#ff0000", projet_id=projet_id,
            ))

        crm_service.enregistrer_log(
            db=db, projet_id=projet.id, type_livrable="CABLES_AUTO",
            details=f"{len(cables)} cables generes depuis SUPPORT + BPE + BTS"
        )
        db.commit()

        return JSONResponse({
            "message": f"{len(cables)} câble(s) généré(s) et enregistré(s) dans les livrables.",
            "nb_cables": len(cables),
            "path": chemin_livrable,
        })
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur génération câbles projet {projet_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def _pds_livrable(db, projet, chemin_sortie, nb_fo=2, capacite_defaut=48, commentaire=None):
    """Génère le PDS (plan de soudure Excel) depuis les SHP LIVRABLES.

    Source de vérité = dossier SHAPE livrable ; repli sur les couches BDD.
    Renvoie (chemin, nb_onglets). Lève HTTPException si la couche BPE est absente.
    Utilisé par l'endpoint /generer-pds ET par la génération /generer-etude/pds
    (séquence Dossier Complet), pour éviter le mock qui produisait un xlsx vide."""
    from app.reporting import pds_generator
    dossier_shape = _trouver_dossier_shape(projet)
    couches = db.query(models.CoucheSIG).filter(
        models.CoucheSIG.projet_id == projet.id).all()

    def _pref(nom):
        inp = liv = None
        for c in couches:
            up = (c.nom or "").upper()
            if up == nom:
                inp = c
            elif up == f"[LIVRABLE] {nom}":
                liv = c
        return liv or inp

    def _lire(nom_fichier, base):
        p = os.path.join(dossier_shape, nom_fichier)
        if os.path.exists(p):
            return gis_handler.lire_shapefile(p)
        c = _pref(base)
        return gis_handler.lire_shapefile(c.chemin_fichier) if c else None

    bpe = _lire("BPE.shp", "BPE")
    if bpe is None or len(bpe) == 0:
        raise HTTPException(status_code=400,
                            detail="Couche BPE introuvable. Générez d'abord les SHP livrables (Créer SHP).")
    cab = _lire("CABLES.shp", "CABLES")
    bts = _lire("BTS.shp", "BTS")
    pt = _lire("PT.shp", "PT")
    return pds_generator.generer_pds(
        bpe, cab, bts, pt, chemin_sortie, template_path=PDS_TEMPLATE,
        nb_fo=nb_fo, capacite_defaut=capacite_defaut, commentaire=commentaire)


@app.post("/api/projets/{projet_id}/generer-pds")
async def api_generer_pds(projet_id: int, request: Request, db: Session = Depends(get_db)):
    """
    Génère le livrable PDS (plan de soudure Excel) : un onglet par boîte BPE,
    rempli depuis les couches BPE / CABLES / BTS / PT du projet.
    Paramètres JSON : nb_fo (int), capacite_defaut (int), commentaire (str, optionnel).
    """
    from app.reporting import pds_generator

    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")

    try:
        params = await request.json()
    except Exception:
        params = {}

    def _entier(cle, defaut):
        try:
            return int(params.get(cle, defaut))
        except (TypeError, ValueError):
            return defaut

    nb_fo = max(1, _entier("nb_fo", 2))
    capacite_defaut = max(1, _entier("capacite_defaut", 48))
    commentaire = (params.get("commentaire") or "").strip() or None

    couches = db.query(models.CoucheSIG).filter(
        models.CoucheSIG.projet_id == projet_id
    ).all()

    def _couche_pref(nom):
        # Le PDS reflète les LIVRABLES : on privilégie la couche "[Livrable] X"
        # (données éditées dans la table livrable) ; repli sur l'entrée si absente.
        inp = liv = None
        for c in couches:
            up = c.nom.upper()
            if up == nom:
                inp = c
            elif up == f"[LIVRABLE] {nom}":
                liv = c
        return liv or inp

    # Source de vérité = les SHP LIVRABLES (dossier SHAPE) ; repli sur les couches.
    dossier_shape = _trouver_dossier_shape(projet)

    def _lire_livrable(nom_fichier, base):
        p = os.path.join(dossier_shape, nom_fichier)
        if os.path.exists(p):
            return gis_handler.lire_shapefile(p)
        c = _couche_pref(base)
        return gis_handler.lire_shapefile(c.chemin_fichier) if c else None

    try:
        bpe_gdf = _lire_livrable("BPE.shp", "BPE")
        if bpe_gdf is None or len(bpe_gdf) == 0:
            raise HTTPException(status_code=400,
                                detail="Couche BPE introuvable. Générez d'abord les SHP livrables (Créer SHP).")
        cab_gdf = _lire_livrable("CABLES.shp", "CABLES")
        bts_gdf = _lire_livrable("BTS.shp", "BTS")
        pt_gdf = _lire_livrable("PT.shp", "PT")

        ref_propre = (projet.reference or f"AFF_{projet.id}").replace("-", "_")
        dossier_apd = os.path.dirname(os.path.dirname(_chemins_livrables_apd(projet)))
        os.makedirs(dossier_apd, exist_ok=True)
        chemin_pds = os.path.join(dossier_apd, f"{ref_propre}_PDS.xlsx")

        chemin, nb = pds_generator.generer_pds(
            bpe_gdf, cab_gdf, bts_gdf, pt_gdf, chemin_pds,
            template_path=PDS_TEMPLATE, nb_fo=nb_fo,
            capacite_defaut=capacite_defaut, commentaire=commentaire,
        )
        crm_service.enregistrer_log(
            db=db, projet_id=projet.id, type_livrable="PDS",
            details=f"PDS genere ({nb} onglets, {nb_fo} FO/boite)"
        )
        db.commit()
        return JSONResponse({"message": f"PDS généré ({nb} onglet(s)).",
                             "nb_onglets": nb, "path": chemin})
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur génération PDS projet {projet_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projets/{projet_id}/couches/{couche_id}/propositions")
def api_propositions_nomenclature(projet_id: int, couche_id: int, db: Session = Depends(get_db)):
    """Propositions de valeurs conformes (NETGEO) pour les champs vides d'une couche
    SUPPORT / PT / CABLES. Lecture seule : ne modifie rien."""
    from app.gis import nomenclature

    couche = db.query(models.CoucheSIG).filter(
        models.CoucheSIG.id == couche_id, models.CoucheSIG.projet_id == projet_id
    ).first()
    if not couche:
        raise HTTPException(status_code=404, detail="Couche non trouvée")

    objet = couche.nom.upper().replace("[LIVRABLE]", "").strip()
    if objet not in ("SUPPORT", "PT", "CABLES"):
        return JSONResponse({"objet": objet, "nom": couche.nom, "propositions": []})
    try:
        gdf = gis_handler.lire_shapefile(couche.chemin_fichier)
        # Étude DOE FO : la date proposée (PT DATE_CREAT, câble POSE) = date TVX.
        projet = crm_service.obtenir_projet(db, projet_id)
        date_doe = None
        if projet and _type_etude_projet(projet).upper().startswith("DOE"):
            date_doe, _ = _doe_fo_params(projet, os.path.join(projet.chemin_dossier, "01_Inputs_SHP"))
            date_doe = date_doe or None
        props = nomenclature.proposer_couche(gdf, objet, date_str=date_doe)
        return JSONResponse({"objet": objet, "nom": couche.nom, "nb": len(props), "propositions": props})
    except Exception as e:
        logger.error(f"Erreur propositions couche #{couche_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/projets/{projet_id}/couches/{couche_id}/appliquer-propositions")
async def api_appliquer_propositions(projet_id: int, couche_id: int, request: Request,
                                     db: Session = Depends(get_db)):
    """Applique les propositions acceptées (liste {ligne, champ, valeur}) au shapefile."""
    couche = db.query(models.CoucheSIG).filter(
        models.CoucheSIG.id == couche_id, models.CoucheSIG.projet_id == projet_id
    ).first()
    if not couche:
        raise HTTPException(status_code=404, detail="Couche non trouvée")
    try:
        data = await request.json()
    except Exception:
        data = {}
    changements = data.get("propositions", [])

    num_champs = {"NB_TUBE", "CAPACITE", "LONGUEUR_R", "LGR_REEL", "ORDRE"}

    def _coerce(champ, val):
        if champ in num_champs:
            try:
                f = float(val)
                return int(f) if f == int(f) else f
            except (TypeError, ValueError):
                return val
        return val

    def _appliquer(path):
        """Applique les changements à un shapefile et le réécrit. Renvoie le nb appliqué."""
        g = gis_handler.lire_shapefile(path)
        k = 0
        for ch in changements:
            try:
                ligne = int(ch["ligne"]); champ = ch["champ"]; val = ch.get("valeur")
            except (KeyError, TypeError, ValueError):
                continue
            if val in (None, "") or champ not in g.columns or not (0 <= ligne < len(g)):
                continue
            g.at[g.index[ligne], champ] = _coerce(champ, val)
            k += 1
        if k:
            g.to_file(path, encoding="utf-8")
        return k

    try:
        n = _appliquer(couche.chemin_fichier)

        # Répercuter dans le SHP LIVRABLE (source de vérité PDS/KMZ) s'il existe.
        # Les indices `ligne` proviennent du fichier édité ; on ne les réutilise
        # sur le livrable QUE si l'alignement 1:1 tient encore (même nombre de
        # lignes = même ordre). Sinon (ex. lignes supprimées côté livrable), on
        # s'abstient pour ne pas écrire sur la mauvaise entité.
        projet = crm_service.obtenir_projet(db, projet_id)
        base = couche.nom.upper().replace("[LIVRABLE]", "").strip()
        if projet and base in ("CABLES", "SUPPORT", "PT", "BPE", "BTS"):
            p_liv = os.path.join(_trouver_dossier_shape(projet), f"{base}.shp")
            if os.path.exists(p_liv) and os.path.abspath(p_liv) != os.path.abspath(couche.chemin_fichier):
                try:
                    n_src = len(gis_handler.lire_shapefile(couche.chemin_fichier))
                    n_liv = len(gis_handler.lire_shapefile(p_liv))
                    if n_src == n_liv:
                        _appliquer(p_liv)
                    else:
                        logger.warning(
                            f"Répercussion nomenclature ignorée pour {base} : "
                            f"alignement rompu ({n_src} lignes source vs {n_liv} livrable)."
                        )
                except Exception as e:
                    logger.warning(f"Répercussion nomenclature au livrable {base} échouée : {e}")

        return JSONResponse({"message": f"{n} valeur(s) appliquée(s).", "nb": n})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur application propositions couche #{couche_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projets/{projet_id}/couches/{couche_id}/geojson")
def api_obtenir_geojson(projet_id: int, couche_id: int, db: Session = Depends(get_db)):
    """
    Retourne le contenu GeoJSON d'une couche pour affichage sur la carte Leaflet.
    La couche est automatiquement reprojetée en WGS84 (EPSG:4326).
    """
    couche = db.query(models.CoucheSIG).filter(
        models.CoucheSIG.id == couche_id,
        models.CoucheSIG.projet_id == projet_id
    ).first()

    if not couche:
        raise HTTPException(status_code=404, detail="Couche non trouvée")

    try:
        # La carte lit le même SHP LIVRABLE que la table d'édition / PDS / KMZ.
        projet = crm_service.obtenir_projet(db, projet_id)
        chemin = _resoudre_shp_livrable(projet, couche) if projet else couche.chemin_fichier
        gdf = gis_handler.lire_shapefile(chemin)
        geojson_str = gis_handler.convertir_en_geojson(gdf)

        # Symbologie NETGEO : on injecte un style par entité (mêmes couleurs/formes
        # que les plans et QGIS) pour que la carte du CRM affiche le vrai style.
        from app.gis import symbologie as symb
        base = couche.nom.upper().replace("[LIVRABLE]", "").strip()
        stylee = symb.est_stylee(base)
        # Seule la couche « propriétaire » (livrable si présent, sinon input)
        # porte les étiquettes, pour éviter les libellés dessinés en double.
        porte_etiq = _couche_porte_etiquettes(projet, couche) if projet else stylee
        if stylee:
            import json as _json
            gj = _json.loads(geojson_str)
            feats = gj.get("features", [])
            # Natures des travaux (annexes C6/C7) pour colorer les poteaux FT.
            natures = (_natures_appuis_projet(projet, _trouver_dossier_shape(projet))
                       if (projet and base == "PT") else {})
            for i, (_, row) in enumerate(gdf.iterrows()):
                if i < len(feats):
                    try:
                        nat = (natures.get(str(row.get("NOM") or "").strip())
                               if natures else None)
                        sty = symb.style_web(base, row, nat)
                        if sty and not porte_etiq:
                            sty.pop("lbl", None)
                            sty.pop("lc", None)
                        feats[i].setdefault("properties", {})["_sty"] = sty
                    except Exception:
                        pass
            geojson_str = _json.dumps(gj)

        return JSONResponse(content={"geojson": geojson_str, "couleur": couche.couleur,
                                     "stylee": stylee})
    except Exception as e:
        logger.error(f"Erreur GeoJSON couche #{couche_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projets/{projet_id}/couches/{couche_id}/attributs")
def api_obtenir_attributs(projet_id: int, couche_id: int, db: Session = Depends(get_db)):
    """
    Retourne la table attributaire d'une couche (sans la géométrie)
    sous forme de JSON pour affichage dans le tableau HTML.
    """
    couche = db.query(models.CoucheSIG).filter(
        models.CoucheSIG.id == couche_id,
        models.CoucheSIG.projet_id == projet_id
    ).first()

    if not couche:
        raise HTTPException(status_code=404, detail="Couche non trouvée")

    try:
        # Pour un livrable, on lit le SHP LIVRABLE (source de vérité) afin que la
        # table, la carte et les livrables PDS/KMZ montrent exactement la même chose.
        projet = crm_service.obtenir_projet(db, projet_id)
        chemin = _resoudre_shp_livrable(projet, couche) if projet else couche.chemin_fichier
        gdf = gis_handler.lire_shapefile(chemin)
        table = gis_handler.obtenir_table_attributaire(gdf)
        colonnes = [col for col in gdf.columns.tolist() if col != 'geometry']
        # BPE/PT existants exclus du DOE FO -> à griser/verrouiller dans l'Édition
        exclus = []
        base = (couche.nom or "").upper().replace("[LIVRABLE]", "").strip()
        if base in ("BPE", "PT") and projet:
            exclus = _doe_exclus_projet(projet).get(base, [])
        return JSONResponse(content={
            "nom_couche": couche.nom,
            "colonnes": colonnes,
            "lignes": table,
            "nb_total": len(table),
            "exclus": exclus,
        })
    except Exception as e:
        logger.error(f"Erreur attributs couche #{couche_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================================
# API REST : GENERATION DE LIVRABLES (PDF)
# =====================================================================

@app.post("/api/projets/{projet_id}/pdf")
def api_generer_pdf(projet_id: int, db: Session = Depends(get_db)):
    """
    Génère la fiche de synthèse PDF du projet et la place dans
    le sous-dossier 03_Livrables_PDF du projet.
    Retourne le fichier PDF en téléchargement direct.
    """
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")

    chemin_pdf_dir = os.path.join(projet.chemin_dossier, "03_Livrables_PDF")

    # Préparer les données des couches pour le PDF
    couches_data = [
        {"nom": c.nom, "type_geometrie": c.type_geometrie, "nb_entites": c.nb_entites}
        for c in projet.couches
    ]

    try:
        chemin_pdf = pdf_generator.generer_fiche_synthese(
            projet_id=projet.id,
            nom_projet=projet.nom,
            reference=projet.reference,
            statut=projet.statut,
            description=projet.description or "",
            couches=couches_data,
            chemin_sortie=chemin_pdf_dir
        )
        return FileResponse(
            chemin_pdf,
            media_type="application/pdf",
            filename=os.path.basename(chemin_pdf)
        )
    except Exception as e:
        logger.error(f"Erreur generation PDF projet #{projet_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def _couches_a_completer(projet, db, livrable=False):
    """[{id, nom, objet}] des couches SUPPORT/PT/CABLES (livrables si ``livrable``,
    sinon input) ayant AU MOINS une proposition de nomenclature (ex. PT
    ``DATE_CREAT`` aaaammjj non faite). Sert à ouvrir le modal de nomenclature à
    l'import ET après (re)génération des SHP livrables."""
    from app.gis import nomenclature
    from app import models
    out, vus = [], set()
    for c in db.query(models.CoucheSIG).filter(models.CoucheSIG.projet_id == projet.id).all():
        nom = c.nom or ""
        est_liv = nom.strip().lower().startswith("[livrable]")
        if est_liv != bool(livrable):
            continue
        base = nom.upper().replace("[LIVRABLE]", "").strip()
        if base not in ("SUPPORT", "PT", "CABLES") or base in vus:
            continue
        try:
            chemin = _resoudre_shp_livrable(projet, c) if est_liv else c.chemin_fichier
            gdf = gis_handler.lire_shapefile(chemin)
            if nomenclature.couche_a_completer(gdf, base):
                out.append({"id": c.id, "nom": c.nom, "objet": base})
                vus.add(base)
        except Exception:
            pass
    return out


def _generer_shapes_livrables(projet, db, mode="overwrite"):
    """Génère / actualise les SHP LIVRABLES (source de vérité) depuis les inputs.

    Modèle = gabarit **NETGEO** si l'étude est « DOE FO », sinon modèle **APD FO**.
    Enregistre les couches ``[Livrable]``, enrichit le PT depuis les annexes C6/C7,
    déverrouille les livrables. Renvoie ``(fichiers, dossier_sortie)``. Utilisé par
    le bouton carte « Créer/Régénérer SHP » et par « Dossier NETGEO »."""
    import geopandas as gpd
    from app import models
    est_doe = _type_etude_projet(projet).upper().startswith("DOE")
    modeles = None
    if est_doe:
        from app.reporting import doe_fo_generator as _dfo
        dossier_modele = DOE_FO_TEMPLATE_DIR
        modeles = _dfo.modeles_livrables(DOE_FO_TEMPLATE_DIR)
        if not modeles:
            raise FileNotFoundError(
                f"Gabarit DOE FO introuvable ou vide : {DOE_FO_TEMPLATE_DIR}. "
                "Définissez la variable d'environnement CRM_SIG_DOE_FO_TEMPLATE.")
    else:
        dossier_modele = MODELE_SHAPE_DIR
        if not os.path.isdir(dossier_modele):
            raise FileNotFoundError(
                f"Dossier modèle SHAPE introuvable : {dossier_modele}. "
                "Vérifiez son emplacement ou définissez CRM_SIG_MODELE_SHAPE.")
    dossier_input = os.path.join(projet.chemin_dossier, "01_Inputs_SHP")
    dossier_sortie = _chemins_livrables_apd(projet)   # …/APD_FO_*/APD_HTL_*/SHAPE
    fichiers = gis_handler.generer_livrables_shp(
        dossier_modele, dossier_input, dossier_sortie,
        overwrite=(mode == "overwrite"), modeles=modeles)

    for nom_fichier in fichiers:
        chemin_shp = os.path.join(dossier_sortie, nom_fichier)
        nom_couche = f"[Livrable] {os.path.splitext(nom_fichier)[0]}"
        couche_existante = db.query(models.CoucheSIG).filter(
            models.CoucheSIG.projet_id == projet.id,
            models.CoucheSIG.nom == nom_couche).first()
        gdf = gpd.read_file(chemin_shp)
        meta = gis_handler.extraire_metadonnees(gdf)
        if couche_existante:
            couche_existante.nb_entites = meta['nb_entites']
            couche_existante.chemin_fichier = chemin_shp
        else:
            db.add(models.CoucheSIG(
                nom=nom_couche, chemin_fichier=chemin_shp, projet_id=projet.id,
                type_geometrie=meta['types_geometrie'][0] if meta['types_geometrie'] else "Inconnu",
                nb_entites=meta['nb_entites'],
                systeme_projection=str(meta['crs']) if meta['crs'] else "4326",
                couleur="#ff0000"))
    db.commit()
    _enrichir_pt_depuis_annexes(projet, dossier_sortie)   # annexes C6/C7 -> PT
    _effacer_shapes_a_maj(projet)                          # déverrouille les livrables
    return fichiers, dossier_sortie


@app.post("/api/projets/{projet_id}/generer-etude/{type_etude}")
def api_generer_etude(projet_id: int, type_etude: str, mode: str = "overwrite", db: Session = Depends(get_db)):
    """
    Génère la structure de dossiers et fichiers pour les études demandées (DOE, FOE, PDS, SYNO, etc.).
    Créé les dossiers et fichiers mockés selon l'arborescence demandée.
    """
    from datetime import datetime
    
    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")

    # Format de date: YYMMDD
    date_str = datetime.utcnow().strftime("%y%m%d")
    # Identifiant affaire pour les noms de fichiers (ex: 21408_003 => on utilise la ref projet formatee)
    ref_propre = (projet.reference or f"AFF_{projet.id}").replace("-", "_")

    # Dossier de base pour les livrables
    base_doe_dir = os.path.join(projet.chemin_dossier, "04_Livrables_DOE")
    os.makedirs(base_doe_dir, exist_ok=True)

    is_doe_fo = type_etude.startswith("doe_fo_")
    
    if is_doe_fo:
        dossier_global = os.path.join(base_doe_dir, f"DOE_FO_{ref_propre}")
        os.makedirs(dossier_global, exist_ok=True)
        dossier_netgeo = os.path.join(dossier_global, f"DOE_NETGEO_{ref_propre}_02_{date_str}")
        dossier_carto = dossier_netgeo # Pour compatibilité potentielle
    else:
        dossier_global = os.path.join(base_doe_dir, f"APD_FO_{ref_propre}_{date_str}")
        os.makedirs(dossier_global, exist_ok=True)
        dossier_carto = os.path.join(dossier_global, f"APD_HTL_{ref_propre}_02_{date_str}")
    
    message = ""

    try:
        if type_etude == "doe_global":
            # Créer l'arborescence complète
            os.makedirs(os.path.join(dossier_carto, "SHAPE"), exist_ok=True)
            os.makedirs(os.path.join(dossier_carto, "KMZ"), exist_ok=True)
            
            # Fichiers mockés
            open(os.path.join(dossier_carto, "SHAPE", f"Cables_{ref_propre}.shp"), "a").close()
            open(os.path.join(dossier_carto, "SHAPE", f"Cables_{ref_propre}.dbf"), "a").close()
            open(os.path.join(dossier_carto, "SHAPE", f"Boitiers_{ref_propre}.shp"), "a").close()
            open(os.path.join(dossier_carto, "KMZ", f"Livrable_{ref_propre}.kmz"), "a").close()
            open(os.path.join(dossier_global, f"APD_HTL_{ref_propre}_02_{date_str}.pdf"), "a").close()
            open(os.path.join(dossier_global, f"PDS_{ref_propre}_{date_str}.xlsx"), "a").close()
            open(os.path.join(dossier_global, f"SYNO_{ref_propre}_{date_str}.pdf"), "a").close()
            
            message = "Arborescence globale DOE générée avec succès."

        elif type_etude == "rapport":
            # Plan APD (livrable global) : copie du template PowerPoint rempli
            # depuis les SHP livrables + la Console Étude, puis export PPTX -> PDF.
            from app.reporting import apd_generator, apd_pptx, plan_generator
            import json as _json, glob as _glob
            dossier_shape = _trouver_dossier_shape(projet)
            natures = _natures_appuis_projet(projet, dossier_shape)
            defauts = apd_generator.calculer_synthese(
                dossier_shape, ref_projet=(projet.reference or f"AFF_{projet.id}"),
                date_str=datetime.utcnow().strftime("%d/%m/%Y"), natures_appuis=natures)
            p_json = _chemin_console_json(projet)
            sauvegarde = {}
            if os.path.exists(p_json):
                try:
                    with open(p_json, "r", encoding="utf-8") as f:
                        sauvegarde = _json.load(f)
                except Exception:
                    sauvegarde = {}
            # Auto recalculé depuis le SHP livrable + saisies manuelles préservées.
            donnees = apd_generator.fusionner_console(defauts, sauvegarde)

            # Assets uploadés (plan de masse ; override éventuel du plan général)
            dossier_assets = os.path.join(projet.chemin_dossier, "02_Traitement", "apd_assets")

            def _asset(prefixe):
                for f in _glob.glob(os.path.join(dossier_assets, prefixe + ".*")):
                    if f.lower().endswith((".png", ".jpg", ".jpeg")):
                        return f
                return None

            plan_masse = _asset("plan_masse")
            plan_general = _asset("plan_general")
            if not plan_general:
                # Page 1 = plan APS/APD généré 100 % backend (fond Plan IGN +
                # symbologie NETGEO exacte), sans aucune dépendance QGIS.
                plan_general = os.path.join(dossier_global, f"plan_general_{ref_propre}.png")
                try:
                    plan_generator.generer_plan_apd(dossier_shape, plan_general, natures=natures)
                except Exception as e:
                    logger.warning(f"Plan général APD non généré ({e})")
                    plan_general = None

            if not os.path.exists(APD_PPTX_TEMPLATE):
                raise HTTPException(status_code=400,
                                    detail=f"Template PPTX APD introuvable : {APD_PPTX_TEMPLATE}")
            chemin_pdf = os.path.join(dossier_global, f"APD_HTL_{ref_propre}_02_{date_str}.pdf")
            apd_pptx.generer_apd_plan(APD_PPTX_TEMPLATE, donnees, dossier_shape, chemin_pdf,
                                      plan_general=plan_general, plan_masse=plan_masse)

            # 2ᵉ série de plans : folios A3 (vue d'ensemble + un folio par page),
            # fusionnés après les pages A4 -> un seul PDF APD.
            try:
                folios_shp = _folios_shp_projet(projet, dossier_shape)
                chemin_folios = os.path.join(dossier_global, f"folios_{ref_propre}.pdf")
                code_projet = (donnees.get("cartouche", {}) or {}).get("code_projet", "") \
                    or (projet.reference or "")
                plan_generator.generer_folios_apd(dossier_shape, chemin_folios,
                                                  folios_shp=folios_shp, natures=natures,
                                                  code_projet=code_projet,
                                                  opacite=_fond_opacite_projet(projet))
                if os.path.exists(chemin_folios):
                    import fitz as _fitz
                    doc = _fitz.open(chemin_pdf)
                    fol = _fitz.open(chemin_folios)
                    doc.insert_pdf(fol)
                    tmp = chemin_pdf + ".tmp"
                    doc.save(tmp)
                    doc.close(); fol.close()
                    os.replace(tmp, chemin_pdf)
            except Exception as e:
                logger.warning(f"Folios APD non fusionnés (projet {projet.id}) : {e}")

            # Recompression JPEG des fonds de carte (ortho lourde) : ~50 Mo -> ~18 Mo.
            try:
                plan_generator._compresser_fond(chemin_pdf)
            except Exception as e:
                logger.warning(f"Recompression APD ignorée (projet {projet.id}) : {e}")

            message = "Plan APD (PPTX → PDF) généré."
            
        elif type_etude == "pds":
            # Vrai PDS (plan de soudure) depuis les SHP livrables — plus de mock vide.
            chemin_pds = os.path.join(dossier_global, f"PDS_{ref_propre}_{date_str}.xlsx")
            _c, _nb = _pds_livrable(db, projet, chemin_pds)
            message = f"Plan de Câblage PDS généré ({_nb} onglet(s))."
            
        elif type_etude == "syno":
            # Plan Synoptique (design ENSIO) généré 100 % backend depuis les
            # SHP LIVRABLES (source de vérité) — sans dépendance QGIS.
            from app.reporting import plan_generator
            import glob as _glob
            dossier_shape = _trouver_dossier_shape(projet)
            if not (os.path.isdir(dossier_shape) and _glob.glob(os.path.join(dossier_shape, "*.shp"))):
                raise HTTPException(status_code=400,
                                    detail="Veuillez d'abord générer les SHP livrables (Créer SHP).")
            chemin_pdf = os.path.join(dossier_global, f"SYNO_{ref_propre}_{date_str}.pdf")
            plan_generator.generer_plan_syno(dossier_shape, chemin_pdf)
            message = "Plan Synoptique (PDF) généré depuis les SHP livrables."
            
        elif type_etude == "shape":
            fichiers, dossier_sortie = _generer_shapes_livrables(projet, db, mode)
            message = f"Cartographie SHAPE générée ({len(fichiers)} fichiers)."
            
        elif type_etude == "kmz":
            # Source = SHP LIVRABLES (dossier SHAPE existant le plus récent).
            import glob as _glob
            dossier_shape = _trouver_dossier_shape(projet)
            dest_kmz = os.path.join(dossier_carto, "KMZ", f"Livrable_{ref_propre}.kmz")

            if not (os.path.isdir(dossier_shape) and _glob.glob(os.path.join(dossier_shape, "*.shp"))):
                raise HTTPException(status_code=400, detail="Veuillez d'abord générer la cartographie SHAPE (Créer SHP).")

            os.makedirs(os.path.dirname(dest_kmz), exist_ok=True)
            gis_handler.generer_livrables_kmz(dossier_shape, dest_kmz)
            message = "Cartographie KMZ générée avec succès."
            
        elif type_etude == "doe_fo_global":
            # Création de l'arborescence complète DOE FO
            dossier_shapes = os.path.join(dossier_netgeo, "03.3_Shapes")
            os.makedirs(os.path.join(dossier_netgeo, "03.1_Synoptique_dwg"), exist_ok=True)
            os.makedirs(os.path.join(dossier_netgeo, "03.2_Plan_de_boite"), exist_ok=True)
            os.makedirs(dossier_shapes, exist_ok=True)
            
            # Fichiers mockés à la racine DOE
            open(os.path.join(dossier_global, f"DOE_VTL_{ref_propre}_{date_str}.pdf"), "a").close()
            open(os.path.join(dossier_global, f"DOE_SYNO_{ref_propre}_{date_str}.pdf"), "a").close()
            open(os.path.join(dossier_global, f"DOE_{ref_propre}_PDS_{date_str}.xlsx"), "a").close()
            open(os.path.join(dossier_global, f"DOE_HTL_{ref_propre}_02_{date_str}.pdf"), "a").close()
            open(os.path.join(dossier_global, f"DOE_{ref_propre}_{date_str}-1.kmz"), "a").close()
            
            message = "Arborescence globale DOE FO générée avec succès."

        elif type_etude == "doe_fo_vtl":
            open(os.path.join(dossier_global, f"DOE_VTL_{ref_propre}_{date_str}.pdf"), "a").close()
            message = "Rapport VTL (PDF) généré."

        elif type_etude == "doe_fo_htl":
            open(os.path.join(dossier_global, f"DOE_HTL_{ref_propre}_02_{date_str}.pdf"), "a").close()
            message = "Rapport HTL (PDF) généré."

        elif type_etude == "doe_fo_syno":
            open(os.path.join(dossier_global, f"DOE_SYNO_{ref_propre}_{date_str}.pdf"), "a").close()
            message = "Plan Synoptique (PDF) généré."

        elif type_etude == "doe_fo_pds":
            open(os.path.join(dossier_global, f"DOE_{ref_propre}_PDS_{date_str}.xlsx"), "a").close()
            message = "Plan de Câblage / Boîte (XLSX) généré."

        elif type_etude == "doe_fo_netgeo":
            # Bouton « Dossier NETGEO (SHP/KMZ/DWG) » : point d'entrée UNIQUE de
            # génération/MAJ des données d'une étude DOE FO.
            from app.reporting import doe_fo_generator as dfo
            import glob as _glob
            dossier_input = os.path.join(projet.chemin_dossier, "01_Inputs_SHP")
            if not _glob.glob(os.path.join(dossier_input, "*.shp")):
                raise HTTPException(status_code=400,
                                    detail="Aucun SHP d'entrée : importez d'abord les couches du projet.")
            if not os.path.isdir(DOE_FO_TEMPLATE_DIR):
                raise HTTPException(status_code=400,
                                    detail=f"Gabarit DOE FO introuvable : {DOE_FO_TEMPLATE_DIR}")
            date_tvx, fci = _doe_fo_params(projet, dossier_input)
            # La date TVX est OBLIGATOIRE : elle renseigne DATE_DE_CR / POSE /
            # DATE_CREAT (aaaammjj) de TOUS les objets. Sans elle, le remplissage
            # automatique n'a pas lieu et les SHP gardent le placeholder « AAAAMMJJ »
            # (d'où le passage forcé par le modal de nomenclature). On la rend requise.
            if not date_tvx:
                raise HTTPException(
                    status_code=400,
                    detail="Date TVX manquante : saisissez la date TVX dans la Console "
                           "DOE FO (ou importez le fichier AAAAMMJJ-DATETVX.txt) avant de "
                           "générer. Elle remplit automatiquement DATE_DE_CR / POSE / "
                           "DATE_CREAT (aaaammjj) sur tous les SHP livrables.")

            # 1) SHP LIVRABLES (schéma NETGEO) — source de vérité. On NE force PAS
            #    l'écrasement (overwrite=False) : les livrables existants (et leurs
            #    éventuelles corrections manuelles) sont conservés, les manquants créés.
            fichiers_liv, _ = _generer_shapes_livrables(projet, db, "keep")

            # 2) Livrable NETGEO (arbre 03.3_Shapes) + arborescence dossier DOE
            dossier_shapes = os.path.join(dossier_netgeo, "03.3_Shapes")
            os.makedirs(os.path.join(dossier_netgeo, "03.1_Synoptique_dwg"), exist_ok=True)
            os.makedirs(os.path.join(dossier_netgeo, "03.2_Plan_de_boite"), exist_ok=True)
            os.makedirs(dossier_shapes, exist_ok=True)
            resume, exclus = dfo.generer_doe_netgeo(
                dossier_input, DOE_FO_TEMPLATE_DIR, dossier_shapes,
                date_tvx=date_tvx, fci_par_cable=fci)
            _sauver_doe_exclus(projet, exclus)   # BPE/PT existants -> grisés en Édition

            # 3) Répercussion des champs DOE (dates TVX / FCI) dans les SHP livrables
            nb_liv = _maj_shp_livrables_doe(projet, date_tvx, fci, exclus)

            # 4) KMZ (réel) depuis les livrables (après répercussion des champs)
            kmz_ok = False
            try:
                dossier_shape_liv = _trouver_dossier_shape(projet)
                if dossier_shape_liv and _glob.glob(os.path.join(dossier_shape_liv, "*.shp")):
                    dest_kmz = os.path.join(dossier_netgeo, "KMZ", f"DOE_{ref_propre}_{date_str}.kmz")
                    os.makedirs(os.path.dirname(dest_kmz), exist_ok=True)
                    gis_handler.generer_livrables_kmz(dossier_shape_liv, dest_kmz)
                    kmz_ok = True
            except Exception as e:
                logger.warning(f"KMZ DOE FO (projet {projet.id}) : {e}")

            nb = sum(v.get("nb", 0) for v in resume.values())
            message = (f"Dossier NETGEO généré : {len(fichiers_liv)} SHP livrables"
                       + (" + KMZ" if kmz_ok else "")
                       + f" ; {nb} entités NETGEO ({len(exclus['BPE'])} BPE + "
                       f"{len(exclus['PT'])} PT existants exclus) ; date TVX "
                       f"{date_tvx or '—'} ; {nb_liv} couche(s) livrable(s) à jour "
                       f"(DWG : dossier prêt, export manuel).")
            
        else:
            raise HTTPException(status_code=400, detail="Type d'étude inconnu")

        # Enregistrer dans le journal d'activité
        crm_service.enregistrer_log(
            db=db,
            projet_id=projet.id,
            type_livrable=type_etude.upper(),
            details=message
        )

        reponse = {"message": message, "path": dossier_global}
        # Après (re)génération des SHP livrables : si la nomenclature d'une couche
        # livrable reste à compléter (ex. PT DATE_CREAT aaaammjj), on le signale
        # pour rouvrir le modal de nomenclature côté carte.
        if type_etude in ("shape", "doe_fo_netgeo"):
            try:
                reponse["couches_a_completer"] = _couches_a_completer(projet, db, livrable=True)
            except Exception:
                reponse["couches_a_completer"] = []
        # URL directe du livrable généré pour ouverture/téléchargement immédiat
        # côté navigateur (projects_data est servi sous /static).
        def _url_statique(chemin):
            try:
                rel = os.path.relpath(chemin, STATIC_DIR).replace("\\", "/")
                return "/static/" + rel if not rel.startswith("..") else None
            except Exception:
                return None
        if type_etude in ("syno", "rapport"):
            u = _url_statique(chemin_pdf)
            if u:
                reponse["pdf_url"] = u
        elif type_etude == "kmz":
            u = _url_statique(dest_kmz)
            if u:
                reponse["kmz_url"] = u
        return JSONResponse(reponse)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur generation etude {type_etude}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/projets/{projet_id}/couches/{couche_id}/sauvegarder-attributs")
async def api_sauvegarder_attributs(request: Request, projet_id: int, couche_id: int, db: Session = Depends(get_db)):
    """
    Sauvegarde directement les attributs.
    Si c'est un livrable, on l'écrase.
    Si c'est un input, on crée un nouveau Livrable dans 02_Traitement pour protéger l'input.
    """
    couche = db.query(models.CoucheSIG).filter(
        models.CoucheSIG.id == couche_id,
        models.CoucheSIG.projet_id == projet_id
    ).first()
    
    if not couche:
        raise HTTPException(status_code=404, detail="Couche non trouvée")
        
    try:
        nouvelles_donnees = await request.json()
        
        if "[Livrable]" in couche.nom:
            # On écrase directement le SHP LIVRABLE (04_.../SHAPE) — source de vérité.
            projet = crm_service.obtenir_projet(db, projet_id)
            chemin = _chemin_shp_edition(db, projet, couche) if projet else couche.chemin_fichier
            gis_handler.sauvegarder_attributs(chemin, chemin, nouvelles_donnees)
            couche.nb_entites = len(nouvelles_donnees)
            db.commit()
            _marquer_shapes_a_maj(projet)  # Édition livrable -> MAJ Shapes requise
            return JSONResponse({"message": "Attributs du livrable sauvegardés avec succès."})
        else:
            # C'est un input ! On sauvegarde dans le dossier Traitement
            projet = crm_service.obtenir_projet(db, projet_id)
            dossier_traitement = os.path.join(projet.chemin_dossier, "02_Traitement")
            nom_base = os.path.basename(couche.chemin_fichier)
            chemin_destination = os.path.join(dossier_traitement, nom_base)
            
            gis_handler.sauvegarder_attributs(couche.chemin_fichier, chemin_destination, nouvelles_donnees)
            
            # Créer la nouvelle couche Livrable dans la BDD
            nom_couche = f"[Livrable] {os.path.splitext(nom_base)[0]}"
            couche_existante = db.query(models.CoucheSIG).filter(
                models.CoucheSIG.projet_id == projet.id,
                models.CoucheSIG.nom == nom_couche
            ).first()
            
            if not couche_existante:
                import geopandas as gpd
                gdf = gpd.read_file(chemin_destination)
                meta = gis_handler.extraire_metadonnees(gdf)
                nouvelle_couche = models.CoucheSIG(
                    nom=nom_couche,
                    chemin_fichier=chemin_destination,
                    projet_id=projet.id,
                    type_geometrie=meta['types_geometrie'][0] if meta['types_geometrie'] else "Inconnu",
                    nb_entites=meta['nb_entites'],
                    systeme_projection=str(meta['crs']) if meta['crs'] else "4326",
                    couleur="#ff0000" # Rouge
                )
                db.add(nouvelle_couche)
                db.commit()
                nouvelle_couche_id = nouvelle_couche.id
            else:
                couche_existante.chemin_fichier = chemin_destination
                db.commit()
                nouvelle_couche_id = couche_existante.id

            _marquer_shapes_a_maj(projet)  # Édition livrable -> MAJ Shapes requise
            return JSONResponse({"message": "Modifications enregistrées en tant que nouveau Livrable !", "nouvelle_couche_id": nouvelle_couche_id})
            
    except Exception as e:
        logger.error(f"Erreur sauvegarde attributs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/projets/{projet_id}/couches/{couche_id}/entites/{ligne}")
def api_supprimer_entite(projet_id: int, couche_id: int, ligne: int,
                         db: Session = Depends(get_db)):
    """
    Supprime définitivement une entité (ligne complète : géométrie + attributs)
    du SHP LIVRABLE de la couche. La suppression est écrite dans le livrable
    (04_.../SHAPE) — source de vérité pour PDS/KMZ.
    """
    couche = db.query(models.CoucheSIG).filter(
        models.CoucheSIG.id == couche_id,
        models.CoucheSIG.projet_id == projet_id
    ).first()
    if not couche:
        raise HTTPException(status_code=404, detail="Couche non trouvée")

    # La suppression ne s'applique qu'aux couches LIVRABLES (les inputs restent intacts).
    if not couche.nom or "[Livrable]" not in couche.nom:
        raise HTTPException(status_code=400,
                            detail="Suppression réservée aux couches livrables (les inputs sont protégés).")

    projet = crm_service.obtenir_projet(db, projet_id)
    if not projet:
        raise HTTPException(status_code=404, detail="Projet non trouvé")

    chemin = _chemin_shp_edition(db, projet, couche)
    try:
        gdf = gis_handler.lire_shapefile(chemin)
        if ligne < 0 or ligne >= len(gdf):
            raise HTTPException(status_code=400,
                                detail=f"Ligne {ligne} hors bornes (0..{len(gdf) - 1}).")

        gdf = gdf.drop(gdf.index[ligne]).reset_index(drop=True)
        gdf.to_file(chemin, encoding="utf-8")

        couche.nb_entites = len(gdf)
        db.commit()
        crm_service.enregistrer_log(
            db=db, projet_id=projet.id, type_livrable="EDITION",
            details=f"Entite supprimee de {couche.nom} (reste {len(gdf)})"
        )
        db.commit()
        _marquer_shapes_a_maj(projet)  # Édition livrable -> MAJ Shapes requise
        return JSONResponse({"message": "Entité supprimée du livrable.",
                             "nb_restant": len(gdf)})
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur suppression entité couche #{couche_id} ligne {ligne}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

