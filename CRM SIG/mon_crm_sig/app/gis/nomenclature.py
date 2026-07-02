"""
nomenclature.py - Propositions de valeurs conformes NETGEO pour les champs vides.

À l'import d'un projet, lorsqu'un SUPPORT / PT / CÂBLE a un NOM/CODE vide ou des
champs vides, ce module PROPOSE des valeurs conformes à la nomenclature client
(cf. NOMENCLATURE.md). Il ne modifie rien : il retourne une liste de propositions
que l'utilisateur valide.

Module pur (pas de web ni de BDD).
"""

import re
import math
import logging
import unicodedata
from collections import Counter
from datetime import datetime

logger = logging.getLogger("crm_sig.nomenclature")

# Correspondance capacité (FO) -> nombre de tubes (12 FO / tube)
def _nb_tubes(capa):
    try:
        return max(1, math.ceil(int(float(capa)) / 12))
    except (TypeError, ValueError):
        return None

PREFIXE_SUPPORT = {"TRANCHEE": "GEC", "AERIEN": "AER", "FACADE": "FAC", "FORAGE": "GEC"}


def normaliser_texte(v):
    """MAJUSCULES, sans accent (diacritiques), sans apostrophe, sans espace/CR en début/fin.
    On retire uniquement les diacritiques (é→E), pas les symboles techniques (ex. Ø)."""
    if v is None:
        return None
    s = str(v)
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = s.replace("'", "").replace("’", "")
    s = s.replace("\r", " ").replace("\n", " ").strip()
    return s.upper()


def _vide(v):
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return str(v).strip().lower() in ("", "none", "nan")


def _nra_du_projet(gdf):
    """Déduit l'identifiant NRA/EMPRISE (ex. 'EUR68') depuis EMPRISE ou les NOM existants."""
    if "EMPRISE" in gdf.columns:
        for v in gdf["EMPRISE"].dropna():
            s = str(v).strip()
            if s:
                return s.rsplit("_", 1)[0] if "_" in s else s  # EUR68_001 -> EUR68
    for col in ("NOM", "CODE", "LIBELLE"):
        if col in gdf.columns:
            for v in gdf[col].dropna():
                parts = str(v).split("_")
                for p in parts:
                    if p.upper().startswith(("NRA", "EUR", "ARC", "PLA")) and any(ch.isdigit() for ch in p):
                        return p.upper()
    return "NRAXX"


def _insee_du_projet(gdf):
    """Détecte le code INSEE (5 chiffres) le plus fréquent dans les NOM/CODE existants
    (ex. 'FT_68218_50895' -> 68218) pour proposer des noms cohérents avec la couche."""
    codes = Counter()
    for col in ("NOM", "CODE"):
        if col in gdf.columns:
            for v in gdf[col].dropna():
                for m in re.findall(r"(?<!\d)(\d{5})(?!\d)", str(v)):
                    codes[m] += 1
    return codes.most_common(1)[0][0] if codes else None


def _emprise(nra):
    return f"{nra}_001"


def _prop(ligne, champ, ancienne, proposee, raison, auto=True):
    return {"ligne": int(ligne), "champ": champ,
            "ancienne": None if ancienne is None else str(ancienne),
            "proposee": None if proposee is None else str(proposee),
            "raison": raison, "auto": bool(auto)}


