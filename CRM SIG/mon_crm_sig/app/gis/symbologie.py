"""
symbologie.py - Source de vérité unique de la symbologie NETGEO.

Reproduit fidèlement les renderers du projet QGIS de production (couleurs,
formes, tailles, règles de catégorisation, expressions d'étiquettes) pour :
  - la génération des plans backend (matplotlib) — `app/reporting/plan_generator.py`
  - la carte Leaflet du CRM — endpoint /geojson + `map_view.html`

Le CRM ne dépend PLUS de QGIS : tout est rendu depuis les SHP livrables.

Chaque `style_*(row)` renvoie un dict :
  point   : {geom:'point',  forme, couleur(rgb), contour(rgb|None), taille_mm,
             contour_mm, categorie, label}
  ligne   : {geom:'ligne',  couleur, largeur_mm, style, traits(list), categorie, label}
  polygone: {geom:'polygone', remplissage(rgba|None), contour, contour_mm, style, label}
"""

import math

# ---------------------------------------------------------------------------
# Couleurs (RGB 0-255) — valeurs EXACTES du projet QGIS
# ---------------------------------------------------------------------------

def hexa(c):
    return "#%02x%02x%02x" % (c[0], c[1], c[2])


def mpl(c):
    return (c[0] / 255, c[1] / 255, c[2] / 255)


NOIR = (0, 0, 0)
BLANC = (255, 255, 255)
CONTOUR_SOMBRE = (35, 35, 35)

# BPE
BPE_A_CREER = (0, 102, 255)
BPE_EXISTANT = (0, 153, 0)

# PT — chambres (losange) / poteaux (flèche)
PT_CHAMBRE_FREE_CREER = (196, 60, 57)
PT_CHAMBRE_FREE_EXIST = (28, 31, 219)
PT_CHAMBRE_ORANGE = (231, 113, 42)
PT_CHAMBRE_TIERS = (163, 72, 182)
PT_POTEAU_FREE_CREER = (255, 5, 1)
PT_POTEAU_FREE_EXIST = (0, 0, 255)
PT_POTEAU_FT = (255, 94, 1)
# Poteau FT par nature des travaux (annexes C6/C7)
PT_POTEAU_FT_REMPL = (255, 140, 0)
PT_POTEAU_FT_RENF = (196, 82, 6)
PT_POTEAU_FT_IRR = (139, 20, 12)

# Sites
BTS_COUL = (120, 214, 25)
NRA_COUL = (20, 116, 206)
NRO_COUL = (222, 103, 163)

# BLOCAGE
BLOCAGE_FILL = (133, 182, 111)
BLOCAGE_CONTOUR = (255, 0, 10)

# CABLES par SYMBOLISAT
CABLE_COULEURS = {
    "CAD": (82, 193, 96),
    "CTR": (221, 0, 0),
    "CDI": (255, 231, 93),
    "CDD": (0, 0, 0),
    "BAG": (154, 21, 206),
    "FON": (0, 102, 255),
    "CAB": (233, 72, 163),
    "CBM": (51, 160, 44),
    "CIM": (255, 255, 255),
}
CABLE_LISERE = (35, 35, 35)  # liseré noir du CTR

# SUPPORT par catégorie
SUPPORT_COULEURS = {
    "BLO ORANGE": (255, 1, 26),
    "BLO ORANGE UNITAIRE": (255, 1, 26),
    "AERIEN FT": (107, 83, 30),
    "GC FREE EXISTANT": (0, 0, 252),
    "GC FREE A CREER": (0, 255, 255),
    "AERIEN FREE": (249, 66, 158),
    "GC PRIVE/OP TIERS": (208, 1, 255),
    "ENEDIS AERIEN": (255, 158, 23),
}

COMMUNE_CONTOUR = (0, 0, 0)

# Couleurs d'étiquettes
LBL_ROUGE = (255, 0, 0)
LBL_BLEU = (0, 0, 255)
LBL_VERT = (0, 255, 0)
LBL_VIOLET = (77, 1, 255)
LBL_MAGENTA = (245, 49, 255)
LBL_JAUNE = (243, 214, 23)


