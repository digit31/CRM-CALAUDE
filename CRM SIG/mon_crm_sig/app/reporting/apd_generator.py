"""
apd_generator.py - Console Étude : calcul de synthèse + rapport APD_HTL (PDF).

Deux rôles :
  1. calculer_synthese(dossier_shape, ...) : pré-remplit les « Caractéristiques
     de la liaison » (page 2 du rapport APD) à partir des SHP LIVRABLES
     (source de vérité) — longueurs réseaux par propriétaire, chambres, appuis,
     boîtes par modèle, + cartouche (code, adresse, commune).
  2. generer_rapport_apd(donnees, chemin_pdf) : produit le rapport APD_HTL PDF
     (cartouche + page synthèse) depuis les données de la console.

Les champs non déductibles (love manchon, VTL, appuis à remplacer/recaler,
propriétaire tiers, informations remarquables, auteur, version) restent saisis
dans la console.
"""

import os
import math
import json
import logging

import geopandas as gpd
from fpdf import FPDF

logger = logging.getLogger("crm_sig.apd_generator")

ASSETS = os.path.join(os.path.dirname(__file__), "assets_plan")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lire(dossier, nom):
    p = os.path.join(dossier, f"{nom}.shp")
    if not os.path.exists(p):
        return None
    try:
        g = gpd.read_file(p)
        return g if len(g) else None
    except Exception as e:
        logger.warning(f"APD : lecture {nom}.shp impossible ({e})")
        return None


def _val(row, champ):
    v = row.get(champ)
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v).strip()


def _up(row, champ):
    return _val(row, champ).upper()


def _somme_lgr(gdf):
    if gdf is None or "LGR_REEL" not in gdf.columns:
        return 0
    return int(round(gdf["LGR_REEL"].fillna(0).sum()))


def _est_aerien(struc):
    s = (struc or "").upper()
    return "AERIEN" in s or "FACADE" in s


def _est_poteau(struc):
    s = (struc or "").upper()
    return "POTEAU" in s or "POTELET" in s


def _modele_boite(modele):
    m = (modele or "").upper().replace(" ", "")
    if "OFDC" in m:
        return "ofdc"
    if "TENIO" in m:
        return "tenio"
    if "3MT1" in m or m == "T1":
        return "t1"
    if "3MT0" in m or m == "T0":
        return "t0"
    return None


# ---------------------------------------------------------------------------
# 1. Calcul de la synthèse (pré-remplissage de la console)
# ---------------------------------------------------------------------------

