"""
plan_generator.py - Plan Synoptique (PDF A3 paysage) 100 % backend, sans QGIS.

Reproduit la mise en page « SYNO-PAYSAGE-A3 » du gabarit de production ENSIO,
calée sur le livrable client de référence (SYNO_MES21_231205.pdf) :
  - fond de carte satellite hybride (tuiles XYZ, comme le projet QGIS) ;
  - câbles fins par ETAT avec étiquettes violettes CODE / longueur / référence
    reliées par des lignes de rappel pointillées ;
  - BPE en carrés magenta (chambre SAT) / orange (boîte FT), étiquettes
    magenta « NOM ⏎ PT / modèle PT ⏎ modèle BPE » ;
  - sites en triangles (NRA bleu, NRO_RIP rose, BTS rouge) ;
  - cartouche 23 mm : [logo free | titre | rose des vents | légende | ensio],
    positions issues du gabarit QGIS (SYNO-PAYSAGE-A3).

Styles, expressions d'étiquetage et logos extraits de CODE_PROJET.qgz.
Dépendances : geopandas + matplotlib (+ tuiles via urllib, avec repli hors-ligne).
"""

import io
import os
import math
import logging
import urllib.request

import matplotlib
matplotlib.use("Agg")  # rendu hors écran (serveur)
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import matplotlib.patheffects as pe
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.path import Path as MplPath

import numpy as np
import geopandas as gpd

from app.gis import symbologie as symb

logger = logging.getLogger("crm_sig.plan_generator")

ASSETS = os.path.join(os.path.dirname(__file__), "assets_plan")

MM = 1 / 25.4          # mm -> pouces
PT_PAR_MM = 2.834645   # mm -> points

# Fond de carte XYZ (celui du projet QGIS : « Google Hybrid »), surchargeable.
# Fond Plan IGN (Géoplateforme IGN, WMTS Web-Mercator) — livrable APD.
FOND_IGN_URL = os.environ.get(
    "CRM_SIG_FOND_IGN",
    "https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile"
    "&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&TILEMATRIXSET=PM"
    "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&STYLE=normal&FORMAT=image/png",
)

FOND_XYZ_URL = os.environ.get(
    "CRM_SIG_SYNO_FOND",
    "http://mt0.google.com/vt/lyrs=y&hl=fr&x={x}&y={y}&z={z}",
)

# ---------------------------------------------------------------------------
# SYMBOLOGIE (valeurs exactes du gabarit ENSIO - CODE_PROJET.qgs)
# ---------------------------------------------------------------------------

def _rgb(r, g, b):
    return (r / 255, g / 255, b / 255)

STYLE_SUPPORT = {
    "BLO ORANGE":        (_rgb(255, 1, 26), 2.0),
    "AERIEN FT":         (_rgb(107, 83, 30), 2.0),
    "AERIEN FREE":       (_rgb(249, 66, 158), 2.0),
    "GC FREE EXISTANT":  (_rgb(0, 0, 252), 2.0),
    "GC FREE A CREER":   (_rgb(0, 255, 255), 2.0),
    "GC PRIVE/OP TIERS": (_rgb(208, 1, 255), 2.0),
    "ENEDIS AERIEN":     (_rgb(255, 158, 23), 2.0),
    "AUTRE":             (_rgb(120, 120, 120), 1.2),
}

STYLE_CABLES_ETAT = {
    "EN SERVICE": ("CABLE TIRE",       _rgb(42, 223, 39), 0.5),
    "EN ETUDE":   ("CABLE A LANCER",   _rgb(29, 45, 219), 0.5),
    "":           ("CABLE EN TRAVAUX", _rgb(255, 158, 23), 0.4),
}

CABLE_LABEL   = _rgb(77, 1, 255)     # étiquette câble (violet)
BPE_LABEL     = _rgb(245, 49, 255)   # étiquette BPE (magenta)
BPE_CARRE_SAT   = _rgb(193, 6, 190)  # BOITE CHAMBRE SAT (PT Free)
BPE_CARRE_BOITE = _rgb(232, 178, 28) # BOITE (PT FT)

NRA_COULEUR     = _rgb(20, 116, 206)
NRO_RIP_COULEUR = _rgb(222, 103, 163)
BTS_COULEUR     = _rgb(248, 64, 13)

BLANC = (1, 1, 1)


def _tampon(taille_mm, couleur=BLANC):
    return [pe.withStroke(linewidth=taille_mm * 2 * PT_PAR_MM, foreground=couleur)]


def _lire(dossier, nom):
    p = os.path.join(dossier, f"{nom}.shp")
    if not os.path.exists(p):
        return None
    try:
        g = gpd.read_file(p)
        return g if len(g) else None
    except Exception as e:
        logger.warning(f"Plan : lecture {nom}.shp impossible ({e})")
        return None


def _val(row, champ):
    v = row.get(champ)
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v).strip()


def _categorie_support(row):
    prop = _val(row, "PROPRIETAI").upper()
    struc = _val(row, "TYPE_STRUC").upper()
    compo = _val(row, "COMPOSITIO").upper()
    aerien = "AERIEN" in struc or "FACADE" in struc
    if "ENEDIS" in prop:
        return "ENEDIS AERIEN" if aerien else "AUTRE"
    if "FREE" in prop:
        if aerien:
            return "AERIEN FREE"
        return "GC FREE A CREER" if compo.startswith("GC FREE") else "GC FREE EXISTANT"
    if "PRIVE" in prop or "TIERS" in prop:
        return "GC PRIVE/OP TIERS"
    if aerien:
        return "AERIEN FT"
    return "BLO ORANGE"


def _titre_ensio(bts, nra, nro):
    """Titre du cartouche selon la convention ENSIO (BTS -> NRO -> NRA)."""
    def _adresse(row):
        ad, cp, vi = _val(row, "ADRESSE"), _val(row, "CP"), _val(row, "VILLE")
        return f"{ad} - {cp}\n{vi}".strip(" -\n")
    if bts is not None and len(bts):
        r = bts.iloc[0]
        ref = _val(r, "REF_PHFM") or _val(r, "NOM")
        return f"{ref[:19]}\n{_adresse(r)}"
    if nro is not None and len(nro):
        r = nro.iloc[0]
        return f"{_val(r, 'CODE')}\n{_adresse(r)}"
    if nra is not None and len(nra):
        r = nra.iloc[0]
        return f"{_val(r, 'NOM')}\n{_adresse(r)}"
    return ""


def _pas_echelle(largeur_m):
    cible = largeur_m / 5
    base = 10 ** math.floor(math.log10(max(cible, 1)))
    for k in (1, 2, 2.5, 5, 10):
        if base * k >= cible:
            return base * k
    return base * 10


