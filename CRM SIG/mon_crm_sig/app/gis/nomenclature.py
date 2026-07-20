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

# ---------------------------------------------------------------------------
# NOMENCLATURE NETGEO v16 (2024-04) — règles par couche / champ
# ---------------------------------------------------------------------------
# Source : « listes de valeurs NETGEO v16_2024-04.xlsx » (onglets « Champs »,
# « liste CODE OP13 », « Exemple REFUS »). Les NOMS DE COLONNES ci-dessous sont
# ceux du gabarit livrable NETGEO (schéma réel des .shp), pas les libellés Excel.
#
# Chaque champ = _spec(...) :
#   defaut : valeur par défaut proposée si le champ est VIDE. Jetons dynamiques :
#            "@EMP"  -> emprise NRA (NRAxx_001) ; "@DATE" -> date aaaammjj ;
#            "@NRA"  -> identifiant NRA détecté. None = pas de défaut calculable
#            (le modal affiche « à saisir » si le champ est obligatoire).
#   liste  : liste FERMÉE de valeurs autorisées -> la validation BLOQUE toute
#            valeur présente hors liste (comparaison MAJUSCULE, sans accent).
#   fmt    : format attendu -> "cp" (5 chiffres), "emprise" (…_NNN),
#            "date" (aaaammjj), "ref_phfm" (SIT_FM_…), "num".
#   oblig  : champ obligatoire -> la validation BLOQUE s'il est vide.
#   gere   : "nom" (nommage) / "geom" (longueur) / "capa" (nb tubes) -> proposé
#            par la logique dédiée (le passage générique ne double pas la proposition).
#   op     : PROPRIETAIRE/GESTIONNAIRE ouvert (FT / FREE MOBILE / PRIVE / OP TIERS)
#            -> proposé en suggestion, NON bloquant (naming opérateur trop variable).
#   sur    : True = proposition sûre (pré-cochée) ; False = suggestion à valider.

# Codes opérateurs Interop (onglet « liste CODE OP13 ») — PROPRIETAI/GESTIONNAI
# des ouvrages OP TIERS (SUPPORT / PT). Utilisés pour guider (suggestion), pas
# pour bloquer.
CODES_OP13 = frozenset({
    "04CF", "0GNY", "0MLM", "0SEQ", "0T2S", "0TCA", "0TE2", "0THS", "0TSO", "ADTH",
    "ADTI", "AISN", "ANFI", "ARTD", "ATHD", "AUDE", "AVSC", "AXTD", "BART", "BEFO",
    "BFCF", "CAPS", "CCPB", "CMIN", "CMTD", "CODR", "CORS", "COSL", "COVT", "CSBY",
    "DAUF", "DEBI", "DOUB", "DPSL", "ENTH", "EULH", "EURE", "FAHA", "FI31", "FI44",
    "FIBA", "FIBR", "FREE", "FTEL", "GAZE", "GDHD", "GERS", "GOTE", "GRAV", "GTHD",
    "GUAD", "GUYA", "HASF", "HMN", "HTHD", "INOL", "ISER", "JURA", "KFIB", "LAFI",
    "LAND", "LOAN", "LOFI", "LOIR", "LOSA", "LOZE", "LRCA", "LTHD", "MANC", "MART",
    "MAYE", "MEIC", "MFIB", "MNUM", "MONU", "NATH", "NEYO", "NIED", "NIVE", "NPDC",
    "NU66", "NUME", "OCTO", "OMTD", "ONUM", "OPAL", "OUTR", "PACT", "RC08", "REFO",
    "RESO", "REUN", "REVA", "ROSA", "RRTH", "SACO", "SART", "SAVO", "SEQU", "SETH",
    "SFMD", "SFOR", "SFRA", "SIEA", "SIEL", "SMTH", "SNMA", "SOGA", "SPLS", "SPTH",
    "SRRA", "SY79", "SYMB", "THDB", "TARN", "TERA", "THDD", "THDT", "VALO", "VAUC",
    "VDLF", "VENU", "VIEN", "VOFI", "VTHD", "WAFI", "WIGA", "YANA", "YCON", "YVFI",
})