# ---------------------------------------------------------------------------
# Helpers valeurs
# ---------------------------------------------------------------------------

def _v(row, champ):
    x = row.get(champ) if hasattr(row, "get") else None
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return ""
    return str(x).strip()


def _up(row, champ):
    return _v(row, champ).upper()


def _num(row, champ):
    try:
        return float(row.get(champ))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Catégorisation (reproduit les CASE QGIS)
# ---------------------------------------------------------------------------

def _cat_poteau_nature(nature):
    """Catégorie de poteau FT selon la nature des travaux (annexes C6/C7)."""
    n = (nature or "").upper()
    if "REMPLAC" in n:
        return "POTEAU FT REMPLACEMENT"
    if "RECAL" in n or "RENFORC" in n:
        return "POTEAU FT RENFORCEMENT/RECALAGE"
    if "IRREMPLAC" in n:
        return "POTEAU FT IRREMPLACABLE"
    return None


def categorie_pt(row, nature=None):
    struc = _up(row, "TYPE_STRUC")
    prop = _up(row, "PROPRIETAI")
    etat = _up(row, "ETAT")
    poteau = "POTEAU" in struc or "POTELET" in struc
    free = "FREE" in prop
    ftorange = ("FT" in prop) or ("ORANGE" in prop)
    etude = "ETUDE" in etat
    if poteau:
        if free:
            return "POTEAU FREE A CRÉER" if etude else "POTEAU FREE EXISTANT"
        return _cat_poteau_nature(nature) or "POTEAU FT"
    if free:
        return "CHAMBRE FREE A CRÉER" if etude else "CHAMBRE FREE EXISTANTE"
    if ftorange:
        return "CHAMBRE ORANGE"
    return "CHAMBRE PRIVE/OP TIERS"


# Chambres = carré, poteaux = flèche (comme le plan de référence).
_PT_STYLE = {
    "CHAMBRE FREE A CRÉER": ("square", PT_CHAMBRE_FREE_CREER, 3.6),
    "CHAMBRE FREE EXISTANTE": ("square", PT_CHAMBRE_FREE_EXIST, 3.6),
    "CHAMBRE ORANGE": ("square", PT_CHAMBRE_ORANGE, 3.6),
    "CHAMBRE PRIVE/OP TIERS": ("square", PT_CHAMBRE_TIERS, 3.6),
    "POTEAU FREE A CRÉER": ("arrow", PT_POTEAU_FREE_CREER, 4.3),
    "POTEAU FREE EXISTANT": ("arrow", PT_POTEAU_FREE_EXIST, 4.3),
    "POTEAU FT": ("arrow", PT_POTEAU_FT, 4.3),
    "POTEAU FT REMPLACEMENT": ("arrow", PT_POTEAU_FT_REMPL, 4.3),
    "POTEAU FT RENFORCEMENT/RECALAGE": ("arrow", PT_POTEAU_FT_RENF, 4.3),
    "POTEAU FT IRREMPLACABLE": ("arrow", PT_POTEAU_FT_IRR, 4.3),
}

# Ordres canoniques pour la légende (dynamique).
_ORDRE_CABLE = ["CAD", "CTR", "CDI", "CDD", "BAG", "FON", "CAB", "CBM", "CIM"]
_ORDRE_PT = ["CHAMBRE FREE A CRÉER", "CHAMBRE FREE EXISTANTE", "CHAMBRE ORANGE",
             "CHAMBRE PRIVE/OP TIERS", "POTEAU FREE A CRÉER", "POTEAU FREE EXISTANT",
             "POTEAU FT", "POTEAU FT REMPLACEMENT", "POTEAU FT RENFORCEMENT/RECALAGE",
             "POTEAU FT IRREMPLACABLE"]