# ---------------------------------------------------------------------------
# FOND DE CARTE (tuiles XYZ Web-Mercator, comme la couche du projet QGIS)
# ---------------------------------------------------------------------------

_RAYON = 6378137.0
_ORIG = math.pi * _RAYON  # demi-circonférence Mercator


def _tuile_de(x3857, y3857, z):
    n = 2 ** z
    tx = int((x3857 + _ORIG) / (2 * _ORIG) * n)
    ty = int((_ORIG - y3857) / (2 * _ORIG) * n)
    return max(0, min(n - 1, tx)), max(0, min(n - 1, ty))


def _emprise_tuile(tx, ty, z):
    n = 2 ** z
    x0 = -_ORIG + tx * (2 * _ORIG) / n
    x1 = -_ORIG + (tx + 1) * (2 * _ORIG) / n
    y1 = _ORIG - ty * (2 * _ORIG) / n
    y0 = _ORIG - (ty + 1) * (2 * _ORIG) / n
    return x0, y0, x1, y1


def _fond_carte(ax, x0, y0, x1, y1, url=None):
    """Mosaïque de tuiles XYZ sous l'emprise (EPSG:3857). Repli silencieux hors-ligne."""
    url = url or FOND_XYZ_URL
    if not url or url.lower() in ("aucun", "none", "off"):
        return False
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Plan : Pillow absent, fond de carte ignoré.")
        return False

    largeur = x1 - x0
    # zoom visant ~2300 px de large (netteté du livrable de référence) ;
    # le fond est ensuite recompressé en JPEG dans le PDF (voir _compresser_fond).
    z = 19
    for cand in range(10, 20):
        px = largeur / ((2 * _ORIG) / (2 ** cand) / 256)
        if px >= 2000:
            z = cand
            break
    z = min(z, 19)

    tx0, ty0 = _tuile_de(x0, y1, z)
    tx1, ty1 = _tuile_de(x1, y0, z)
    nx, ny = tx1 - tx0 + 1, ty1 - ty0 + 1
    if nx * ny > 200:  # garde-fou
        logger.warning(f"Plan : trop de tuiles ({nx*ny}), fond ignoré.")
        return False

    mosaique = Image.new("RGB", (nx * 256, ny * 256), (240, 240, 240))

    def _fetch(ij):
        i, j = ij
        u = (url.replace("{x}", str(tx0 + i)).replace("{y}", str(ty0 + j))
                .replace("{z}", str(z)))
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "GeoCRM-SIG/1.0"})
            with urllib.request.urlopen(req, timeout=8) as rep:
                return i, j, Image.open(io.BytesIO(rep.read())).convert("RGB")
        except Exception:
            return i, j, None

    from concurrent.futures import ThreadPoolExecutor
    ok = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        for i, j, im in ex.map(_fetch, [(i, j) for i in range(nx) for j in range(ny)]):
            if im is not None:
                mosaique.paste(im, (i * 256, j * 256))
                ok += 1
    if ok == 0:
        logger.warning("Plan : aucune tuile récupérée (hors-ligne ?) — fond blanc.")
        return False

    ex0, _, _, ey1 = _emprise_tuile(tx0, ty0, z)
    _, ey0, ex1, _ = _emprise_tuile(tx1, ty1, z)
    ax.imshow(np.asarray(mosaique), extent=(ex0, ex1, ey0, ey1),
              origin="upper", interpolation="bilinear", zorder=0)
    logger.info(f"Plan : fond de carte {ok}/{nx*ny} tuiles (z{z}).")
    return True


def _compresser_fond(chemin_pdf):
    """Recompresse le fond satellite du PDF en JPEG (qualité 82).

    matplotlib encode les images en Flate (sans perte), inefficace sur de la
    photo aérienne (~20 Mo). On remplace uniquement les grandes images ;
    textes et symboles restent vectoriels. Sans PyMuPDF, on garde le PDF tel quel."""
    try:
        import fitz  # PyMuPDF
        from PIL import Image
    except ImportError:
        logger.warning("Plan : PyMuPDF/Pillow absent — PDF non recompressé.")
        return
    try:
        doc = fitz.open(chemin_pdf)
        modifie = False
        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                base = doc.extract_image(xref)
                if len(base["image"]) < 800_000:  # logos/rose : intacts
                    continue
                im = Image.open(io.BytesIO(base["image"])).convert("RGB")
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=82)
                page.replace_image(xref, stream=buf.getvalue())
                modifie = True
        if modifie:
            tmp = chemin_pdf + ".tmp"
            doc.save(tmp, garbage=4, deflate=True)
            doc.close()
            os.replace(tmp, chemin_pdf)
        else:
            doc.close()
    except Exception as e:
        logger.warning(f"Plan : recompression du fond impossible ({e}).")


# ---------------------------------------------------------------------------
# GÉNÉRATION DU PLAN
# ---------------------------------------------------------------------------