_ETAT2 = ("EN SERVICE", "EN ETUDE")


def _spec(defaut=None, liste=None, fmt=None, oblig=False, gere=None, op=False, sur=True, derive=False):
    # derive=True : champ RECALCULÉ à la génération DOE (POSE/DATE_DE_CR <- date TVX,
    # DATE_CREAT <- placeholder) -> la validation l'IGNORE (sa valeur d'entrée sera
    # écrasée), on ne bloque donc pas sur son format/état d'entrée.
    return {"defaut": defaut, "liste": liste, "fmt": fmt, "oblig": oblig,
            "gere": gere, "op": op, "sur": sur, "derive": derive}


NOMENCLATURE = {
    "CABLES": {
        "NOM":        _spec(gere="nom", oblig=True, sur=False),
        "CODE":       _spec(gere="nom", oblig=True, sur=False),
        "EMPRISE":    _spec(defaut="@EMP", fmt="emprise", oblig=True),
        "PROPRIETAI": _spec(defaut="FREE MOBILE", liste=("FREE MOBILE",), oblig=True),
        "GESTIONNAI": _spec(defaut="FREE MOBILE", liste=("FREE MOBILE",), oblig=True),
        "POSE":       _spec(defaut="@DATE", fmt="date", derive=True),   # <- date TVX à la génération
        "ETAT":       _spec(defaut="EN ETUDE", liste=_ETAT2, oblig=True),
        "FABRICANT":  _spec(),   # référentiel capa/fabricant/référence (phase 2)
        "REFERENCE":  _spec(),
        "LONGUEUR_R": _spec(gere="geom", fmt="num"),
        "NB_TUBE":    _spec(gere="capa"),   # = ceil(capacité/12) : 1..24 selon la capa -> non bloquant
        "CAPACITE":   _spec(),
        "FCI":        _spec(),
        "SYMBOLISAT": _spec(liste=("CBM", "BAG", "FON", "CDD", "CTR", "CDI", "CAD"), sur=False),
    },
    "BPE": {
        "NOM":        _spec(gere="nom", oblig=True, sur=False),
        "CODE":       _spec(gere="nom", oblig=True, sur=False),
        "ADRESSE":    _spec(),
        "CP":         _spec(fmt="cp"),
        "VILLE":      _spec(),
        "TYPE_FONCT": _spec(defaut="PROTECTION_EPISSURE", liste=("PROTECTION_EPISSURE",), oblig=True),
        "ETAT":       _spec(defaut="EN ETUDE", liste=_ETAT2, oblig=True),
        "MODELE":     _spec(),   # référentiel modèle/référence (phase 2)
        "REFERENCE":  _spec(),
        "PROPRIETAI": _spec(defaut="FREE MOBILE", liste=("FREE MOBILE",), oblig=True),
        "GESTIONNAI": _spec(defaut="FREE MOBILE", liste=("FREE MOBILE",), oblig=True),
        "DATE_DE_CR": _spec(fmt="date", derive=True),   # <- date TVX à la génération DOE
        "EMPRISE":    _spec(defaut="@EMP", fmt="emprise", oblig=True),
    },
    "SUPPORT": {
        "LIBELLE":    _spec(gere="nom", oblig=True, sur=False),
        "EMPRISE":    _spec(defaut="@EMP", fmt="emprise", oblig=True),
        "TYPE_STRUC": _spec(liste=("TRANCHEE", "AERIEN", "FORAGE", "FACADE"), oblig=True, sur=False),
        "PROPRIETAI": _spec(op=True),
        "GESTIONNAI": _spec(op=True),
        "LGR_REEL":   _spec(gere="geom", fmt="num"),
        "VOIE":       _spec(),
        "COMMUNE":    _spec(),
        "P_VOIRIE":   _spec(),
        "CHARGE":     _spec(),
        "DOMANI":     _spec(),
        "COMPO":      _spec(),
        "DATE_CONST": _spec(fmt="date"),
    },
    "PT": {
        "NOM":        _spec(gere="nom", oblig=True, sur=False),
        "CODE":       _spec(gere="nom", oblig=True, sur=False),
        "ADRESSE":    _spec(),
        "CODE_POSTA": _spec(fmt="cp"),
        "VILLE":      _spec(),
        "TYPE_FONC":  _spec(defaut="PASSAGE", liste=("PASSAGE", "DERIVATION", "PASSAGE/DERIVATION"), oblig=True),
        # PT.TYPE_STRUC : ouvert — au-delà de CHAMBRE/POTEAU/POTELET, il existe des
        # points techniques (COFFRET, EGOUT, FACADE, AERO SOUTERRAIN…) : non bloquant.
        "TYPE_STRUC": _spec(defaut="CHAMBRE", oblig=True, sur=False),
        "ETAT":       _spec(defaut="EN SERVICE", liste=_ETAT2, oblig=True),
        "MODELE":     _spec(),   # OHN / K2C / L0T… ou BOIS/METAL/COMPOSITE/BETON (poteaux)
        "PROPRIETAI": _spec(op=True),
        "GESTIONNAI": _spec(op=True),
        "EMPRISE":    _spec(defaut="@EMP", fmt="emprise", oblig=True),
        "DATE_CREAT": _spec(fmt="date", derive=True),   # <- placeholder AAAAMMJJ à la génération DOE
    },
    "BTS": {
        "REF_PHFM":   _spec(fmt="ref_phfm", oblig=True, sur=False),
        "ADRESSE":    _spec(),
        "CP":         _spec(fmt="cp"),
        "VILLE":      _spec(),
        "TYPE_FONCT": _spec(defaut="FM", liste=("FM",), oblig=True),
        "TYPE_STRUC": _spec(),   # IMMEUBLE / PYLONE / CHATEAU D EAU / TOUR… (ouvert)
        "PROPRIETAI": _spec(),   # FREE MOBILE / TDF / SYNDIC / PRIVE… (ouvert)
        "GESTIONNAI": _spec(defaut="FREE MOBILE", liste=("FREE MOBILE",), oblig=True),
        "EMPRISE":    _spec(defaut="@EMP", fmt="emprise", oblig=True),
        "ETAT":       _spec(defaut="EN SERVICE", liste=_ETAT2, oblig=True),
    },
    "NRA": {
        "NOM":        _spec(defaut="@NRA", gere="nom", oblig=True, sur=False),
        "ADRESSE":    _spec(),
        "CP":         _spec(fmt="cp"),
        "VILLE":      _spec(),
        "TYPE_FONCT": _spec(defaut="NOEUD DE RACCORDEMENT",
                            liste=("NOEUD DE RACCORDEMENT", "POINT DE PRESENCE FREE"), oblig=True, sur=False),
        "TYPE_STRUC": _spec(),   # IMMEUBLE / SHELTER / ARMOIRE DE RUE… (ouvert)
        "PROPRIETAI": _spec(liste=("FT", "FREE MOBILE")),
        "GESTIONNAI": _spec(liste=("FT", "FREE MOBILE")),
        "EMPRISE":    _spec(defaut="@EMP", fmt="emprise", oblig=True),
        "ETAT":       _spec(defaut="EN SERVICE", liste=_ETAT2, oblig=True),
    },
    "NRO_RIP": {
        "NOM":        _spec(gere="nom", oblig=True, sur=False),   # RIP_COMMUNE (à saisir)
        "CODE":       _spec(),
        "ADRESSE":    _spec(),
        "CP":         _spec(fmt="cp"),
        "VILLE":      _spec(),
        "TYPE_FONCT": _spec(defaut="NOEUD DE RACCORDEMENT OPTIQUE",
                            liste=("NOEUD DE RACCORDEMENT OPTIQUE",), oblig=True, sur=False),
        "TYPE_STRUC": _spec(),   # IMMEUBLE / SHELTER / BATIMENT (ouvert)
        "PROPRIETAI": _spec(defaut="RIP", liste=("RIP",), oblig=True),
        "GESTIONNAI": _spec(),   # COVAGE / AXIONE / SFR… (ouvert)
        "EMPRISE":    _spec(defaut="@EMP", fmt="emprise", oblig=True),
        "ETAT":       _spec(defaut="EN SERVICE", liste=_ETAT2, oblig=True),
    },
}