_ORDRE_SUPPORT = ["BLO ORANGE", "BLO ORANGE UNITAIRE", "AERIEN FT", "GC FREE EXISTANT",
                  "GC FREE A CREER", "AERIEN FREE", "GC PRIVE/OP TIERS", "ENEDIS AERIEN"]
_LIB_SUPPORT = {"GC FREE A CREER": "GC FREE A CRÉER"}  # libellé affiché


def categorie_support(row):
    prop = _up(row, "PROPRIETAI")
    struc = _up(row, "TYPE_STRUC")
    compo = _up(row, "COMPOSITIO")
    aerien = "AERIEN" in struc or "FACADE" in struc
    if "ENEDIS" in prop:
        return "ENEDIS AERIEN" if aerien else "AERIEN FT"
    if "FREE" in prop:
        if aerien:
            return "AERIEN FREE"
        return "GC FREE A CREER" if compo.startswith("GC FREE") else "GC FREE EXISTANT"
    if "PRIVE" in prop or "TIERS" in prop:
        return "GC PRIVE/OP TIERS"
    if aerien:
        return "AERIEN FT"
    return "BLO ORANGE UNITAIRE" if "CIMENT" in compo else "BLO ORANGE"


def symbolisat_cable(row):
    s = _up(row, "SYMBOLISAT")
    if s in CABLE_COULEURS:
        return s
    nom = _up(row, "NOM") or _up(row, "CODE")
    for p in CABLE_COULEURS:
        if nom.startswith(p):
            return p
    return "CTR"


# ---------------------------------------------------------------------------
# Étiquettes (expressions QGIS reproduites)
# ---------------------------------------------------------------------------

def _rnd(row, champ):
    v = _num(row, champ)
    return "" if v is None else str(int(round(v)))


def label_pt(row):
    etat = _up(row, "ETAT")
    if etat in ("REMPLACEMENT", "RENFORCEMENT/RECALAGE", "IRREMPLACABLE"):
        return "\n".join(x for x in (_v(row, "CODE"), _v(row, "REF_CHAMBR"), etat) if x)
    return "\n".join(x for x in (_v(row, "NOM"), _v(row, "REF_CHAMBR")) if x)


def label_cable(row):
    lg = _rnd(row, "LONGUEUR_R")
    base = _v(row, "NOM")
    return f"{base} - {lg}ml" if lg else base


def label_support(row):
    return f"{_v(row,'ORDRE')}/ {_v(row,'COMPOSITIO')} / {_rnd(row,'LGR_REEL')}ml".strip("/ ")


# ---------------------------------------------------------------------------
# Styles par couche
# ---------------------------------------------------------------------------

def style_bpe(row):
    a_creer = "ETUDE" in _up(row, "ETAT") or "SERVICE" not in _up(row, "ETAT")
    coul = BPE_A_CREER if a_creer else BPE_EXISTANT
    return {"geom": "point", "forme": "circle", "couleur": coul,
            "contour": CONTOUR_SOMBRE, "taille_mm": 2.2, "contour_mm": 0.2,
            "categorie": "A CREER" if a_creer else "EXISTANT",
            "label": _v(row, "NOM"), "label_couleur": LBL_ROUGE}


def style_pt(row, nature=None):
    cat = categorie_pt(row, nature)
    forme, coul, taille = _PT_STYLE[cat]
    contour = coul if "CHAMBRE" in cat else CONTOUR_SOMBRE
    return {"geom": "point", "forme": forme, "couleur": coul, "contour": contour,
            "taille_mm": taille, "contour_mm": 0.4 if "CHAMBRE" in cat else 0.0,
            "categorie": cat, "label": label_pt(row), "label_couleur": NOIR}


def style_bts(row):
    return {"geom": "point", "forme": "triangle", "couleur": BTS_COUL,
            "contour": CONTOUR_SOMBRE, "taille_mm": 4.0, "contour_mm": 0.0,
            "categorie": "BTS", "label": _v(row, "REF_PHFM") or _v(row, "NOM"),
            "label_couleur": LBL_BLEU}