def generer_plan_syno(dossier_shape: str, chemin_pdf: str,
                      orientation: str = "paysage", titre: str = None) -> str:
    """Génère le Plan Synoptique PDF (style production ENSIO) depuis les SHP."""
    noms = ("BPE", "BTS", "CABLES", "COMMUNE", "NRA", "NRO_RIP", "PT", "SUPPORT")
    brut = {n: _lire(dossier_shape, n) for n in noms}
    if all(v is None for v in brut.values()):
        raise ValueError(f"Aucune couche exploitable dans {dossier_shape}")

    # longueurs réelles calculées dans le CRS métrique d'origine (Lambert-93)
    long_cables = None
    if brut["CABLES"] is not None:
        long_cables = [g.length if g is not None else 0 for g in brut["CABLES"].geometry]

    # reprojection en Web-Mercator (grille des tuiles)
    couches = {}
    for n, g in brut.items():
        if g is None:
            couches[n] = None
            continue
        try:
            couches[n] = g.to_crs(3857) if g.crs else g
        except Exception:
            couches[n] = g

    # --- Page A3 (paysage par défaut, comme le livrable client) ---
    l_mm, h_mm = (420, 297) if orientation != "portrait" else (297, 420)
    fig = plt.figure(figsize=(l_mm * MM, h_mm * MM))

    cart_h = 23.0  # hauteur du cartouche (gabarit QGIS : 23 mm)
    ax = fig.add_axes([0.15 / l_mm, (cart_h + 0.6) / h_mm,
                       (l_mm - 0.3) / l_mm, (h_mm - cart_h - 0.75) / h_mm])
    ax.set_xticks([]); ax.set_yticks([])
    for c in ax.spines.values():
        c.set_linewidth(0.8)

    # --- Emprise (bbox des couches d'étude + marge, ajustée au ratio) ---
    bornes = None
    for n in ("CABLES", "SUPPORT", "BPE", "BTS", "PT"):
        g = couches[n]
        if g is None:
            continue
        b = g.total_bounds
        bornes = b if bornes is None else (min(bornes[0], b[0]), min(bornes[1], b[1]),
                                           max(bornes[2], b[2]), max(bornes[3], b[3]))
    if bornes is None:
        bornes = couches["COMMUNE"].total_bounds
    x0, y0, x1, y1 = bornes
    dx, dy = max(x1 - x0, 50), max(y1 - y0, 50)
    x0, x1 = x0 - dx * 0.10, x1 + dx * 0.10
    y0, y1 = y0 - dy * 0.10, y1 + dy * 0.10
    dx, dy = x1 - x0, y1 - y0

    pos = ax.get_position()
    ratio_axes = (pos.height * h_mm) / (pos.width * l_mm)
    if dy / dx < ratio_axes:
        sup = dx * ratio_axes - dy
        y0, y1 = y0 - sup / 2, y1 + sup / 2
    else:
        sup = dy / ratio_axes - dx
        x0, x1 = x0 - sup / 2, x1 + sup / 2
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    ax.set_aspect("equal", adjustable="box")

    # facteur d'échelle local Mercator (mètres terrain = unités carte × cos(lat))
    lat_c = math.degrees(2 * math.atan(math.exp(((y0 + y1) / 2) / _RAYON)) - math.pi / 2)
    k = math.cos(math.radians(lat_c))

    def lw(mm_):
        return mm_ * PT_PAR_MM

    def ms(mm_):
        return mm_ * PT_PAR_MM

    # --- Fond de carte ---
    fond_ok = _fond_carte(ax, x0, y0, x1, y1)
    if not fond_ok and couches["COMMUNE"] is not None:
        couches["COMMUNE"].boundary.plot(ax=ax, color="black", linewidth=lw(0.3), alpha=0.5)

    # --- SUPPORT (infrastructure) ---
    g = couches["SUPPORT"]
    if g is not None:
        for _, r in g.iterrows():
            if r.geometry is None:
                continue
            coul, larg = STYLE_SUPPORT[_categorie_support(r)]
            for ln in getattr(r.geometry, "geoms", [r.geometry]):
                xs, ys = ln.xy
                ax.plot(xs, ys, color=coul, linewidth=lw(larg),
                        solid_capstyle="round", zorder=2)

    # --- CABLES (fins, par ETAT) + étiquettes violettes avec ligne de rappel ---
    g = couches["CABLES"]
    if g is not None:
        n_c = len(g)
        for i, (_, r) in enumerate(g.iterrows()):
            if r.geometry is None:
                continue
            etat = _val(r, "ETAT").upper()
            _, coul, larg = STYLE_CABLES_ETAT.get(etat, STYLE_CABLES_ETAT[""])
            for ln in getattr(r.geometry, "geoms", [r.geometry]):
                xs, ys = ln.xy
                ax.plot(xs, ys, color=coul, linewidth=lw(larg), zorder=3)

            # étiquette : CODE ⏎ longueur ml ⏎ REFERENCE (gabarit « CABLES copier »)
            code = _val(r, "CODE") or _val(r, "NOM")
            longueur = _val(r, "LONGUEUR_R")
            try:
                l_m = float(longueur) if longueur else (long_cables[i] if long_cables else 0)
            except (TypeError, ValueError):
                l_m = long_cables[i] if long_cables else 0
            ref = _val(r, "REFERENCE")
            texte = "\n".join(t for t in (code, f"{l_m:.0f} ml" if l_m else "", ref) if t)
            if not texte:
                continue
            geom = max(getattr(r.geometry, "geoms", [r.geometry]), key=lambda s: s.length)
            m = geom.interpolate(0.5, normalized=True)
            # décalage alterné, ligne de rappel pointillée (style du livrable)
            cote = -1 if (i % 2) else 1
            d_off = (x1 - x0) * 0.045
            tx, ty = m.x + cote * d_off, m.y + d_off * (0.55 + 0.3 * (i % 3))
            # rester dans le cadre de la carte (marge 4 %)
            mx, my = (x1 - x0) * 0.04, (y1 - y0) * 0.05
            tx = min(max(tx, x0 + mx), x1 - mx)
            ty = min(max(ty, y0 + my), y1 - my)
            ax.plot([m.x, tx], [m.y, ty], linestyle=(0, (4, 3)), color=coul,
                    linewidth=lw(0.25), zorder=6)
            ax.text(tx, ty, texte, fontsize=8 * 0.83, color=CABLE_LABEL,
                    fontweight="bold", ha="left" if cote > 0 else "right",
                    va="bottom", zorder=7, path_effects=_tampon(0.6))

    # --- BPE : carrés (SAT magenta / BOITE orange) + étiquette magenta 3 lignes ---
    g = couches["BPE"]
    pt_g = couches["PT"]
    if g is not None:
        for i, (_, r) in enumerate(g.iterrows()):
            p = r.geometry
            if p is None:
                continue
            # PT porteur (le plus proche) -> couleur + lignes d'étiquette
            coul, pt_nom, pt_modele = BPE_CARRE_SAT, "", ""
            if pt_g is not None:
                try:
                    d = pt_g.distance(p)
                    prow = pt_g.iloc[int(d.idxmin())]
                    pt_nom, pt_modele = _val(prow, "NOM"), _val(prow, "MODELE")
                    if _val(prow, "PROPRIETAI").upper() in ("FT", "ORANGE"):
                        coul = BPE_CARRE_BOITE
                except Exception:
                    pass
            ax.plot(p.x, p.y, marker="s", markerfacecolor="none",
                    markeredgecolor=coul, markeredgewidth=lw(0.6),
                    markersize=ms(2.4), linestyle="None", zorder=5)
            # « NOM ⏎ PT / modèle PT ⏎ MODELE » (expression du gabarit)
            l2 = f"{pt_nom} / {pt_modele}".strip(" /")
            texte = "\n".join(t for t in (_val(r, "NOM"), l2, _val(r, "MODELE")) if t)
            if texte:
                dxy = ((6, 6), (6, -26), (-6, 6), (-6, -26))[i % 4]
                ax.annotate(texte, (p.x, p.y), xytext=dxy, textcoords="offset points",
                            fontsize=8 * 0.83, color=BPE_LABEL, fontweight="bold",
                            ha="left" if dxy[0] > 0 else "right", zorder=7,
                            path_effects=_tampon(0.6))

    # --- Sites : triangles + étiquettes noires ---
    for nom_couche, coul, champ_label in (("BTS", BTS_COULEUR, "REF_PHFM"),
                                          ("NRA", NRA_COULEUR, "NOM"),
                                          ("NRO_RIP", NRO_RIP_COULEUR, "CODE")):
        g = couches[nom_couche]
        if g is None:
            continue
        for _, r in g.iterrows():
            p = r.geometry
            if p is None:
                continue
            if p.geom_type != "Point":
                p = p.representative_point()
            ax.plot(p.x, p.y, marker="^", color=coul, markersize=ms(4),
                    markeredgecolor="black", markeredgewidth=lw(0.2),
                    linestyle="None", zorder=5)
            et = _val(r, champ_label) or _val(r, "NOM")
            if nom_couche == "NRA" and et:
                et = f"NRA_{et}" if not et.upper().startswith("NRA") else et
            if et:
                ax.annotate(et, (p.x, p.y), xytext=(6, -12), textcoords="offset points",
                            fontsize=8 * 0.83, color="black", zorder=7,
                            path_effects=_tampon(1.0))

    # --- Échelle graphique (sur carte, bas-gauche — position du gabarit) ---
    largeur_terrain = (x1 - x0) * k
    pas = _pas_echelle(largeur_terrain)
    pas_carte = pas / k
    x_ech = x0 + (x1 - x0) * 0.017
    y_ech = y0 + (y1 - y0) * 0.045
    h_ech = (y1 - y0) * 0.008
    for i, coul in enumerate(("black", "white")):
        ax.add_patch(Rectangle((x_ech + i * pas_carte / 2, y_ech), pas_carte / 2, h_ech,
                               facecolor=coul, edgecolor="black",
                               linewidth=lw(0.2), zorder=8))
    for frac, txt in ((0, "0"), (0.5, f"{pas / 2:g}"), (1, f"{pas:g} m")):
        ax.text(x_ech + frac * pas_carte, y_ech + h_ech * 1.6, txt, fontsize=9,
                ha="center", va="bottom", zorder=8, color="black",
                path_effects=_tampon(0.6))

    # --- CARTOUCHE 23 mm : [free | titre | rose | légende | ensio] ---
    cart = fig.add_axes([0.15 / l_mm, 0.15 / h_mm,
                         (l_mm - 0.3) / l_mm, cart_h / h_mm])
    cart.set_xlim(0, l_mm); cart.set_ylim(0, cart_h)
    cart.set_xticks([]); cart.set_yticks([])
    for c in cart.spines.values():
        c.set_linewidth(1.0)

    # séparations verticales (positions du gabarit QGIS)
    x_titre0, x_titre1 = 39.6, 191.8
    x_rose0, x_rose1 = 198.8, 221.2
    x_leg0, x_leg1 = 228.8, 380.1
    for xc in (x_titre0, x_titre1, x_leg0, x_leg1):
        cart.plot([xc, xc], [0, cart_h], color="black", linewidth=1.0)

    def _logo(nom, xf0, xf1):
        try:
            img = mpimg.imread(os.path.join(ASSETS, nom))
            a = fig.add_axes([(xf0 + 2) / l_mm, 2.5 / h_mm,
                              (xf1 - xf0 - 4) / l_mm, (cart_h - 5) / h_mm])
            a.set_axis_off(); a.imshow(img)
        except Exception as e:
            logger.warning(f"Plan : logo {nom} indisponible ({e})")

    _logo("logo_free.png", 0.15, x_titre0)
    _logo("logo_ensio.png", x_leg1, l_mm - 0.15)
    # rose des vents dans le cartouche (comme le livrable client)
    try:
        rose = mpimg.imread(os.path.join(ASSETS, "rose_nord.png"))
        a = fig.add_axes([(x_rose0 + 1) / l_mm, 1.5 / h_mm,
                          (x_rose1 - x_rose0 - 2) / l_mm, (cart_h - 3) / h_mm])
        a.set_axis_off(); a.imshow(rose)
    except Exception:
        pass

    # titre (mêmes tailles que le livrable : gras, centré)
    t = titre if titre is not None else _titre_ensio(brut["BTS"], brut["NRA"], brut["NRO_RIP"])
    if t:
        cart.text((x_titre0 + x_titre1) / 2, cart_h / 2, t, fontsize=12,
                  fontweight="bold", ha="center", va="center", color="black",
                  linespacing=1.35)

    # légende 3 colonnes : SITES / BPE / CABLES (libellés du gabarit)
    lx, col2, col3 = x_leg0 + 6, x_leg0 + 52, x_leg0 + 98
    y_titre = cart_h - 4.2
    pas_l = 5.4
    cart.text(lx + 8, y_titre, "SITES", fontsize=8, fontweight="bold")
    for i, (coul, txt) in enumerate(((NRA_COULEUR, "NRA"),
                                     (NRO_RIP_COULEUR, "NRO_RIP"),
                                     (BTS_COULEUR, "BTS"))):
        y = y_titre - 4.6 - i * pas_l
        cart.plot(lx + 3, y, marker="^", color=coul, markersize=7,
                  markeredgecolor="black", markeredgewidth=0.4, linestyle="None")
        cart.text(lx + 8, y, txt, fontsize=7, va="center")

    cart.text(col2 + 8, y_titre, "BPE", fontsize=8, fontweight="bold")
    for i, (coul, txt) in enumerate(((BPE_CARRE_SAT, "BOITE CHAMBRE SAT"),
                                     (BPE_CARRE_BOITE, "BOITE"))):
        y = y_titre - 4.6 - i * pas_l
        cart.plot(col2 + 3, y, marker="s", markerfacecolor="none",
                  markeredgecolor=coul, markeredgewidth=1.6, markersize=6,
                  linestyle="None")
        cart.text(col2 + 8, y, txt, fontsize=7, va="center")

    cart.text(col3 + 8, y_titre, "CABLES", fontsize=8, fontweight="bold")
    for i, cle in enumerate(("EN SERVICE", "EN ETUDE", "")):
        libelle, coul, _l = STYLE_CABLES_ETAT[cle]
        y = y_titre - 4.6 - i * pas_l
        cart.add_line(Line2D([col3, col3 + 6], [y, y], color=coul, linewidth=2))
        cart.text(col3 + 8, y, libelle, fontsize=7, va="center")

    # --- Export PDF (+ recompression JPEG du fond satellite) ---
    os.makedirs(os.path.dirname(chemin_pdf), exist_ok=True)
    est_png = chemin_pdf.lower().endswith(".png")
    fig.savefig(chemin_pdf, format="png" if est_png else "pdf", dpi=200 if est_png else 300)
    plt.close(fig)
    if not est_png:
        _compresser_fond(chemin_pdf)   # recompression JPEG du fond (PDF uniquement)
    logger.info(f"Plan Synoptique genere : {chemin_pdf}")
    return chemin_pdf


