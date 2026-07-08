"""
doe_fo_generator.py — Génération du livrable DOE FO « NETGEO ».

Principe
--------
Le gabarit ``COUCHE TEMPLATE DOE FO`` (7 couches vides : 01-BPE … 07-NRO, avec
leur ``.prj``) est **copié** dans le dossier livrable ``…/DOE_NETGEO`` du projet,
puis **rempli** depuis les SHP d'entrée (schéma conforme au gabarit), en
appliquant les règles métier du DOE (dossier des ouvrages exécutés = l'existant
neuf, passé « en service ») :

  · BPE     : les BPE ``EN SERVICE`` (déjà existants) sont EXCLUS ; les BPE
              ``EN ETUDE`` (le neuf) sont conservés et passés ``EN SERVICE`` ;
              ``DATE_DE_CR`` = date TVX.
  · PT      : les PT qui INTERSECTENT un BPE existant (co-localisés) sont EXCLUS ;
              ``DATE_CREAT`` = date TVX pour les PT FREE (logique nomenclature).
  · CABLE   : ``POSE`` = date TVX ; ``FCI`` renseigné par câble (Console).
  · SUPPORT : ``DATE_CONST`` = date TVX ; champs hors gabarit retirés.
  · NRO/NRA/BTS : copie conforme au schéma.

La date TVX est au format **aaaammjj** (ex. ``20260407``), issue du nom du
fichier ``AAAAMMJJ-DATETVX.txt``.

Renvoie ``{sous_dossier: {"nb": n, "exclus": k}}`` et la liste des NOM de BPE/PT
exclus (pour l'affichage « grisé/verrouillé » de l'Édition Livrables).
"""

import os
import glob
import shutil
import logging

logger = logging.getLogger("crm_sig.doe_fo")

# gabarit : sous-dossier -> nom de couche source (input)
MAPPING = [
    ("01-BPE", "BPE"),
    ("02-BTS", "BTS"),
    ("03-CABLE", "CABLES"),
    ("04-NRA", "NRA"),
    ("05-PT", "PT"),
    ("06-SUPPORT", "SUPPORT"),
    ("07-NRO", "NRO_RIP"),
]

_EXTS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix")
_TOL_M = 2.0   # tolérance d'intersection BPE existant ↔ PT (co-localisation)


def _shp_de(dossier, sous=None):
    base = os.path.join(dossier, sous) if sous else dossier
    l = glob.glob(os.path.join(base, "*.shp"))
    return l[0] if l else None


def _est_en_service(v):
    return "SERVICE" in str(v or "").upper()


def _date_tvx_depuis_txt(chemin_txt):
    """Date TVX (aaaammjj) depuis le NOM du fichier ``AAAAMMJJ-DATETVX.txt``
    (le contenu est généralement vide). Renvoie '' si introuvable."""
    import re
    if not chemin_txt:
        return ""
    m = re.search(r"(\d{8})", os.path.basename(chemin_txt))
    return m.group(1) if m else ""