def calculer_synthese(dossier_shape: str, ref_projet: str = "", date_str: str = "") -> dict:
    """Valeurs déductibles des SHP livrables pour la console / le rapport APD."""
    sup = _lire(dossier_shape, "SUPPORT")
    pt = _lire(dossier_shape, "PT")
    bpe = _lire(dossier_shape, "BPE")
    cab = _lire(dossier_shape, "CABLES")
    bts = _lire(dossier_shape, "BTS")
    nra = _lire(dossier_shape, "NRA")
    nro = _lire(dossier_shape, "NRO_RIP")
    com = _lire(dossier_shape, "COMMUNE")

    # --- SUPPORT : longueurs par réseau ---
    sout = {"blo": 0, "free": 0, "gc": 0, "tiers": 0, "total": 0}
    aer = {"free": 0, "orange": 0, "enedis": 0, "tiers": 0}
    aer_ap = {"free": 0, "orange": 0, "enedis": 0, "tiers": 0}
    if sup is not None:
        for _, r in sup.iterrows():
            prop = _up(r, "PROPRIETAI")
            compo = _up(r, "COMPOSITIO")
            struc = _up(r, "TYPE_STRUC")
            lgr = 0
            try:
                lgr = int(round(float(r.get("LGR_REEL") or 0)))
            except (TypeError, ValueError):
                lgr = 0
            free = "FREE" in prop
            enedis = "ENEDIS" in prop
            ftorange = ("FT" in prop) or ("ORANGE" in prop)
            if _est_aerien(struc):
                if free:
                    aer["free"] += lgr
                elif "ORANGE" in prop:
                    aer["orange"] += lgr
                elif enedis:
                    aer["enedis"] += lgr
                elif ftorange:
                    aer["orange"] += lgr
                else:
                    aer["tiers"] += lgr
            else:  # souterrain
                sout["total"] += lgr
                if free:
                    if compo.startswith("GC FREE"):
                        sout["gc"] += lgr
                    else:
                        sout["free"] += lgr
                elif ftorange or enedis:
                    sout["blo"] += lgr
                else:
                    sout["tiers"] += lgr

    # --- PT : chambres (souterrain) et appuis (aérien) par propriétaire ---
    ch = {"blo": 0, "free": 0, "tiers": 0}
    appuis = {"remplacer": 0, "recaler": 0, "implanter": 0}
    if pt is not None:
        for _, r in pt.iterrows():
            prop = _up(r, "PROPRIETAI")
            struc = _up(r, "TYPE_STRUC")
            etat = _up(r, "ETAT")
            free = "FREE" in prop
            if _est_poteau(struc):
                if "ETUDE" in etat:
                    appuis["implanter"] += 1
                if "REMPLAC" in etat:
                    appuis["remplacer"] += 1
                if "RENFORC" in etat or "RECALAGE" in etat:
                    appuis["recaler"] += 1
                # comptage appuis aériens par proprio
                if free:
                    aer_ap["free"] += 1
                elif "ORANGE" in prop or "FT" in prop:
                    aer_ap["orange"] += 1
                elif "ENEDIS" in prop:
                    aer_ap["enedis"] += 1
                else:
                    aer_ap["tiers"] += 1
            else:  # chambre
                if free:
                    ch["free"] += 1
                elif ("FT" in prop) or ("ORANGE" in prop):
                    ch["blo"] += 1
                else:
                    ch["tiers"] += 1

    # --- BPE : boîtes par modèle (nouvelles) + classement souterrain/aérien ---
    boites = {"ofdc": 0, "tenio": 0, "t0": 0, "t1": 0}
    nb_boites_sout = nb_boites_aer = 0  # TOUTES les boîtes (existantes + à créer)
    if bpe is not None:
        for _, r in bpe.iterrows():
            # Page 2 (synthèse liaison) : compter TOUTES les boîtes BPE de la
            # couche — existantes (EN SERVICE) ET à créer. (Les vignettes des
            # pages 3/4 ne listent, elles, que les boîtes à créer.)
            cle = _modele_boite(_val(r, "MODELE"))
            if cle:
                boites[cle] += 1
            # boîte souterraine (chambre) ou aérienne (poteau) via le PT porteur
            g = r.geometry
            typ = ""
            if g is not None and pt is not None:
                try:
                    typ = _up(pt.iloc[int(pt.distance(g).idxmin())], "TYPE_STRUC")
                except Exception:
                    typ = ""
            if typ and _est_poteau(typ):
                nb_boites_aer += 1
            else:
                nb_boites_sout += 1
    total_boites = sum(boites.values())

    # --- Chambre(s) FREE sans boîte (love manchon souterrain) ---
    free_ch_sans_boite = 0
    if pt is not None:
        for _, r in pt.iterrows():
            if "FREE" not in _up(r, "PROPRIETAI") or _est_poteau(_up(r, "TYPE_STRUC")):
                continue
            g = r.geometry
            a_boite = False
            if g is not None and bpe is not None:
                try:
                    a_boite = bool((bpe.distance(g) <= 2.0).any())
                except Exception:
                    a_boite = False
            if not a_boite:
                free_ch_sans_boite += 1

    # --- Câbles (info complémentaire) ---
    nb_cables = 0 if cab is None else len(cab)
    capas = []
    if cab is not None and "CAPACITE" in cab.columns:
        capas = sorted({int(x) for x in cab["CAPACITE"].dropna().tolist()})

    # --- Cartouche (code / adresse / commune) + type de site ---
    def _adresse(row):
        ad = _val(row, "ADRESSE")
        cp = _val(row, "CP") or _val(row, "CODE_POSTA")
        vi = _val(row, "VILLE")
        return ad, cp, vi

    code = ref_projet.replace("-", "_")
    adresse = commune = cp = ""
    type_etude = "APD HTL"
    site = ""
    pylone = False
    l5t_nro = 0
    if bts is not None and len(bts):
        r = bts.iloc[0]
        ref = _val(r, "REF_PHFM") or _val(r, "NOM")
        for pre in ("SIT_FM_", "SIT_", "BTS_"):
            if ref.upper().startswith(pre):
                ref = ref[len(pre):]
                break
        code = ref or code
        adresse, cp, commune = _adresse(r)
        type_etude = "APD HTL BTS"
        site = "BTS"
        pylone = "PYLONE" in _up(r, "TYPE_STRUC")
    elif nro is not None and len(nro):
        r = nro.iloc[0]
        code = _val(r, "CODE") or code
        adresse, cp, commune = _adresse(r)
        type_etude = "APD HTL NRO"
        site = "NRO"
    elif nra is not None and len(nra):
        r = nra.iloc[0]
        code = _val(r, "NOM") or code
        adresse, cp, commune = _adresse(r)
        type_etude = "APD HTL NRA"
        site = "NRA"
    if not commune and com is not None and len(com):
        commune = _val(com.iloc[0], "NOM")

    if site == "NRO" and pt is not None:
        for _, r in pt.iterrows():
            if "L5T" in _up(r, "REF_CHAMBR") or "L5T" in _up(r, "MODELE"):
                l5t_nro = 1
                break

    # --- VTL : 20 ml si BTS Pylône ou NRO ; BTS non-pylône = à saisir (APD VTL) ;
    #           autre site (NRA) = ligne supprimée ---
    if (site == "BTS" and pylone) or site == "NRO":
        vtl_ml, vtl_visible = 20, True
    elif site == "BTS":
        vtl_ml, vtl_visible = 0, True
    else:
        vtl_ml, vtl_visible = 0, False

    # GC : longueur issue de l'APD GC (saisie manuelle), non déduite du SHP FO.
    gc_ml = 0

    # --- Totaux souterrains (total des lignes du dessus) ---
    total_sout = sout["blo"] + sout["free"] + sout["tiers"] + gc_ml + vtl_ml
    # Love manchon : (total + 20 si L5T NRO ou chambre Free sans boîte
    #                 + 20/boîte souterraine + 3/chambre FT) x 1,05
    comp_l5t_free = 20 if (l5t_nro or free_ch_sans_boite > 0) else 0
    total_sout_love = int(round(
        (total_sout + comp_l5t_free + 20 * nb_boites_sout + 3 * ch["blo"]) * 1.05))

    # --- Totaux aériens ---
    total_aer = aer["free"] + aer["orange"] + aer["enedis"] + aer["tiers"]
    total_aer_ap = sum(aer_ap.values())
    # Love manchon aérien : (total + 20/boîte aérienne) x 1,05
    total_aer_love = int(round((total_aer + 20 * nb_boites_aer) * 1.05))

    return {
        "cartouche": {
            "code_projet": code, "type_etude": type_etude,
            "adresse": adresse, "commune": commune, "cp": cp,
            "date_real": date_str, "fait_par": "", "version": "V1", "site": site,
        },
        "souterrain": {
            "blo_ml": sout["blo"], "blo_ch": ch["blo"],
            "free_ml": sout["free"], "free_ch": ch["free"],
            "tiers_nom": "", "tiers_ml": sout["tiers"], "tiers_ch": ch["tiers"],
            "gc_ml": gc_ml, "gc_ch": 0,
            "vtl_ml": vtl_ml, "vtl_visible": vtl_visible,
            "total_ml": total_sout, "total_love_ml": total_sout_love,
        },
        "aerien": {
            "free_ml": aer["free"], "free_ap": aer_ap["free"],
            "orange_ml": aer["orange"], "orange_ap": aer_ap["orange"],
            "enedis_ml": aer["enedis"], "enedis_ap": aer_ap["enedis"],
            "tiers_nom": "", "tiers_ml": aer["tiers"], "tiers_ap": aer_ap["tiers"],
            "total_ml": total_aer, "total_ap": total_aer_ap,
            "total_love_ml": total_aer_love,
        },
        "appuis": appuis,
        "boites": {**boites, "total": total_boites},
        "cables": {"nombre": nb_cables, "capacites": capas},
        "infos": "",
    }