# ===========================================================================
# PLAN APD (APS/APD_GENERAL_PAYSAGE) — 100 % backend, symbologie QGIS exacte
# ===========================================================================

# Marqueur « flèche » (poteau) reproduit de QGIS.
_ARROW = MplPath(
    [(-0.28, -1.0), (-0.28, 0.15), (-0.62, 0.15), (0.0, 1.0),
     (0.62, 0.15), (0.28, 0.15), (0.28, -1.0), (-0.28, -1.0)],
    [MplPath.MOVETO, MplPath.LINETO, MplPath.LINETO, MplPath.LINETO,
     MplPath.LINETO, MplPath.LINETO, MplPath.LINETO, MplPath.CLOSEPOLY],
)
_MARQUEURS = {"circle": "o", "diamond": "D", "square": "s", "triangle": "^",
              "cross": "X", "arrow": _ARROW}


def _dessiner_couches(ax, couches, x0, y0, x1, y1, lw, ms, natures=None):
    """Dessine les couches selon la symbologie NETGEO (ordre QGIS).

    Comme le plan de référence : SUPPORT non étiqueté, câbles étiquetés le long
    de la ligne, étiquettes de points anti-chevauchement (filtre de distance).
    ``natures`` : {NOM_poteau: nature} (annexes C6/C7) pour colorer les poteaux FT.
    """
    natures = natures or {}
    pts_lbl = []   # (x, y, texte, couleur, taille_marqueur_mm)
    for i_ordre, nom in enumerate(symb.ORDRE_DESSIN):
        g = couches.get(nom)
        if g is None:
            continue
        zbase = 2 + i_ordre * 0.3
        for _, r in g.iterrows():
            geom = r.geometry
            if geom is None:
                continue
            nat = natures.get(str(r.get("NOM") or "").strip()) if (nom == "PT" and natures) else None
            st = symb.style_de(nom, r, nat)
            if st is None:
                continue

            if st["geom"] == "polygone":
                for gg in getattr(geom, "geoms", [geom]):
                    try:
                        xs, ys = gg.exterior.xy
                        ax.plot(xs, ys, color=symb.mpl(st["contour"]),
                                linewidth=lw(st["contour_mm"]),
                                linestyle=(0, (1, 2)) if st["style"] == "dot" else "solid",
                                zorder=zbase)
                    except Exception:
                        pass

            elif st["geom"] == "ligne":
                for ln in getattr(geom, "geoms", [geom]):
                    try:
                        xs, ys = ln.xy
                    except Exception:
                        continue
                    if st.get("traits"):  # casing noir (CTR)
                        ax.plot(xs, ys, color="black",
                                linewidth=lw(st["largeur_mm"] + 0.5), zorder=zbase)
                    ax.plot(xs, ys, color=symb.mpl(st["couleur"]),
                            linewidth=lw(st["largeur_mm"]), solid_capstyle="round",
                            zorder=zbase + 0.1)
                # étiquette câble UNIQUEMENT (pas SUPPORT), le long de la ligne
                if nom == "CABLES" and st.get("label"):
                    try:
                        gg = max(getattr(geom, "geoms", [geom]), key=lambda s: s.length)
                        m = gg.interpolate(0.5, normalized=True)
                        a = gg.interpolate(0.42, normalized=True)
                        b = gg.interpolate(0.58, normalized=True)
                        ang = math.degrees(math.atan2(b.y - a.y, b.x - a.x))
                        if ang > 90:
                            ang -= 180
                        elif ang < -90:
                            ang += 180
                        if x0 <= m.x <= x1 and y0 <= m.y <= y1:
                            ax.text(m.x, m.y, st["label"], fontsize=6,
                                    color=symb.mpl(st.get("label_couleur", (0, 0, 0))),
                                    rotation=ang, rotation_mode="anchor",
                                    ha="center", va="bottom", zorder=19,
                                    path_effects=_tampon(0.6))
                    except Exception:
                        pass

            else:  # point
                p = geom if geom.geom_type == "Point" else geom.representative_point()
                mk = _MARQUEURS.get(st["forme"], "o")
                ax.plot(p.x, p.y, marker=mk, color=symb.mpl(st["couleur"]),
                        markersize=ms(st["taille_mm"]),
                        markeredgecolor=symb.mpl(st["contour"]) if st.get("contour") else "none",
                        markeredgewidth=lw(st.get("contour_mm", 0)),
                        linestyle="None", zorder=zbase + 1)
                if st.get("label"):
                    pts_lbl.append((p.x, p.y, st["label"],
                                    st.get("label_couleur", (0, 0, 0)), st["taille_mm"]))

    # --- étiquettes de points : filtre anti-chevauchement (distance mini) ---
    dmin = (x1 - x0) * 0.028
    places = []
    # sites/BPE d'abord (prioritaires), puis le reste
    for x, y, txt, coul, taille in pts_lbl:
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            continue
        if any((x - px) ** 2 + (y - py) ** 2 < dmin ** 2 for px, py in places):
            continue
        places.append((x, y))
        off = ms(taille) * 0.5 + 2
        ax.annotate(txt, (x, y), xytext=(off, off), textcoords="offset points",
                    fontsize=6, color=symb.mpl(coul), zorder=20, ha="left",
                    path_effects=_tampon(0.7))