def style_nra(row):
    return {"geom": "point", "forme": "triangle", "couleur": NRA_COUL,
            "contour": CONTOUR_SOMBRE, "taille_mm": 4.0, "contour_mm": 0.0,
            "categorie": "NRA", "label": _v(row, "NOM"), "label_couleur": LBL_BLEU}


def style_nro(row):
    return {"geom": "point", "forme": "triangle", "couleur": NRO_COUL,
            "contour": CONTOUR_SOMBRE, "taille_mm": 4.0, "contour_mm": 0.0,
            "categorie": "NRO_RIP", "label": _v(row, "CODE"), "label_couleur": LBL_BLEU}


def style_blocage(row):
    return {"geom": "point", "forme": "cross", "couleur": BLOCAGE_FILL,
            "contour": BLOCAGE_CONTOUR, "taille_mm": 4.0, "contour_mm": 0.5,
            "categorie": "BLOCAGE", "label": _v(row, "TYPE"), "label_couleur": LBL_ROUGE}


def style_cable(row):
    sym = symbolisat_cable(row)
    coul = CABLE_COULEURS.get(sym, CABLE_COULEURS["CTR"])
    traits = []
    if sym == "CTR":  # trait rouge + 2 liserés noirs (triple)
        traits = [(CABLE_LISERE, 0.25, 0.4), (CABLE_LISERE, 0.25, -0.4)]
    return {"geom": "ligne", "couleur": coul, "largeur_mm": 0.4, "style": "solid",
            "traits": traits, "categorie": sym, "label": label_cable(row),
            "label_couleur": LBL_VIOLET}


def style_support(row):
    cat = categorie_support(row)
    coul = SUPPORT_COULEURS.get(cat, SUPPORT_COULEURS["BLO ORANGE"])
    return {"geom": "ligne", "couleur": coul, "largeur_mm": 2.0, "style": "solid",
            "traits": [], "categorie": cat, "label": label_support(row),
            "label_couleur": NOIR}


def style_commune(row):
    return {"geom": "polygone", "remplissage": None, "contour": COMMUNE_CONTOUR,
            "contour_mm": 0.6, "style": "dot", "categorie": "",
            "label": _v(row, "NOM"), "label_couleur": LBL_JAUNE}


# Table : nom de couche -> fonction de style
STYLE_COUCHE = {
    "BPE": style_bpe,
    "PT": style_pt,
    "BTS": style_bts,
    "NRA": style_nra,
    "NRO_RIP": style_nro,
    "BLOCAGE": style_blocage,
    "CABLES": style_cable,
    "SUPPORT": style_support,
    "COMMUNE": style_commune,
}

# Ordre de dessin (bas -> haut), comme l'arbre QGIS.
ORDRE_DESSIN = ["COMMUNE", "SUPPORT", "CABLES", "PT", "BPE", "NRA", "NRO_RIP", "BTS", "BLOCAGE"]


def style_de(nom_couche, row, nature=None):
    """Style d'une entité pour une couche (nom sans préfixe [Livrable]).

    ``nature`` : nature des travaux du poteau (annexes C6/C7), pour colorer les
    poteaux FT à remplacer / recaler-renforcer / irremplaçables."""
    base = (nom_couche or "").upper().replace("[LIVRABLE]", "").strip()
    fn = STYLE_COUCHE.get(base)
    if fn is None:
        return None
    if base == "PT":
        return fn(row, nature)
    return fn(row)


def est_stylee(nom_couche):
    base = (nom_couche or "").upper().replace("[LIVRABLE]", "").strip()
    return base in STYLE_COUCHE


