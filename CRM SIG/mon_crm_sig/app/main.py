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
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "projets": projets,
        "clients": clients,
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
        defauts = apd_generator.calculer_synthese(dossier_shape, ref_projet=ref, date_str=date_str)
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
    db: Session = Depends(get_db)
):
    """Crée un nouveau projet et redirige vers le tableau de bord."""
    try:
        cid = int(client_id) if client_id else None
        crm_service.creer_projet(db, nom=nom, description=description, client_id=cid)
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
    for fichier in fichiers:
        chemin_dest = os.path.join(dossier_input, fichier.filename)
        with open(chemin_dest, "wb") as f:
            contenu = await fichier.read()
            f.write(contenu)
        logger.info(f"Fichier sauvegardé : {chemin_dest}")

        # Collecter tous les .shp trouvés
        if fichier.filename.lower().endswith(".shp"):
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

            # Attribuer une couleur différente à chaque couche
            couleur_index = (couches_existantes + nb_couches_ok) % len(COULEURS)

            nouvelle_couche = models.CoucheSIG(
                nom=os.path.splitext(os.path.basename(chemin_shp))[0],
                type_geometrie=metadonnees["types_geometrie"][0] if metadonnees["types_geometrie"] else "Inconnu",
                chemin_fichier=chemin_shp,
                systeme_projection=str(metadonnees["crs"]) if metadonnees["crs"] else "4326",
                nb_entites=metadonnees["nb_entites"],
                couleur=COULEURS[couleur_index],
                projet_id=projet_id
            )
            db.add(nouvelle_couche)
            db.commit()
            nb_couches_ok += 1
            logger.info(f"Couche SIG enregistrée : {nouvelle_couche.nom} "
                        f"({metadonnees['nb_entites']} entités, couleur={COULEURS[couleur_index]})")

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

    # Couches SUPPORT/PT/CABLES avec des NOM/LIBELLE vides -> propositions de nomenclature
    from app.gis import nomenclature
    couches_a_completer = []
    objets_vus = set()
    for c in couches:
        base = c.nom.upper()
        if (base.startswith("[LIVRABLE]") or base not in ("SUPPORT", "PT", "CABLES")
                or base in objets_vus):
            continue
        try:
            gdf = gis_handler.lire_shapefile(c.chemin_fichier)
            if nomenclature.couche_a_completer(gdf, base):
                couches_a_completer.append({"id": c.id, "nom": c.nom, "objet": base})
                objets_vus.add(base)
        except Exception:
            pass

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
    """Dossier SHAPE des livrables APD FO (même convention que la génération 'shape')."""
    from datetime import datetime
    date_str = datetime.utcnow().strftime("%y%m%d")
    ref_propre = (projet.reference or f"AFF_{projet.id}").replace("-", "_")
    base_doe = os.path.join(projet.chemin_dossier, "04_Livrables_DOE")
    dossier_global = os.path.join(base_doe, f"APD_FO_{ref_propre}_{date_str}")
    dossier_carto = os.path.join(dossier_global, f"APD_HTL_{ref_propre}_02_{date_str}")
    return os.path.join(dossier_carto, "SHAPE")