_LEG_HEADER = 6.0
_LEG_TITRE = 4.4
_LEG_LIGNE = 4.2
_LEG_GAP = 1.8


def _hauteur_legende(blocs):
    h = _LEG_HEADER + 2
    for _, entrees in blocs:
        h += _LEG_TITRE + len(entrees) * _LEG_LIGNE + _LEG_GAP
    return h


def _legende_encart(fig, l_mm, h_mm, x_mm, y_mm, w_mm, h_box_mm, blocs):
    """Encart de légende (fond blanc) superposé à la carte, comme le plan de réf.
    Ne montre que les catégories présentes (blocs = legende_dynamique)."""
    ax = fig.add_axes([x_mm / l_mm, y_mm / h_mm, w_mm / l_mm, h_box_mm / h_mm], zorder=30)
    ax.set_xlim(0, w_mm); ax.set_ylim(0, h_box_mm)
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_facecolor("white")
    for s in ax.spines.values():
        s.set_linewidth(0.9)
    ax.text(w_mm / 2, h_box_mm - 3.5, "Légende", fontsize=8, fontweight="bold", ha="center")
    y = h_box_mm - _LEG_HEADER - 2.5
    for titre, entrees in blocs:
        ax.text(2, y, titre, fontsize=6.5, fontweight="bold", va="center")
        y -= _LEG_TITRE
        for forme, coul, lib in entrees:
            if forme == "ligne":
                ax.add_line(Line2D([3, 10], [y, y], color=symb.mpl(coul), linewidth=2.4))
            else:
                ax.plot(6.5, y, marker=_MARQUEURS.get(forme, "o"), color=symb.mpl(coul),
                        markersize=6, markeredgecolor="black", markeredgewidth=0.3,
                        linestyle="None")
            ax.text(13, y, lib, fontsize=5.5, va="center")
            y -= _LEG_LIGNE
        y -= _LEG_GAP