def generer_doe_netgeo(dossier_input, dossier_template, dossier_sortie,
                       date_tvx="", fci_par_cable=None):
    """Génère le DOE FO NETGEO. ``dossier_sortie`` = …/DOE_NETGEO.

    Renvoie ``(resume, exclus)`` où ``resume={sous:{'nb':n}}`` et
    ``exclus={'BPE':[nom…], 'PT':[nom…]}`` (éléments existants non générés)."""
    import geopandas as gpd
    fci_par_cable = fci_par_cable or {}
    date_tvx = str(date_tvx or "").strip()
    os.makedirs(dossier_sortie, exist_ok=True)

    # 1) BPE existants (EN SERVICE) + PT qu'ils intersectent (à exclure)
    bpe_in = _lire(dossier_input, "BPE")
    pt_in = _lire(dossier_input, "PT")
    pt_exclus_idx, bpe_exclus_noms, pt_exclus_noms = set(), [], []
    if bpe_in is not None and len(bpe_in):
        for _, b in bpe_in.iterrows():
            if not _est_en_service(b.get("ETAT")):
                continue
            bpe_exclus_noms.append(str(b.get("NOM") or b.get("CODE") or ""))
            if pt_in is not None and len(pt_in) and b.geometry is not None:
                try:
                    d = pt_in.geometry.distance(b.geometry)
                    j = d.idxmin()
                    if float(d[j]) <= _TOL_M and j not in pt_exclus_idx:
                        pt_exclus_idx.add(j)
                        pt_exclus_noms.append(str(pt_in.loc[j].get("NOM")
                                                  or pt_in.loc[j].get("CODE") or ""))
                except Exception:
                    pass

    resume = {}
    for sous, nom_in in MAPPING:
        tpl = _shp_de(dossier_template, sous)
        if tpl is None:
            continue
        dest_dir = os.path.join(dossier_sortie, sous)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, os.path.basename(tpl))

        gdf_tpl = gpd.read_file(tpl)                       # gabarit vide -> schéma cible
        champs = [c for c in gdf_tpl.columns if c != "geometry"]
        crs_cible = gdf_tpl.crs

        gdf_in = _lire(dossier_input, nom_in)
        if gdf_in is None or len(gdf_in) == 0:
            _copier_gabarit(tpl, dest)                     # couche vide : gabarit tel quel
            resume[sous] = {"nb": 0}
            continue

        g = gdf_in.copy()

        # ---- règles par couche ----
        if nom_in == "BPE":
            g = g[~g["ETAT"].map(_est_en_service)].copy()  # exclure les existants
            g["ETAT"] = "EN SERVICE"                        # le neuf passe en service
            if "DATE_DE_CR" in g.columns and date_tvx:
                g["DATE_DE_CR"] = date_tvx
        elif nom_in == "PT":
            g = g[~g.index.isin(pt_exclus_idx)].copy()      # exclure PT des BPE existants
            if "DATE_CREAT" in g.columns and date_tvx:
                free = g.get("PROPRIETAI").map(lambda v: "FREE" in str(v or "").upper()) \
                    if "PROPRIETAI" in g.columns else None
                if free is not None:
                    g.loc[free, "DATE_CREAT"] = date_tvx    # nomenclature : PT FREE
                else:
                    g["DATE_CREAT"] = date_tvx
        elif nom_in == "CABLES":
            if "POSE" in g.columns and date_tvx:
                g["POSE"] = date_tvx
            if "FCI" in g.columns and fci_par_cable:
                g["FCI"] = g["NOM"].map(lambda n: fci_par_cable.get(str(n), None)) \
                    if "NOM" in g.columns else g["FCI"]
        elif nom_in == "SUPPORT":
            if "DATE_CONST" in g.columns and date_tvx:
                g["DATE_CONST"] = date_tvx

        # ---- conformer au schéma cible (champs + ordre + CRS + .prj) ----
        for c in champs:
            if c not in g.columns:
                g[c] = None
        g = g[champs + ["geometry"]]
        if crs_cible is not None:
            try:
                g = g.set_crs(crs_cible, allow_override=True)
            except Exception:
                pass
        g.to_file(dest, encoding="utf-8")
        _copier_prj(tpl, dest)                              # « bien réutiliser le .prj »
        resume[sous] = {"nb": int(len(g))}

    exclus = {"BPE": bpe_exclus_noms, "PT": pt_exclus_noms}
    logger.info(f"DOE NETGEO : {resume} | exclus BPE={len(bpe_exclus_noms)} "
                f"PT={len(pt_exclus_noms)} (date TVX={date_tvx or '—'})")
    return resume, exclus


# ---------------------------------------------------------------------------

def _lire(dossier, nom):
    import geopandas as gpd
    p = os.path.join(dossier, f"{nom}.shp")
    if not os.path.exists(p):
        return None
    try:
        return gpd.read_file(p)
    except Exception as e:
        logger.warning(f"DOE NETGEO : lecture {nom} impossible ({e})")
        return None


def _copier_gabarit(src_shp, dest_shp):
    base_src = os.path.splitext(src_shp)[0]
    base_dst = os.path.splitext(dest_shp)[0]
    for ext in _EXTS:
        if os.path.exists(base_src + ext):
            try:
                shutil.copy2(base_src + ext, base_dst + ext)
            except Exception:
                pass


def _copier_prj(src_shp, dest_shp):
    ps, pd_ = os.path.splitext(src_shp)[0] + ".prj", os.path.splitext(dest_shp)[0] + ".prj"
    if os.path.exists(ps):
        try:
            shutil.copy2(ps, pd_)
        except Exception:
            pass