def fusionner(defauts: dict, sauvegarde: dict) -> dict:
    """Fusionne les valeurs sauvegardées par-dessus les défauts auto-calculés."""
    if not sauvegarde:
        return defauts
    out = json.loads(json.dumps(defauts))
    for sec, val in sauvegarde.items():
        if isinstance(val, dict) and isinstance(out.get(sec), dict):
            out[sec].update({k: v for k, v in val.items() if v is not None})
        else:
            out[sec] = val
    return out


# Champs de SAISIE MANUELLE (non déductibles du SHP FO) : seuls ceux-ci sont
# repris de la dernière sauvegarde. Tout le reste est TOUJOURS recalculé depuis
# le SHP livrable (source de vérité), afin que la Console reflète l'état courant
# du SHP tout en préservant les saisies de l'utilisateur.
_CHAMPS_MANUELS = {
    "cartouche": ("fait_par", "version"),
    "souterrain": ("gc_ml", "gc_ch", "tiers_nom", "vtl_ml"),
    "aerien": ("tiers_nom",),
    "appuis": ("remplacer", "recaler"),
    "boites": ("ofdc", "tenio", "t1"),
}


def _n0(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


def fusionner_console(defauts: dict, sauvegarde: dict) -> dict:
    """Fusion Console/rapport : valeurs AUTO toujours recalculées depuis le SHP
    livrable (defauts), et SEULS les champs de saisie manuelle repris de la
    sauvegarde. La Console/le rapport reflètent donc toujours le SHP courant sans
    jamais perdre les saisies — remplace l'ancienne fusion qui figeait les
    valeurs auto (instantané périmé après modification du SHP)."""
    out = json.loads(json.dumps(defauts))
    if sauvegarde:
        for sec, champs in _CHAMPS_MANUELS.items():
            src = sauvegarde.get(sec)
            if isinstance(src, dict) and isinstance(out.get(sec), dict):
                for k in champs:
                    if src.get(k) is not None:
                        out[sec][k] = src[k]
        if sauvegarde.get("infos") is not None:
            out["infos"] = sauvegarde["infos"]
    # Totaux dépendant de champs manuels : recalcul après fusion.
    s = out.get("souterrain")
    if isinstance(s, dict):
        vtl = _n0(s.get("vtl_ml")) if s.get("vtl_visible", True) else 0
        s["total_ml"] = (_n0(s.get("blo_ml")) + _n0(s.get("free_ml"))
                         + _n0(s.get("tiers_ml")) + _n0(s.get("gc_ml")) + vtl)
    b = out.get("boites")
    if isinstance(b, dict):
        b["total"] = sum(_n0(b.get(k)) for k in ("ofdc", "tenio", "t0", "t1"))
    return out


# ---------------------------------------------------------------------------
# 2. Rapport APD_HTL (PDF)
# ---------------------------------------------------------------------------

class _APD(FPDF):
    code = ""
    type_etude = ""

    def header(self):
        # bandeau logos + type
        try:
            self.image(os.path.join(ASSETS, "logo_free.png"), 10, 8, 28)
        except Exception:
            pass
        try:
            self.image(os.path.join(ASSETS, "logo_ensio.png"), self.w - 38, 8, 28)
        except Exception:
            pass
        self.set_y(9)
        self.set_font("helvetica", "B", 11)
        self.cell(0, 6, self.code, align="C")
        self.ln(5)
        self.set_font("helvetica", "", 8)
        self.set_text_color(90, 90, 90)
        self.cell(0, 5, self.type_etude, align="C")
        self.set_text_color(0, 0, 0)
        self.ln(9)

    def footer(self):
        self.set_y(-12)
        self.set_font("helvetica", "", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"{self.code}   -   Page {self.page_no()}", align="C")
        self.set_text_color(0, 0, 0)


def _ligne(pdf, label, valeur, unite="", gras_total=False):
    pdf.set_font("helvetica", "B" if gras_total else "", 9.5)
    pdf.set_fill_color(245, 247, 249) if gras_total else pdf.set_fill_color(255, 255, 255)
    pdf.cell(120, 6.5, "  " + label, border="B", fill=gras_total)
    txt = "" if valeur in (None, "") else f"{valeur}{(' ' + unite) if unite else ''}"
    pdf.set_font("helvetica", "B" if gras_total else "", 9.5)
    pdf.cell(0, 6.5, txt + "  ", border="B", align="R", fill=gras_total)
    pdf.ln(6.5)


def _titre_section(pdf, titre):
    pdf.ln(2)
    pdf.set_font("helvetica", "B", 10.5)
    pdf.set_fill_color(15, 118, 110)  # brand-700
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 7, "  " + titre, fill=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(8)


def generer_rapport_apd(donnees: dict, chemin_pdf: str) -> str:
    """Génère le rapport APD_HTL (cartouche + Caractéristiques de la liaison)."""
    c = donnees.get("cartouche", {})
    s = donnees.get("souterrain", {})
    a = donnees.get("aerien", {})
    ap = donnees.get("appuis", {})
    b = donnees.get("boites", {})

    pdf = _APD(orientation="P", unit="mm", format="A4")
    pdf.code = c.get("code_projet", "")
    pdf.type_etude = c.get("type_etude", "APD HTL")
    pdf.set_auto_page_break(True, margin=15)
    pdf.set_margins(12, 12, 12)

    # ---------- PAGE 1 : cartouche ----------
    pdf.add_page()
    pdf.ln(6)
    pdf.set_font("helvetica", "B", 20)
    pdf.cell(0, 12, "PLAN", align="C")
    pdf.ln(16)
    pdf.set_font("helvetica", "B", 12)
    adresse = c.get("adresse", "")
    ligne_adr = adresse
    if c.get("cp") or c.get("commune"):
        ligne_adr = f"{adresse}  -  {c.get('cp','')} {c.get('commune','')}".strip(" -")
    pdf.multi_cell(0, 8, ligne_adr, align="C")
    pdf.ln(12)

    # encadré infos
    infos = [
        ("Date de réalisation", ("Le " + c.get("date_real", "")) if c.get("date_real") else ""),
        ("Fait par", c.get("fait_par", "")),
        ("Code projet du site", c.get("code_projet", "")),
        ("Type d'étude", c.get("type_etude", "")),
        ("Version du document", c.get("version", "")),
    ]
    pdf.set_x(45)
    largeur = pdf.w - 2 * 45
    for lab, v in infos:
        x = pdf.get_x()
        pdf.set_font("helvetica", "B", 10)
        pdf.cell(largeur * 0.5, 9, "  " + lab, border=1)
        pdf.set_font("helvetica", "", 10)
        pdf.cell(largeur * 0.5, 9, "  " + str(v), border=1)
        pdf.ln(9)
        pdf.set_x(45)

    # ---------- PAGE 2 : Caractéristiques de la liaison ----------
    pdf.add_page()
    pdf.set_font("helvetica", "B", 13)
    pdf.cell(0, 9, "Caractéristiques de la liaison", align="C")
    pdf.ln(12)

    def _n(v):
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return 0

    _titre_section(pdf, "Réseaux souterrains")
    _ligne(pdf, "BLO", f"{_n(s.get('blo_ml'))} ml + {_n(s.get('blo_ch'))} chambre(s) traversée(s)")
    _ligne(pdf, "Réseaux FREE", f"{_n(s.get('free_ml'))} ml + {_n(s.get('free_ch'))} chambre(s) traversée(s)")
    tn = s.get("tiers_nom") or "à préciser"
    _ligne(pdf, f"Réseaux Tiers ({tn})", f"{_n(s.get('tiers_ml'))} ml + {_n(s.get('tiers_ch'))} chambre(s)")
    _ligne(pdf, "GC", f"{_n(s.get('gc_ml'))} ml + {_n(s.get('gc_ch'))} chambre(s) traversée(s)")
    vtl_visible = s.get("vtl_visible", True)
    if vtl_visible:
        _ligne(pdf, "VTL", _n(s.get("vtl_ml")), "ml")
    # TOTAL = somme des lignes ci-dessus
    total_sout = (_n(s.get("blo_ml")) + _n(s.get("free_ml")) + _n(s.get("tiers_ml"))
                  + _n(s.get("gc_ml")) + (_n(s.get("vtl_ml")) if vtl_visible else 0))
    _ligne(pdf, "TOTAL réseaux souterrain", total_sout, "ml", gras_total=True)
    _ligne(pdf, "TOTAL souterrain + love manchon", _n(s.get("total_love_ml")), "ml", gras_total=True)

    _titre_section(pdf, "Réseaux aériens")
    _ligne(pdf, "AERIEN FREE", f"{a.get('free_ml',0)} ml + {a.get('free_ap',0)} appui(s)")
    _ligne(pdf, "AERIEN ORANGE", f"{a.get('orange_ml',0)} ml + {a.get('orange_ap',0)} appui(s)")
    _ligne(pdf, "AERIEN ENEDIS", f"{a.get('enedis_ml',0)} ml + {a.get('enedis_ap',0)} appui(s)")
    tna = a.get("tiers_nom") or "à préciser"
    _ligne(pdf, f"AERIEN TIERS ({tna})", f"{a.get('tiers_ml',0)} ml + {a.get('tiers_ap',0)} appui(s)")
    _ligne(pdf, "TOTAL réseaux aériens", f"{a.get('total_ml',0)} ml + {a.get('total_ap',0)} appui(s)", gras_total=True)
    _ligne(pdf, "TOTAL aérien + love manchon", a.get("total_love_ml", 0), "ml", gras_total=True)
    _ligne(pdf, "Nombre d'appuis à remplacer", ap.get("remplacer", 0))
    _ligne(pdf, "Nombre d'appuis à recaler / renforcer", ap.get("recaler", 0))
    _ligne(pdf, "Nombre d'appuis à implanter", ap.get("implanter", 0))

    _titre_section(pdf, "Boîtes")
    _ligne(pdf, "Nombre de boîte FREE OFDC", b.get("ofdc", 0))
    _ligne(pdf, "Nombre de boîte FREE TENIO", b.get("tenio", 0))
    _ligne(pdf, "Nombre de boîte FREE 3M T0", b.get("t0", 0))
    _ligne(pdf, "Nombre de boîte FREE 3M T1", b.get("t1", 0))
    _ligne(pdf, "TOTAL boîtes", b.get("total", 0), gras_total=True)

    infos_txt = (donnees.get("infos") or "").strip()
    _titre_section(pdf, "Informations remarquables")
    pdf.set_font("helvetica", "", 9.5)
    pdf.multi_cell(0, 5.5, infos_txt if infos_txt else "-")

    os.makedirs(os.path.dirname(chemin_pdf), exist_ok=True)
    pdf.output(chemin_pdf)
    logger.info(f"Rapport APD généré : {chemin_pdf}")
    return chemin_pdf