def generer_plan_apd(dossier_shape: str, chemin_pdf: str,
                     orientation: str = "paysage", titre: str = None,
                     natures: dict = None) -> str:
    """
    Plan APS/APD (page 1 du livrable APD) : fond Plan IGN + symbologie NETGEO
    exacte (formes/couleurs QGIS) + légende à droite + rose des vents + échelle.
    Rendu 100 % backend depuis les SHP (aucune dépendance QGIS).
    """
    noms = ("BPE", "BTS", "CABLES", "COMMUNE", "NRA", "NRO_RIP", "PT", "SUPPORT", "BLOCAGE")
    brut = {n: _lire(dossier_shape, n) for n in noms}
    if all(v is None for v in brut.values()):
        raise ValueError(f"Aucune couche exploitable dans {dossier_shape}")

    couches = {}
    for n, g in brut.items():
        if g is None:
            couches[n] = None
            continue
        try:
            couches[n] = g.to_crs(3857) if g.crs else g
        except Exception:
            couches[n] = g

    l_mm, h_mm = (297, 210) if orientation != "portrait" else (210, 297)
    fig = plt.figure(figsize=(l_mm * MM, h_mm * MM))

    marge = 3.0
    ax = fig.add_axes([marge / l_mm, marge / h_mm,
                       (l_mm - 2 * marge) / l_mm, (h_mm - 2 * marge) / h_mm])
    ax.set_xticks([]); ax.set_yticks([])
    for c in ax.spines.values():
        c.set_linewidth(0.8)

    # emprise
    bornes = None
    for n in ("CABLES", "SUPPORT", "BPE", "BTS", "PT"):
        g = couches[n]
        if g is None:
            continue
        b = g.total_bounds
        bornes = b if bornes is None else (min(bornes[0], b[0]), min(bornes[1], b[1]),
                                           max(bornes[2], b[2]), max(bornes[3], b[3]))
    if bornes is None:
        bornes = couches["COMMUNE"].total_bounds
    x0, y0, x1, y1 = bornes
    dx, dy = max(x1 - x0, 50), max(y1 - y0, 50)
    x0, x1 = x0 - dx * 0.12, x1 + dx * 0.12
    y0, y1 = y0 - dy * 0.12, y1 + dy * 0.12
    dx, dy = x1 - x0, y1 - y0
    pos = ax.get_position()
    ratio = (pos.height * h_mm) / (pos.width * l_mm)
    if dy / dx < ratio:
        sup = dx * ratio - dy
        y0, y1 = y0 - sup / 2, y1 + sup / 2
    else:
        sup = dy / ratio - dx
        x0, x1 = x0 - sup / 2, x1 + sup / 2
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    ax.set_aspect("equal", adjustable="box")

    lat_c = math.degrees(2 * math.atan(math.exp(((y0 + y1) / 2) / _RAYON)) - math.pi / 2)
    k = math.cos(math.radians(lat_c))

    def lw(mm_):
        return max(mm_, 0) * PT_PAR_MM

    def ms(mm_):
        return mm_ * PT_PAR_MM

    # fond Plan IGN
    fond_ok = _fond_carte(ax, x0, y0, x1, y1, url=FOND_IGN_URL)
    if not fond_ok and couches["COMMUNE"] is not None:
        couches["COMMUNE"].boundary.plot(ax=ax, color="black", linewidth=lw(0.3), alpha=0.4)

    # couches (symbologie NETGEO) + étiquettes
    _dessiner_couches(ax, couches, x0, y0, x1, y1, lw, ms, natures=natures)

    # commune (label) au centre
    g = couches["COMMUNE"]
    if g is not None:
        for _, r in g.iterrows():
            nom = _val(r, "NOM")
            if nom and r.geometry is not None:
                c = r.geometry.representative_point()
                if x0 <= c.x <= x1 and y0 <= c.y <= y1:
                    ax.text(c.x, c.y, nom, fontsize=11, fontweight="bold",
                            color=symb.mpl(symb.LBL_JAUNE), ha="center", va="center",
                            zorder=15, path_effects=_tampon(0.7, (0.3, 0.05, 0.35)))

    # rose des vents (haut-gauche)
    try:
        rose = mpimg.imread(os.path.join(ASSETS, "rose_nord.png"))
        a = fig.add_axes([(marge + 1) / l_mm, (h_mm - marge - 18) / h_mm, 16 / l_mm, 16 / h_mm])
        a.set_axis_off(); a.imshow(rose)
    except Exception:
        pass

    # échelle graphique (bas-gauche)
    largeur_terrain = (x1 - x0) * k
    pas = _pas_echelle(largeur_terrain)
    pas_carte = pas / k
    x_ech = x0 + (x1 - x0) * 0.02
    y_ech = y0 + (y1 - y0) * 0.04
    h_ech = (y1 - y0) * 0.008
    for i, coul in enumerate(("black", "white")):
        ax.add_patch(Rectangle((x_ech + i * pas_carte / 2, y_ech), pas_carte / 2, h_ech,
                               facecolor=coul, edgecolor="black", linewidth=lw(0.2), zorder=9))
    for frac, txt in ((0, "0"), (0.5, f"{pas / 2:g}"), (1, f"{pas:g} m")):
        ax.text(x_ech + frac * pas_carte, y_ech + h_ech * 1.8, txt, fontsize=8,
                ha="center", va="bottom", zorder=9, color="black",
                path_effects=_tampon(0.6))

    # légende dynamique (uniquement les catégories présentes), encart haut-droite
    blocs = symb.legende_dynamique(couches, natures)
    if blocs:
        leg_w = 58.0
        leg_h = min(_hauteur_legende(blocs), h_mm - 2 * marge - 4)
        _legende_encart(fig, l_mm, h_mm, l_mm - marge - leg_w - 1,
                        h_mm - marge - leg_h - 1, leg_w, leg_h, blocs)

    os.makedirs(os.path.dirname(chemin_pdf), exist_ok=True)
    est_png = chemin_pdf.lower().endswith(".png")
    fig.savefig(chemin_pdf, format="png" if est_png else "pdf", dpi=200 if est_png else 300)
    plt.close(fig)
    if not est_png:
        _compresser_fond(chemin_pdf)
    logger.info(f"Plan APD (APS/APD) généré : {chemin_pdf}")
    return chemin_pdf