# Couches gérées par la nomenclature (7 couches NETGEO).
COUCHES_NOMENCLATURE = tuple(NOMENCLATURE.keys())


def _valeurs_autorisees(spec):
    """Ensemble des valeurs autorisées pour la PROPOSITION (surface les valeurs
    hors liste dans le modal). Pour les champs PROPRIETAI/GESTIONNAI ouverts
    (``op``), on élargit à FT/FREE MOBILE/PRIVE + codes OP13."""
    if spec.get("op"):
        return {"FT", "FREE MOBILE", "PRIVE"} | set(CODES_OP13) | set(spec.get("liste") or ())
    liste = spec.get("liste")
    return set(liste) if liste else None


def _format_ok(fmt, s):
    """True si ``s`` (déjà normalisé MAJUSCULE) respecte le format ``fmt``."""
    if not s:
        return True
    if fmt == "cp":
        return bool(re.fullmatch(r"\d{5}", s))
    if fmt == "emprise":
        return bool(re.search(r"_\d{3}$", s))          # …_001, …_002 (souple)
    if fmt == "date":
        return bool(re.fullmatch(r"\d{8}", s))          # aaaammjj
    if fmt == "ref_phfm":
        return s.startswith("SIT_FM")                   # SIT_FM_XXXXX_XXX_XX
    if fmt == "num":
        try:
            float(str(s).replace(",", "."))
            return True
        except (TypeError, ValueError):
            return False
    return True