def style_web(nom_couche, row, nature=None):
    """Style compact (couleurs hex) pour Leaflet — injecté dans le GeoJSON.

    Transporte aussi l'étiquette (``lbl``) et sa couleur (``lc``) afin que la
    carte interactive affiche les mêmes libellés que les plans PDF.
    """
    st = style_de(nom_couche, row, nature)
    if not st:
        return None
    if st["geom"] == "point":
        d = {"g": "pt", "forme": st["forme"], "coul": hexa(st["couleur"]),
             "cont": hexa(st["contour"]) if st.get("contour") else None,
             "taille": st["taille_mm"]}
    elif st["geom"] == "ligne":
        d = {"g": "ln", "coul": hexa(st["couleur"]), "larg": st["largeur_mm"],
             "casing": bool(st.get("traits"))}
    else:
        d = {"g": "pg", "cont": hexa(st["contour"]), "pointille": st["style"] == "dot"}
    lbl = (st.get("label") or "").strip()
    if lbl:
        d["lbl"] = lbl
        d["lc"] = hexa(st["label_couleur"]) if st.get("label_couleur") else "#222222"
    return d


# ---------------------------------------------------------------------------
# Légende (structure identique au plan de référence)
# ---------------------------------------------------------------------------

def _presents(gdf, fn):
    """Liste ordonnée (sans doublon) des catégories présentes dans gdf via fn(row)."""
    vus = []
    if gdf is None:
        return vus
    for _, r in gdf.iterrows():
        c = fn(r)
        if c and c not in vus:
            vus.append(c)
    return vus


def legende_dynamique(couches, natures=None):
    """Légende ne montrant QUE les catégories présentes dans les données importées.

    `couches` : dict {nom_couche: GeoDataFrame|None}. Structure identique au plan
    de référence : SITE / BPE / CABLES / PT / SUPPORT.
    `natures` : {NOM_poteau: nature} (annexes C6/C7) pour les sous-catégories poteau.
    """
    natures = natures or {}
    blocs = []

    def _non_vide(n):
        g = couches.get(n)
        return g if (g is not None and len(g)) else None

    # SITE
    site = []
    if _non_vide("NRA") is not None:
        site.append(("triangle", NRA_COUL, "NRA"))
    if _non_vide("NRO_RIP") is not None:
        site.append(("triangle", NRO_COUL, "NRO_RIP"))
    if _non_vide("BTS") is not None:
        site.append(("triangle", BTS_COUL, "BTS"))
    if site:
        blocs.append(("SITE", site))

    # BPE
    bpe = _non_vide("BPE")
    if bpe is not None:
        cats = {style_bpe(r)["categorie"] for _, r in bpe.iterrows()}
        entrees = []
        if "A CREER" in cats:
            entrees.append(("circle", BPE_A_CREER, "A CREER"))
        if "EXISTANT" in cats:
            entrees.append(("circle", BPE_EXISTANT, "EXISTANT"))
        if entrees:
            blocs.append(("BPE", entrees))

    # CABLES
    cab = _non_vide("CABLES")
    if cab is not None:
        pres = _presents(cab, symbolisat_cable)
        entrees = [("ligne", CABLE_COULEURS[s], s) for s in _ORDRE_CABLE if s in pres]
        if entrees:
            blocs.append(("CABLES", entrees))

    # PT (poteaux FT sous-catégorisés par nature des travaux si annexes fournies)
    pt = _non_vide("PT")
    if pt is not None:
        pres = []
        for _, r in pt.iterrows():
            nat = natures.get(str(r.get("NOM") or "").strip()) if natures else None
            c = categorie_pt(r, nat)
            if c and c not in pres:
                pres.append(c)
        entrees = [(_PT_STYLE[c][0], _PT_STYLE[c][1], c) for c in _ORDRE_PT if c in pres]
        if entrees:
            blocs.append(("PT", entrees))

    # SUPPORT
    sup = _non_vide("SUPPORT")
    if sup is not None:
        pres = _presents(sup, categorie_support)
        entrees = [("ligne", SUPPORT_COULEURS[c], _LIB_SUPPORT.get(c, c))
                   for c in _ORDRE_SUPPORT if c in pres]
        if entrees:
            blocs.append(("SUPPORT", entrees))

    return blocs