# ---------------------------------------------------------------------------
# 2ᵉ série de plans : FOLIOS (A3 paysage) — vue d'ensemble + un folio par page.
# Mise en page calée sur les layouts « FOLIO » et « FOLIO-VUE D'ENSEMBLE-A3 »
# de CODE_PROJET.qgz (positions/tailles en mm, logos + rose extraits du gabarit).
# ---------------------------------------------------------------------------

_A3_L, _A3_H = 420.0, 297.0                      # A3 paysage (mm)
_FOLIO_ASSETS = os.path.join(ASSETS, "folio")
_BLEU_FOLIO = "#1f4fd6"
_ROUGE_FOLIO = "#d00000"


def _lw(mm_):
    return max(mm_, 0) * PT_PAR_MM


def _ms(mm_):
    return mm_ * PT_PAR_MM


def _img_folio(nom):
    for base in (_FOLIO_ASSETS, ASSETS):
        p = os.path.join(base, nom)
        if os.path.exists(p):
            try:
                return mpimg.imread(p)
            except Exception:
                pass
    return None


def _ax_mm(fig, x_mm, y_top_mm, w_mm, h_mm):
    """Axe positionné en mm (origine HAUT-gauche, comme QGIS) sur la page A3."""
    return fig.add_axes([x_mm / _A3_L, (_A3_H - y_top_mm - h_mm) / _A3_H,
                         w_mm / _A3_L, h_mm / _A3_H])


def _cadrer(ext, w_mm, h_mm):
    """Étend l'emprise pour remplir un cadre w×h (mm) en gardant le ratio 1:1."""
    x0, y0, x1, y1 = ext
    ratio = h_mm / w_mm
    dx, dy = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    if dy / dx < ratio:
        s = dx * ratio - dy
        y0, y1 = y0 - s / 2, y1 + s / 2
    else:
        s = dy / ratio - dx
        x0, x1 = x0 - s / 2, x1 + s / 2
    return x0, y0, x1, y1


def _charger_couches_folio(dossier_shape):
    noms = ("BPE", "BTS", "CABLES", "COMMUNE", "NRA", "NRO_RIP", "PT", "SUPPORT", "BLOCAGE")
    out = {}
    for n in noms:
        g = _lire(dossier_shape, n)
        try:
            out[n] = g.to_crs(3857) if (g is not None and g.crs) else g
        except Exception:
            out[n] = g
    return out


def _emprise(couches, cles=("CABLES", "SUPPORT", "BPE", "BTS", "PT"), marge=0.10):
    b = None
    for n in cles:
        g = couches.get(n)
        if g is None or len(g) == 0:
            continue
        t = g.total_bounds
        b = t if b is None else (min(b[0], t[0]), min(b[1], t[1]),
                                 max(b[2], t[2]), max(b[3], t[3]))
    if b is None and couches.get("COMMUNE") is not None:
        b = couches["COMMUNE"].total_bounds
    x0, y0, x1, y1 = b
    dx, dy = max(x1 - x0, 50), max(y1 - y0, 50)
    return (x0 - dx * marge, y0 - dy * marge, x1 + dx * marge, y1 + dy * marge)


def _titre_lieu(couches):
    ad = cp = com = ""
    for n in ("BTS", "NRA", "NRO_RIP", "BPE", "PT"):
        g = couches.get(n)
        if g is None:
            continue
        for _, r in g.iterrows():
            if not ad:
                ad = _val(r, "ADRESSE")
            if not cp:
                cp = _val(r, "CP") or _val(r, "CODE_POSTA")
        if ad:
            break
    g = couches.get("COMMUNE")
    if g is not None and len(g):
        com = _val(g.iloc[0], "NOM")
    parts = [p for p in (ad, cp, com) if p]
    return " - ".join(parts).upper() if parts else "PLAN"


def _charger_folios(folios_shp, couches):
    """[(id, (x0,y0,x1,y1) en 3857)] triés par id ; auto-génère si SHP absent."""
    if folios_shp and os.path.exists(folios_shp):
        try:
            g = gpd.read_file(folios_shp)
            g = g.to_crs(3857) if g.crs else g
            col_id = "id" if "id" in g.columns else None
            recs = []
            for i, (_, r) in enumerate(g.iterrows(), 1):
                fid = r[col_id] if (col_id and r[col_id] == r[col_id]) else i
                if r.geometry is not None:
                    recs.append((int(float(fid)), tuple(r.geometry.bounds)))
            if recs:
                recs.sort(key=lambda x: x[0])
                # Renumérotation séquentielle 1..N : l'id affiché = l'index de page
                # (évite toute divergence titre « FOLIO n/N » / cadre surligné si le
                # SHP porte des id non contigus, ex. édité sous QGIS).
                return [(i, b) for i, (_, b) in enumerate(recs, 1)]
        except Exception as e:
            logger.warning(f"FOLIO_LIVRABLES illisible ({e}) — auto-génération.")
    return _folios_auto(couches)


def _folios_auto(couches, max_folios=4):
    """Quadrille l'emprise réseau en au plus ``max_folios`` cadres. La subdivision
    ncol×nrow (≤ max_folios) est choisie pour épouser l'aspect du réseau — y
    compris les corridors très étroits/longs (FTTH le long des routes). Le cadrage
    A4/A3 (`_cadrer`) étend ensuite chaque cellule au ratio de la page."""
    x0, y0, x1, y1 = _emprise(couches, marge=0.03)
    dx, dy = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    # Découpe selon une taille de cellule cible (détail lisible) qui agrandit
    # LES DEUX dimensions jusqu'à respecter le plafond — évite l'explosion pour
    # les corridors étroits/longs (le nombre de folios suit la taille du réseau).
    cible = 450.0
    ncol = nrow = 1
    for _ in range(24):
        ncol = max(1, int(math.ceil(dx / cible)))
        nrow = max(1, int(math.ceil(dy / cible)))
        if ncol * nrow <= max_folios:
            break
        cible *= 1.3
    cellw, cellh = dx / ncol, dy / nrow
    folios, fid = [], 1
    for j in range(nrow):
        cy0 = y0 + j * cellh
        for i in range(ncol):
            cx0 = x0 + i * cellw
            folios.append((fid, (cx0, cy0, cx0 + cellw, cy0 + cellh)))
            fid += 1
    return folios[:max_folios]   # garantie dure du plafond