def proposer_couche(gdf, objet, date_str=None):
    """
    Retourne la liste des propositions {ligne, champ, ancienne, proposee, raison, auto}
    pour la couche `gdf` de type `objet` (SUPPORT / PT / CABLES).
    `auto=True` = correction sûre ; `auto=False` = suggestion à valider.
    """
    objet = (objet or "").upper().replace("[LIVRABLE]", "").strip()
    date_str = date_str or datetime.utcnow().strftime("%y%m%d")
    props = []
    if gdf is None or len(gdf) == 0:
        return props
    nra = _nra_du_projet(gdf)
    insee = _insee_du_projet(gdf)
    emp = _emprise(nra)
    cols = set(gdf.columns)

    # Normalisation des champs texte existants (MAJUSCULE/accents/trim)
    champs_texte = [c for c in cols if c not in ("geometry", "LONGUEUR_R", "LGR_REEL",
                                                 "NB_TUBE", "CAPACITE", "ORDRE")]
    for i, (_, r) in enumerate(gdf.iterrows()):
        for c in champs_texte:
            v = r.get(c)
            if not _vide(v):
                n = normaliser_texte(v)
                if n != str(v):
                    props.append(_prop(i, c, v, n, "Normalisation (MAJUSCULE, sans accent/apostrophe, trim)"))

    # Compteurs pour les nommages incrémentés (par type)
    compteurs = {}

    def _incr(cle):
        compteurs[cle] = compteurs.get(cle, 0) + 1
        return compteurs[cle]

    for i, (_, r) in enumerate(gdf.iterrows()):
        geom = r.geometry

        if objet == "CABLES":
            capa = r.get("CAPACITE")
            if "NOM" in cols and _vide(r.get("NOM")):
                num = _incr("CTR")
                nom = f"CTR_{nra}_001_{num:02d}"
                props.append(_prop(i, "NOM", r.get("NOM"), nom,
                                   "Nommage câble (type CTR par défaut, à confirmer)", auto=False))
                if "CODE" in cols and _vide(r.get("CODE")):
                    props.append(_prop(i, "CODE", r.get("CODE"), nom, "CODE = NOM", auto=False))
            if "EMPRISE" in cols and _vide(r.get("EMPRISE")):
                props.append(_prop(i, "EMPRISE", r.get("EMPRISE"), emp, "Emprise du NRA (…_001)"))
            for champ in ("PROPRIETAI", "GESTIONNAI"):
                if champ in cols and _vide(r.get(champ)):
                    props.append(_prop(i, champ, r.get(champ), "FREE MOBILE", "Propriétaire/Gestionnaire FREE MOBILE"))
            if "ETAT" in cols and _vide(r.get("ETAT")):
                props.append(_prop(i, "ETAT", r.get("ETAT"), "EN ETUDE", "État par défaut (nouveau projet)"))
            if "POSE" in cols and _vide(r.get("POSE")):
                props.append(_prop(i, "POSE", r.get("POSE"), date_str, "Date de pose = aaaammjj"))
            if "LONGUEUR_R" in cols and _vide(r.get("LONGUEUR_R")) and geom is not None:
                props.append(_prop(i, "LONGUEUR_R", r.get("LONGUEUR_R"), round(geom.length, 1),
                                   "Longueur réelle (géométrie)"))
            if "NB_TUBE" in cols and _vide(r.get("NB_TUBE")) and not _vide(capa):
                nt = _nb_tubes(capa)
                if nt:
                    props.append(_prop(i, "NB_TUBE", r.get("NB_TUBE"), nt, "Nb de tubes = capacité / 12"))

        elif objet == "SUPPORT":
            ts = str(r.get("TYPE_STRUC") or "").upper()
            if "LIBELLE" in cols and _vide(r.get("LIBELLE")):
                pref = PREFIXE_SUPPORT.get(ts, "GEC")
                num = _incr(pref)
                lib = f"{pref}_{nra}_001_{num:04d}"
                props.append(_prop(i, "LIBELLE", r.get("LIBELLE"), lib,
                                   f"Nommage support ({pref} d'après TYPE_STRUC)", auto=(ts in PREFIXE_SUPPORT)))
                if "CODE" in cols and _vide(r.get("CODE")):
                    props.append(_prop(i, "CODE", r.get("CODE"), lib, "CODE = LIBELLE", auto=(ts in PREFIXE_SUPPORT)))
            if "EMPRISE" in cols and _vide(r.get("EMPRISE")):
                props.append(_prop(i, "EMPRISE", r.get("EMPRISE"), emp, "Emprise du NRA (…_001)"))
            if "LGR_REEL" in cols and _vide(r.get("LGR_REEL")) and geom is not None:
                props.append(_prop(i, "LGR_REEL", r.get("LGR_REEL"), round(geom.length, 1),
                                   "Longueur réelle (géométrie)"))

        elif objet == "PT":
            prop_owner = str(r.get("PROPRIETAI") or "").upper()
            ts = str(r.get("TYPE_STRUC") or "").upper()
            if "NOM" in cols and _vide(r.get("NOM")):
                if "FREE" in prop_owner:
                    pref = "PAR" if "POTEAU" in ts or "POTELET" in ts else "PCH"
                    num = _incr("FM")
                    nom = f"{pref}_{nra}_000_FM{num:04d}"
                    props.append(_prop(i, "NOM", r.get("NOM"), nom,
                                       f"Nommage PT FREE MOBILE ({pref})", auto=False))
                    if "CODE" in cols and _vide(r.get("CODE")):
                        props.append(_prop(i, "CODE", r.get("CODE"), nom, "CODE = NOM", auto=False))
                else:
                    # PT tiers : préfixe selon le propriétaire, aidé des attributs existants
                    # (CODE, REF_CHAMBRE) et de l'INSEE détecté sur les autres PT.
                    pref = "PRV" if "PRIVE" in prop_owner else "FT"
                    code_ex = r.get("CODE")
                    ref_ch = r.get("REF_CHAMBR")
                    if not _vide(code_ex):
                        val, rz = normaliser_texte(code_ex), "NOM repris du CODE existant"
                    elif insee and not _vide(ref_ch):
                        val, rz = f"{pref}_{insee}_{normaliser_texte(ref_ch)}", f"Nommage {pref} (INSEE + REF_CHAMBRE)"
                    elif insee:
                        val, rz = f"{pref}_{insee}_{_incr(pref):06d}", f"Nommage {pref} (INSEE détecté ; n° à confirmer)"
                    else:
                        val, rz = None, "PT tiers : INSEE non détecté — à saisir"
                    props.append(_prop(i, "NOM", r.get("NOM"), val, rz, auto=False))
                    if val and "CODE" in cols and _vide(r.get("CODE")):
                        props.append(_prop(i, "CODE", r.get("CODE"), val, "CODE = NOM", auto=False))
            if "TYPE_FONC" in cols and _vide(r.get("TYPE_FONC")):
                props.append(_prop(i, "TYPE_FONC", r.get("TYPE_FONC"), "PASSAGE", "Type fonctionnel par défaut"))
            if "ETAT" in cols and _vide(r.get("ETAT")):
                props.append(_prop(i, "ETAT", r.get("ETAT"), "EN SERVICE", "État PT existant"))
            if "EMPRISE" in cols and _vide(r.get("EMPRISE")):
                props.append(_prop(i, "EMPRISE", r.get("EMPRISE"), emp, "Emprise du NRA (…_001)"))
            if "DATE_CREAT" in cols and _vide(r.get("DATE_CREAT")) and "FREE" in prop_owner:
                props.append(_prop(i, "DATE_CREAT", r.get("DATE_CREAT"), date_str, "Date de création (ouvrage FREE)"))

    logger.info(f"{len(props)} proposition(s) de nomenclature pour {objet} (NRA {nra}).")
    return props


def couche_a_completer(gdf, objet):
    """True si la couche a au moins un NOM/CODE/LIBELLE vide (donc à compléter)."""
    objet = (objet or "").upper().replace("[LIVRABLE]", "").strip()
    if gdf is None or len(gdf) == 0:
        return False
    cle = "LIBELLE" if objet == "SUPPORT" else "NOM"
    if cle not in gdf.columns:
        return False
    return bool(gdf[cle].map(_vide).any())