_FMT_MSG = {
    "cp": "Code postal attendu : 5 chiffres",
    "emprise": "Emprise attendue : …_001 (ex. NRA68_001)",
    "date": "Date attendue au format aaaammjj (8 chiffres)",
    "ref_phfm": "Référence PHFM attendue : SIT_FM_XXXXX_XXX_XX",
    "num": "Valeur numérique attendue",
}


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


def _val_str(v):
    """Représentation texte d'une valeur pour COMPARAISON (liste/format) : les
    floats entiers issus des shapefiles (48.0, 4.0, 68000.0) redeviennent
    '48'/'4'/'68000' — sinon 'EN SERVICE' passe mais '68000.0' échouerait au
    format CP et '4.0' serait vu hors liste."""
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        if v.is_integer():
            return str(int(v))
    return str(v)


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


def _variante_unique(val, utilises):
    """Propose une variante UNIQUE d'un nommage en doublon : incrémente le nombre
    final s'il existe (…_0002 -> …_0003), sinon suffixe _2, _3… — jusqu'à sortir
    de ``utilises``."""
    m = re.search(r"(\d+)$", val)
    if m:
        base, n, largeur = val[:m.start()], int(m.group(1)), len(m.group(1))
        k = n + 1
        cand = f"{base}{k:0{largeur}d}"
        while cand in utilises:
            k += 1
            cand = f"{base}{k:0{largeur}d}"
        return cand
    k = 2
    cand = f"{val}_{k}"
    while cand in utilises:
        k += 1
        cand = f"{val}_{k}"
    return cand


def _prop(ligne, champ, ancienne, proposee, raison, auto=True):
    return {"ligne": int(ligne), "champ": champ,
            "ancienne": None if ancienne is None else str(ancienne),
            "proposee": None if proposee is None else str(proposee),
            "raison": raison, "auto": bool(auto)}