def _carte(ax, couches, ext, fond=True, natures=None):
    x0, y0, x1, y1 = ext
    ax.set_xticks([]); ax.set_yticks([])
    for c in ax.spines.values():
        c.set_linewidth(0.7)
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    ax.set_aspect("equal", adjustable="box")
    if fond:
        _fond_carte(ax, x0, y0, x1, y1, url=FOND_IGN_URL)
    _dessiner_couches(ax, couches, x0, y0, x1, y1, _lw, _ms, natures=natures)


def _rects_folios(ax, folios, courant=None):
    for fid, b in folios:
        actif = (fid == courant)
        coul = _ROUGE_FOLIO if actif else _BLEU_FOLIO
        ax.add_patch(Rectangle((b[0], b[1]), b[2] - b[0], b[3] - b[1], fill=False,
                               edgecolor=coul, linewidth=_lw(1.1 if actif else 0.6), zorder=20))
        ax.text(b[2] - (b[2] - b[0]) * 0.03, b[1] + (b[3] - b[1]) * 0.03, str(fid),
                fontsize=13, fontweight="bold", color=coul, ha="right", va="bottom",
                zorder=21, path_effects=_tampon(0.8))


def _legende_gauche(fig, couches, x_mm, y_top_mm, w_mm, h_mm, natures=None):
    blocs = symb.legende_dynamique(couches, natures)
    if not blocs:
        return
    hb = min(_hauteur_legende(blocs), h_mm)
    # _legende_encart attend y_mm depuis le BAS
    y_bas = _A3_H - y_top_mm - hb
    _legende_encart(fig, _A3_L, _A3_H, x_mm, y_bas, w_mm, hb, blocs)


def _cartouche_folio(fig, titre, mention):
    by, bh = 266.985, 30.015
    # cadre + séparateurs
    axc = _ax_mm(fig, 0, by, _A3_L, bh); axc.set_axis_off()
    axc.add_patch(Rectangle((0, 0), 1, 1, transform=axc.transAxes, fill=False,
                            edgecolor="black", linewidth=1.0))
    for xf in (44.0 / _A3_L, 315.0 / _A3_L, 363.0 / _A3_L):
        axc.plot([xf, xf], [0, 1], transform=axc.transAxes, color="black", linewidth=0.7)
    free = _img_folio("folio_pic1.png")
    if free is None:
        free = _img_folio("logo_free.png")
    ensio = _img_folio("folio_pic2.png")
    if ensio is None:
        ensio = _img_folio("logo_ensio.png")
    if free is not None:
        a = _ax_mm(fig, 2.5, by + 3, 39, bh - 6); a.set_axis_off(); a.imshow(free)
    if ensio is not None:
        a = _ax_mm(fig, _A3_L - 56, by + 3, 53, bh - 6); a.set_axis_off(); a.imshow(ensio)
    at = _ax_mm(fig, 46, by, 267, bh); at.set_axis_off()
    at.text(0.5, 0.5, titre, ha="center", va="center", fontsize=19, fontweight="bold")
    am = _ax_mm(fig, 316, by, 47, bh); am.set_axis_off()
    am.text(0.5, 0.5, mention, ha="center", va="center", fontsize=11, fontweight="bold")


def _rose_folio(fig, x_mm, y_top_mm, taille_mm):
    rose = _img_folio("rose_nord.png")
    if rose is None:
        return
    a = _ax_mm(fig, x_mm, y_top_mm, taille_mm, taille_mm)
    a.set_axis_off(); a.imshow(rose)


def _page_ensemble(couches, folios, titre, natures=None):
    fig = plt.figure(figsize=(_A3_L * MM, _A3_H * MM))
    # carte principale (quasi pleine page) : layout ensemble « Carte 1 »
    mx, my, mw, mh = 48.219, 0.25, 371.631, 272.724
    ax = _ax_mm(fig, mx, my, mw, mh)
    ext = _cadrer(_emprise(couches), mw, mh)
    _carte(ax, couches, ext, fond=True, natures=natures)
    _rects_folios(ax, folios)
    # légende (colonne gauche étroite) + rose
    _legende_gauche(fig, couches, 1.0, 2.0, 46.0, 230.0, natures=natures)
    _rose_folio(fig, 11.3, 233.2, 26.0)
    _cartouche_folio(fig, titre, "VUE D'ENSEMBLE")
    return fig


def _page_folio(couches, folios, ext_folio, num, total, emprise_glob, titre, natures=None):
    fig = plt.figure(figsize=(_A3_L * MM, _A3_H * MM))
    # carte principale (droite) : layout FOLIO « Carte 1 »
    mx, my, mw, mh = 120.005, 0.3, 299.995, 266.535
    ax = _ax_mm(fig, mx, my, mw, mh)
    ext = _cadrer(ext_folio, mw, mh)
    _carte(ax, couches, ext, fond=True, natures=natures)
    # colonne gauche : légende (haut) + rose + carte de localisation (bas)
    _legende_gauche(fig, couches, 1.0, 1.0, 115.0, 130.0, natures=natures)
    _rose_folio(fig, 2.38, 134.8, 27.9)
    lx, ly, lw_, lh = 0.0, 164.169, 119.705, 102.666
    axl = _ax_mm(fig, lx, ly, lw_, lh)
    ext_loc = _cadrer(emprise_glob, lw_, lh)
    _carte(axl, couches, ext_loc, fond=True, natures=natures)
    _rects_folios(axl, folios, courant=num)
    _cartouche_folio(fig, titre, f"FOLIO {num}/{total}")
    return fig


def generer_folios_apd(dossier_shape: str, chemin_pdf: str, folios_shp: str = None,
                       titre: str = None, natures: dict = None) -> str:
    """2ᵉ série de plans APD (A3 paysage) : 1 page « Vue d'ensemble » + 1 page par
    folio. Fond Plan IGN + symbologie NETGEO + légende, 100 % backend."""
    from matplotlib.backends.backend_pdf import PdfPages
    couches = _charger_couches_folio(dossier_shape)
    if all(v is None for v in couches.values()):
        raise ValueError(f"Aucune couche exploitable dans {dossier_shape}")
    folios = _charger_folios(folios_shp, couches)
    titre = titre or _titre_lieu(couches)
    emprise_glob = _emprise(couches)

    os.makedirs(os.path.dirname(chemin_pdf), exist_ok=True)
    with PdfPages(chemin_pdf) as pdf:
        fig = _page_ensemble(couches, folios, titre, natures=natures)
        pdf.savefig(fig, dpi=300); plt.close(fig)
        for i, (fid, ext) in enumerate(folios, 1):
            fig = _page_folio(couches, folios, ext, i, len(folios), emprise_glob, titre,
                              natures=natures)
            pdf.savefig(fig, dpi=300); plt.close(fig)
    logger.info(f"Folios APD générés ({len(folios) + 1} pages) : {chemin_pdf}")
    return chemin_pdf