def _trouver_dossier_shape(projet) -> str:
    """Dossier SHAPE des livrables : renvoie le dossier EXISTANT le plus récent
    (source de vérité pour PDS/KMZ), sinon le chemin par défaut (date du jour)."""
    import glob
    base_doe = os.path.join(projet.chemin_dossier, "04_Livrables_DOE")
    cands = [c for c in glob.glob(os.path.join(base_doe, "APD_FO_*", "APD_HTL_*", "SHAPE"))
             if os.path.isdir(c)]
    if cands:
        return max(cands, key=os.path.getmtime)
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
        props = nomenclature.proposer_couche(gdf, objet)
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
            for i, (_, row) in enumerate(gdf.iterrows()):
                if i < len(feats):
                    try:
                        sty = symb.style_web(base, row)
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
        return JSONResponse(content={
            "nom_couche": couche.nom,
            "colonnes": colonnes,
            "lignes": table,
            "nb_total": len(table)
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
    ref_propre = projet.reference.replace("-", "_")

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
            defauts = apd_generator.calculer_synthese(
                dossier_shape, ref_projet=(projet.reference or f"AFF_{projet.id}"),
                date_str=datetime.utcnow().strftime("%d/%m/%Y"))
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
                    plan_generator.generer_plan_apd(dossier_shape, plan_general)
                except Exception as e:
                    logger.warning(f"Plan général APD non généré ({e})")
                    plan_general = None

            if not os.path.exists(APD_PPTX_TEMPLATE):
                raise HTTPException(status_code=400,
                                    detail=f"Template PPTX APD introuvable : {APD_PPTX_TEMPLATE}")
            chemin_pdf = os.path.join(dossier_global, f"APD_HTL_{ref_propre}_02_{date_str}.pdf")
            apd_pptx.generer_apd_plan(APD_PPTX_TEMPLATE, donnees, dossier_shape, chemin_pdf,
                                      plan_general=plan_general, plan_masse=plan_masse)
            message = "Plan APD (PPTX → PDF) généré."
            
        elif type_etude == "pds":
            open(os.path.join(dossier_global, f"PDS_{ref_propre}_{date_str}.xlsx"), "a").close()
            message = "Plan de Câblage (XLSX) généré."
            
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
            dossier_modele = MODELE_SHAPE_DIR
            if not os.path.isdir(dossier_modele):
                raise FileNotFoundError(
                    f"Dossier modèle SHAPE introuvable : {dossier_modele}. "
                    "Vérifiez son emplacement ou définissez la variable "
                    "d'environnement CRM_SIG_MODELE_SHAPE."
                )
            dossier_input = os.path.join(projet.chemin_dossier, "01_Inputs_SHP")
            dossier_sortie = os.path.join(dossier_carto, "SHAPE")
            fichiers = gis_handler.generer_livrables_shp(
                dossier_modele, dossier_input, dossier_sortie, overwrite=(mode == "overwrite")
            )
            
            # Enregistrer les livrables générés comme de nouvelles couches dans la BDD
            import geopandas as gpd
            from app import models
            for nom_fichier in fichiers:
                chemin_shp = os.path.join(dossier_sortie, nom_fichier)
                nom_couche = f"[Livrable] {os.path.splitext(nom_fichier)[0]}"
                
                # Vérifier si elle existe déjà pour l'écraser
                couche_existante = db.query(models.CoucheSIG).filter(
                    models.CoucheSIG.projet_id == projet.id,
                    models.CoucheSIG.nom == nom_couche
                ).first()
                
                gdf = gpd.read_file(chemin_shp)
                meta = gis_handler.extraire_metadonnees(gdf)
                
                if couche_existante:
                    couche_existante.nb_entites = meta['nb_entites']
                    # Recaler le pointeur sur le dossier livrable courant (évite
                    # que la carte lise un ancien dossier daté après régénération).
                    couche_existante.chemin_fichier = chemin_shp
                else:
                    nouvelle_couche = models.CoucheSIG(
                        nom=nom_couche,
                        chemin_fichier=chemin_shp,
                        projet_id=projet.id,
                        type_geometrie=meta['types_geometrie'][0] if meta['types_geometrie'] else "Inconnu",
                        nb_entites=meta['nb_entites'],
                        systeme_projection=str(meta['crs']) if meta['crs'] else "4326",
                        couleur="#ff0000" # Rouge pour les livrables
                    )
                    db.add(nouvelle_couche)
            db.commit()
            _effacer_shapes_a_maj(projet)  # Shapes à jour -> déverrouille les livrables

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
            dossier_shapes = os.path.join(dossier_netgeo, "03.3_Shapes")
            os.makedirs(os.path.join(dossier_netgeo, "03.1_Synoptique_dwg"), exist_ok=True)
            os.makedirs(os.path.join(dossier_netgeo, "03.2_Plan_de_boite"), exist_ok=True)
            os.makedirs(dossier_shapes, exist_ok=True)
            open(os.path.join(dossier_global, f"DOE_{ref_propre}_{date_str}-1.kmz"), "a").close()
            message = "Dossier NETGEO (SHP, DWG, KMZ) généré."
            
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