def proposer_couche(gdf, objet, date_str=None):
    """
    Retourne la liste des propositions {ligne, champ, ancienne, proposee, raison, auto}
    pour la couche `gdf` — les 7 couches NETGEO (BPE, CABLES, SUPPORT, PT, BTS,
    NRA, NRO_RIP). Trois natures de proposition :
      1. NOMMAGE (bespoke) : NOM/CODE/LIBELLE incrémentés (auto=False, à confirmer) ;
      2. DÉFAUTS conformes (passage générique piloté par ``NOMENCLATURE``) : champ
         vide -> valeur par défaut (EMPRISE, ETAT, PROPRIETAI/GESTIONNAI=FREE MOBILE,
         TYPE_FONCT, POSE…) ; champ OBLIGATOIRE sans défaut calculable -> « à saisir » ;
         valeur PRÉSENTE hors liste/format -> proposition de correction (à valider) ;
      3. NORMALISATION (MAJUSCULE, sans accent/apostrophe, trim) des textes existants.
    ``auto=True`` = correction sûre (pré-cochée) ; ``auto=False`` = suggestion à valider.
    Ne modifie rien : c'est le modal de nomenclature qui applique après validation.
    """
    objet = (objet or "").upper().replace("[LIVRABLE]", "").strip()
    date_str = date_str or datetime.utcnow().strftime("%Y%m%d")   # aaaammjj (8 chiffres)
    props = []
    if gdf is None or len(gdf) == 0:
        return props
    nra = _nra_du_projet(gdf)
    insee = _insee_du_projet(gdf)
    emp = _emprise(nra)
    cols = set(gdf.columns)

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
            # PT.DATE_CREAT : NON proposée — reste le placeholder « AAAAMMJJ » côté DOE FO.

        elif objet == "BPE":
            # Nommage BPE incrémenté (PXX_NRAxx_XXX_FMXXX) — à confirmer au terrain.
            if "NOM" in cols and _vide(r.get("NOM")):
                num = _incr("BPE")
                nom = f"P_{nra}_001_FM{num:03d}"
                props.append(_prop(i, "NOM", r.get("NOM"), nom,
                                   "Nommage BPE (FREE MOBILE, à confirmer)", auto=False))
                if "CODE" in cols and _vide(r.get("CODE")):
                    props.append(_prop(i, "CODE", r.get("CODE"), nom, "CODE = NOM", auto=False))

    # ------------------------------------------------------------------
    # Passage GÉNÉRIQUE piloté par NOMENCLATURE (7 couches, tous champs) :
    # défauts, obligatoires « à saisir », et corrections des valeurs présentes
    # hors liste / hors format. Ne double PAS ce que le nommage bespoke a déjà
    # proposé (garde `deja`).
    # ------------------------------------------------------------------
    # CODE = NOM : si CODE est vide alors que NOM est renseigné (le nommage bespoke
    # ne couvre que le cas « NOM vide »). Correction sûre (auto=True), AVANT le
    # passage générique pour qu'il ne propose pas « CODE à saisir » à la place.
    if objet in ("CABLES", "BPE", "PT") and "NOM" in cols and "CODE" in cols:
        deja_code = {(p["ligne"], p["champ"]) for p in props}
        for i, (_, r) in enumerate(gdf.iterrows()):
            if (i, "CODE") in deja_code:
                continue
            if _vide(r.get("CODE")) and not _vide(r.get("NOM")):
                props.append(_prop(i, "CODE", r.get("CODE"), normaliser_texte(r.get("NOM")), "CODE = NOM"))

    spec_couche = NOMENCLATURE.get(objet, {})
    deja = {(p["ligne"], p["champ"]) for p in props}

    def _resoudre(defaut):
        if defaut == "@EMP":
            return emp
        if defaut == "@DATE":
            return date_str
        if defaut == "@NRA":
            return nra
        return defaut

    for i, (_, r) in enumerate(gdf.iterrows()):
        for champ, spec in spec_couche.items():
            if champ not in cols or (i, champ) in deja:
                continue
            v = r.get(champ)
            if _vide(v):
                if spec["gere"] in ("geom", "capa"):
                    continue                       # longueur/nb tubes : gérés bespoke (sans défaut ici)
                defv = _resoudre(spec["defaut"])
                if defv is not None:
                    props.append(_prop(i, champ, v, defv, "Valeur par défaut (nomenclature)",
                                       auto=spec["sur"]))
                elif spec["oblig"]:
                    props.append(_prop(i, champ, v, None, "Champ obligatoire — à saisir", auto=False))
                continue
            # champ présent : corriger si hors liste / hors format
            if spec.get("derive"):
                continue                            # POSE/DATE_* : recalculés à la génération DOE
            s = normaliser_texte(_val_str(v))
            if spec["fmt"] == "date" and s == "AAAAMMJJ":
                continue                            # placeholder DOE FO toléré
            liste = spec.get("liste")               # liste FERMÉE (op = ouvert, non signalé)
            if liste and s not in set(liste):
                defv = _resoudre(spec["defaut"])
                apercu = " / ".join(liste[:6]) + ("…" if len(liste) > 6 else "")
                props.append(_prop(i, champ, v, defv, f"Valeur hors liste ({apercu}) — à corriger",
                                   auto=False))
            elif spec["fmt"] and not _format_ok(spec["fmt"], s):
                defv = _resoudre(spec["defaut"]) if spec["fmt"] == "emprise" else None
                # correction de format DÉTERMINISTE (ex. EMPRISE -> …_001) : pré-cochée.
                props.append(_prop(i, champ, v, defv,
                                   _FMT_MSG.get(spec["fmt"], "Format invalide") + " — à corriger",
                                   auto=(defv is not None)))

    # ------------------------------------------------------------------
    # DOUBLONS de nommage (interdits) : on propose une variante unique pour les
    # occurrences répétées d'un NOM/LIBELLE/REF_PHFM présent (auto=False).
    # ------------------------------------------------------------------
    id_champ = next((c for c in ("NOM", "LIBELLE", "REF_PHFM") if c in cols), None)
    deja = {(p["ligne"], p["champ"]) for p in props}
    if id_champ:
        utilises, vus = set(), {}
        for i, (_, r) in enumerate(gdf.iterrows()):
            v = r.get(id_champ)
            if not _vide(v):
                utilises.add(normaliser_texte(v))
        for i, (_, r) in enumerate(gdf.iterrows()):
            v = r.get(id_champ)
            if _vide(v) or (i, id_champ) in deja:
                continue
            s = normaliser_texte(v)
            if s in vus:                            # doublon : proposer une variante
                nv = _variante_unique(s, utilises)
                utilises.add(nv)
                props.append(_prop(i, id_champ, v, nv,
                                   f"Doublon de nommage (déjà ligne {vus[s]}) — renommer", auto=False))
            else:
                vus[s] = i

    # ------------------------------------------------------------------
    # NORMALISATION (MAJUSCULE, sans accent/apostrophe, trim) — en dernier, en
    # évitant les champs déjà proposés (défaut/correction) pour ne pas masquer
    # une valeur hors liste par une simple mise en forme.
    # ------------------------------------------------------------------
    deja = {(p["ligne"], p["champ"]) for p in props}
    champs_texte = [c for c in cols if c not in ("geometry", "LONGUEUR_R", "LGR_REEL",
                                                 "NB_TUBE", "CAPACITE", "ORDRE")]
    for i, (_, r) in enumerate(gdf.iterrows()):
        for c in champs_texte:
            if (i, c) in deja:
                continue
            v = r.get(c)
            if not _vide(v):
                n = normaliser_texte(v)
                if n != str(v):
                    props.append(_prop(i, c, v, n, "Normalisation (MAJUSCULE, sans accent/apostrophe, trim)"))

    logger.info(f"{len(props)} proposition(s) de nomenclature pour {objet} (NRA {nra}).")
    return props


def valider_couche(gdf, objet):
    """Contrôle de conformité NETGEO d'une couche. Retourne la liste des ANOMALIES
    BLOQUANTES ``{ligne, champ, valeur, raison}`` :
      · champ OBLIGATOIRE vide ;
      · valeur présente HORS LISTE fermée (ETAT, NB_TUBE, SYMBOLISAT, TYPE_STRUC,
        TYPE_FONCT, PROPRIETAI/GESTIONNAI figés…) ;
      · FORMAT invalide (CP 5 chiffres, EMPRISE …_NNN, date aaaammjj, REF_PHFM) ;
      · DOUBLON de nommage (NOM/LIBELLE/REF_PHFM répété).
    Les champs PROPRIETAI/GESTIONNAI « ouverts » (SUPPORT/PT/BTS) NE bloquent PAS
    (naming opérateur trop variable) — ils sont seulement suggérés à l'import.
    Liste vide = couche conforme."""
    objet = (objet or "").upper().replace("[LIVRABLE]", "").strip()
    spec_couche = NOMENCLATURE.get(objet)
    anomalies = []
    if gdf is None or len(gdf) == 0 or not spec_couche:
        return anomalies
    cols = set(gdf.columns)

    def _anom(ligne, champ, valeur, raison):
        anomalies.append({"ligne": int(ligne), "champ": champ,
                          "valeur": None if valeur is None else str(valeur), "raison": raison})

    # Doublons de nommage (interdits par la nomenclature).
    id_champ = next((c for c in ("NOM", "LIBELLE", "REF_PHFM") if c in cols), None)
    if id_champ:
        vus = {}
        for i, (_, r) in enumerate(gdf.iterrows()):
            v = r.get(id_champ)
            if _vide(v):
                continue
            s = normaliser_texte(v)
            if s in vus:
                _anom(i, id_champ, v, f"Doublon de nommage (déjà ligne {vus[s]})")
            else:
                vus[s] = i

    for i, (_, r) in enumerate(gdf.iterrows()):
        for champ, spec in spec_couche.items():
            if champ not in cols:
                continue
            if spec.get("derive"):
                continue                            # POSE/DATE_* : recalculés à la génération DOE
            v = r.get(champ)
            if _vide(v):
                if spec["oblig"]:
                    _anom(i, champ, v, "Champ obligatoire vide")
                continue
            s = normaliser_texte(_val_str(v))
            if spec["fmt"] == "date" and s == "AAAAMMJJ":
                continue                            # placeholder DOE FO toléré
            liste = spec.get("liste")               # liste FERMÉE uniquement (op = non bloquant)
            if liste and s not in set(liste):
                apercu = " / ".join(liste)
                _anom(i, champ, v, f"Valeur hors liste (attendu : {apercu})")
            elif spec["fmt"] and not _format_ok(spec["fmt"], s):
                _anom(i, champ, v, _FMT_MSG.get(spec["fmt"], "Format invalide"))
    return anomalies


def couche_a_completer(gdf, objet):
    """True si la couche a AU MOINS UNE proposition de nomenclature — pas seulement
    un NOM/LIBELLE vide, mais aussi une normalisation (MAJUSCULE/accents) ou un
    champ par défaut à renseigner (EMPRISE, POSE, ETAT…). Aligné sur le moteur
    ``proposer_couche`` : sans quoi une couche déjà nommée mais à normaliser
    n'ouvrait jamais le modal de nomenclature à l'import."""
    objet = (objet or "").upper().replace("[LIVRABLE]", "").strip()
    if gdf is None or len(gdf) == 0:
        return False
    try:
        return len(proposer_couche(gdf, objet)) > 0
    except Exception:
        return False
