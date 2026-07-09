"""
gis_handler.py - Module SIG : Lecture, Validation et Traitement des fichiers géospatiaux.

Ce module est le cœur technique du traitement cartographique.
Il utilise GeoPandas, Shapely et Fiona pour manipuler les Shapefiles.

PRINCIPE D'INDÉPENDANCE :
  Ce module ne connaît PAS l'interface web ni la base de données CRM.
  Il reçoit un chemin de fichier, le lit, le valide et retourne les données.
  Si demain on veut remplacer GeoPandas par PyQGIS, on ne modifie que CE fichier.
"""

import os
import json
import logging
import geopandas as gpd

# CORRECTION CRITIQUE : Si le fichier .shx (index spatial) est manquant,
# GDAL/Fiona le reconstruit automatiquement au lieu de planter.
# C'est fréquent quand l'utilisateur n'uploade pas tous les fichiers compagnons.
os.environ["SHAPE_RESTORE_SHX"] = "YES"

logger = logging.getLogger("crm_sig.gis")


def lire_shapefile(chemin_fichier: str) -> gpd.GeoDataFrame:
    """
    Lit un fichier Shapefile (.shp) et retourne un GeoDataFrame.
    Vérifie que le fichier existe avant de tenter la lecture.

    Args:
        chemin_fichier: Chemin absolu vers le fichier .shp

    Returns:
        Un GeoDataFrame contenant les données géospatiales.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas.
        RuntimeError: Si la lecture échoue (fichier corrompu, etc.).
    """
    if not os.path.exists(chemin_fichier):
        logger.error(f"Fichier introuvable : {chemin_fichier}")
        raise FileNotFoundError(f"Le fichier {chemin_fichier} n'existe pas.")

    try:
        gdf = gpd.read_file(chemin_fichier)
        logger.info(f"Shapefile lu avec succès : {chemin_fichier} ({len(gdf)} entités)")
        return gdf
    except Exception as e:
        logger.error(f"Erreur lors de la lecture du Shapefile '{chemin_fichier}': {str(e)}")
        raise RuntimeError(f"Erreur lors de la lecture du Shapefile: {str(e)}")


def verifier_projection(gdf: gpd.GeoDataFrame, epsg_cible: int = 4326) -> gpd.GeoDataFrame:
    """
    Vérifie et harmonise la projection (CRS) du GeoDataFrame.
    Leaflet.js utilise le système WGS84 (EPSG:4326) pour l'affichage web.
    Ce module reprojette automatiquement si nécessaire.

    Args:
        gdf: Le GeoDataFrame à vérifier.
        epsg_cible: Le code EPSG cible (4326 par défaut pour le web).

    Returns:
        Le GeoDataFrame reprojeté si nécessaire.
    """
    CRS_METIER = 2154   # Lambert-93 : CRS des SHP livrables ; défaut si .prj manquant
    if gdf.crs is None:
        logger.warning("Aucune projection définie (.prj manquant ?) : Lambert-93 "
                       f"(EPSG:{CRS_METIER}) supposé, puis reprojection vers EPSG:{epsg_cible}.")
        gdf = gdf.set_crs(epsg=CRS_METIER)
        if CRS_METIER != epsg_cible:
            gdf = gdf.to_crs(epsg=epsg_cible)
    elif gdf.crs.to_epsg() != epsg_cible:
        crs_original = gdf.crs.to_epsg()
        gdf = gdf.to_crs(epsg=epsg_cible)
        logger.info(f"Reprojection effectuée : EPSG:{crs_original} -> EPSG:{epsg_cible}")

    return gdf


def extraire_metadonnees(gdf: gpd.GeoDataFrame) -> dict:
    """
    Extrait les informations descriptives de la couche pour l'affichage
    dans le panneau latéral de l'interface cartographique.

    Returns:
        Un dictionnaire contenant le nombre d'entités, les types de géométrie,
        la liste des attributs (colonnes), et le code EPSG.
    """
    geom_types = gdf.geom_type.unique().tolist()
    colonnes = [col for col in gdf.columns.tolist() if col != 'geometry']

    return {
        "nb_entites": len(gdf),
        "types_geometrie": geom_types,
        "attributs": colonnes,
        "crs": gdf.crs.to_epsg() if gdf.crs else None
    }


def convertir_en_geojson(gdf: gpd.GeoDataFrame) -> str:
    """
    Convertit un GeoDataFrame en chaîne GeoJSON pour affichage dans Leaflet.js.
    Reprojette automatiquement en WGS84 si ce n'est pas déjà le cas.

    Returns:
        Une chaîne JSON valide (GeoJSON) prête à être envoyée au frontend.
    """
    gdf_wgs84 = verifier_projection(gdf.copy(), 4326)
    geojson_str = gdf_wgs84.to_json()
    logger.info(f"Conversion GeoJSON réussie ({len(gdf_wgs84)} entités)")
    return geojson_str


def obtenir_table_attributaire(gdf: gpd.GeoDataFrame) -> list:
    """
    Retourne la table attributaire (sans la géométrie) sous forme de liste
    de dictionnaires, prête pour l'affichage dans un tableau HTML.
    """
    df_sans_geom = gdf.drop(columns=['geometry'], errors='ignore')
    return df_sans_geom.to_dict(orient='records')


def sauvegarder_attributs(chemin_source: str, chemin_destination: str, nouvelles_donnees: list) -> None:
    """
    Sauvegarde les modifications de la table attributaire.
    Ne modifie pas le fichier source (input), mais enregistre dans chemin_destination (traitement).
    """
    import pandas as pd
    
    gdf = lire_shapefile(chemin_source)
    
    # Remplacer les attributs en s'assurant de garder l'ordre des géométries
    # nouvelles_donnees est une liste de dict
    if len(nouvelles_donnees) != len(gdf):
        raise ValueError("Le nombre de lignes modifiées ne correspond pas au fichier source.")
        
    df_nouveau = pd.DataFrame(nouvelles_donnees)

    # Le front envoie toutes les valeurs en TEXTE. Sans re-typage, les champs
    # numériques du DBF (CAPACITE, NB_TUBE, ORDRE, LONGUEUR_R, LGR_REEL…) seraient
    # réécrits en chaîne et corrompraient le schéma du livrable (source de vérité
    # PDS/KMZ). On restaure le type numérique des colonnes qui l'étaient déjà.
    for col in df_nouveau.columns:
        if col in gdf.columns and pd.api.types.is_numeric_dtype(gdf[col].dtype):
            df_nouveau[col] = pd.to_numeric(df_nouveau[col], errors="coerce")

    # On garde la géométrie de l'ancien GeoDataFrame
    gdf_final = gpd.GeoDataFrame(df_nouveau, geometry=gdf.geometry, crs=gdf.crs)
    
    # Sauvegarde
    os.makedirs(os.path.dirname(chemin_destination), exist_ok=True)
    gdf_final.to_file(chemin_destination, encoding="utf-8")
    logger.info(f"Fichier modifié sauvegardé dans : {chemin_destination}")


def generer_livrables_shp(dossier_modele: str, dossier_input: str, dossier_sortie: str, overwrite: bool = True, modeles=None) -> list:
    """
    Parcourt le dossier modèle (ex: EXEMPLE).
    Pour chaque .shp modèle, vérifie si un .shp du même nom existe dans dossier_input.
    Si oui, copie ses géométries en appliquant strictement le schéma du modèle.
    Sinon, crée un .shp vide avec le schéma du modèle.
    Sauvegarde dans dossier_sortie.
    Retourne la liste des fichiers créés.

    ``modeles`` (optionnel) : liste de couples ``(nom_fichier_sortie, chemin_shp_modèle)``
    pour piloter explicitement les couches à produire — ex. DOE FO, où le gabarit
    NETGEO est imbriqué (01-BPE/… → SHAPE/BPE.shp). Si None, on utilise le schéma
    APD FO à plat : ``dossier_modele/*.shp``.
    """
    import glob
    fichiers_crees = []

    os.makedirs(dossier_sortie, exist_ok=True)

    if modeles is None:
        modeles = [(os.path.basename(p), p)
                   for p in glob.glob(os.path.join(dossier_modele, "*.shp"))]

    for nom_fichier, modele_path in modeles:
        chemin_sortie = os.path.join(dossier_sortie, nom_fichier)
        
        # Si overwrite est False et le fichier existe déjà, on le conserve intact
        if not overwrite and os.path.exists(chemin_sortie):
            fichiers_crees.append(nom_fichier)
            logger.info(f"Conservation du livrable existant (non écrasé) : {chemin_sortie}")
            continue
        
        # Lire le schéma du modèle (juste pour récupérer la structure)
        gdf_modele = gpd.read_file(modele_path)
        
        # Chercher l'input correspondant (on cherche le même nom de fichier)
        chemin_input_possible = os.path.join(dossier_input, nom_fichier)
        
        if os.path.exists(chemin_input_possible):
            try:
                try:
                    gdf_input = gpd.read_file(chemin_input_possible)
                except UnicodeDecodeError:                      # DBF Latin-1 accentué
                    gdf_input = gpd.read_file(chemin_input_possible, encoding="latin-1")
                # Aligner le schéma : on garde les colonnes de gdf_input qui existent dans le modèle
                # Et on ajoute les colonnes du modèle qui n'existent pas dans l'input (avec valeurs vides)
                
                colonnes_modele = gdf_modele.columns.tolist()
                colonnes_communes = [col for col in gdf_input.columns if col in colonnes_modele]
                
                gdf_livrable = gdf_input[colonnes_communes + ['geometry']].copy() if 'geometry' not in colonnes_communes else gdf_input[colonnes_communes].copy()
                
                # Ajouter les colonnes manquantes du modèle
                for col in colonnes_modele:
                    if col not in gdf_livrable.columns and col != 'geometry':
                        gdf_livrable[col] = None
                        
                # Réordonner pour correspondre exactement au modèle
                gdf_livrable = gdf_livrable[colonnes_modele]
                
                # Normaliser le CRS vers celui du modèle (norme Lambert-93 EPSG:2154) :
                # sans CRS -> on force ; CRS différent -> on reprojette (évite les livrables
                # dans un système hétérogène, ex. entrée en EPSG:27572).
                if gdf_modele.crs is not None:
                    if gdf_livrable.crs is None:
                        gdf_livrable.set_crs(gdf_modele.crs, inplace=True)
                    elif gdf_livrable.crs != gdf_modele.crs:
                        gdf_livrable = gdf_livrable.to_crs(gdf_modele.crs)
                    
            except Exception as e:
                # NE PAS produire un livrable VIDE en silence (perte de données :
                # le SHP livrable est la source de vérité PDS/KMZ/carte). On propage.
                logger.error(f"Erreur fusion {nom_fichier} : {str(e)}")
                raise
        else:
            # Créer un fichier vide
            gdf_livrable = gdf_modele.iloc[0:0].copy()
            
        # Sauvegarde
        gdf_livrable.to_file(chemin_sortie, encoding="utf-8")
        fichiers_crees.append(nom_fichier)
        logger.info(f"Livrable généré : {chemin_sortie} ({len(gdf_livrable)} entités)")
        
    return fichiers_crees


def generer_livrables_kmz(src_dir: str, dest_kmz: str) -> None:
    """
    Génère un fichier KMZ à partir des Shapefiles d'un répertoire.
    Applique le paramétrage strict des couleurs métier.
    """
    import simplekml
    import chardet
    import geopandas as gpd
    import os
    
    def get_encoding(filepath):
        try:
            with open(filepath, 'rb') as f:
                return chardet.detect(f.read(15000))['encoding'] or 'utf-8'
        except:
            return 'latin-1'

    kml = simplekml.Kml()
    
    layer_styles = {
        "NRO_RIP": {"color": "ff1400ff", "width": 3},
        "NRA":     {"color": "ff00a5ff", "width": 3},
        "BTS":     {"color": "ff0000bc", "width": 3},
        "SUPPORT": {"color": "ff8e8e8e", "width": 4},
        "PT":      {"color": "ff00cc44", "width": 3},
        "BPE":     {"color": "ff00d5ff", "width": 3},
        "CABLES":  {"color": "ffff22aa", "width": 3}
    }
    
    if not os.path.exists(src_dir):
        raise ValueError(f"Le dossier source n'existe pas: {src_dir}")
        
    all_files = os.listdir(src_dir)
    
    for layer_name, config in layer_styles.items():
        matched_file = None
        for file in all_files:
            # On cherche par préfixe pour supporter CABLES_123.shp
            if file.upper().startswith(layer_name) and file.upper().endswith(".SHP"):
                matched_file = os.path.join(src_dir, file)
                break
        
        if matched_file and os.path.exists(matched_file):
            dbf_file = matched_file.replace('.shp', '.dbf').replace('.SHP', '.DBF')
            enc = get_encoding(dbf_file) if os.path.exists(dbf_file) else 'utf-8'
            
            gdf = gpd.read_file(matched_file, encoding=enc)
            if gdf.crs is None:
                gdf = gdf.set_crs(epsg=2154)      # .prj manquant -> Lambert-93 supposé
            gdf = gdf.to_crs(epsg=4326)
            
            folder = kml.newfolder(name=layer_name)
            
            for _, row in gdf.iterrows():
                geom = row.geometry
                if geom is None:
                    continue
                
                label = str(row.get('NOM', row.get('LIBELLE', row.get('CODE', layer_name)))).strip()
                
                html_table = "<table border='1' style='border-collapse:collapse; font-family:Segoe UI, Arial; font-size:11px; width:300px; border-color:#e0e0e0;'>"
                html_table += "<tr style='background-color:#00adb5; color:white;'><th style='padding:5px;'>Attribut</th><th style='padding:5px;'>Valeur</th></tr>"
                for col in gdf.columns:
                    if col != 'geometry' and row[col] is not None:
                        html_table += f"<tr><td style='padding:4px; background-color:#f9f9f9;'><b>{col}</b></td><td style='padding:4px;'>{row[col]}</td></tr>"
                html_table += "</table>"
                
                if geom.geom_type == 'Point':
                    pnt = folder.newpoint(name=label, coords=[(geom.x, geom.y)])
                    pnt.description = html_table
                    pnt.style.iconstyle.color = config["color"]
                    pnt.style.iconstyle.icon.href = 'http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png'
                    
                elif geom.geom_type == 'LineString':
                    item = folder.newlinestring(name=label, coords=list(geom.coords))
                    item.description = html_table
                    item.style.linestyle.color = config["color"]
                    item.style.linestyle.width = config["width"]
                    
                elif geom.geom_type == 'MultiLineString':
                    for line in geom.geoms:
                        item = folder.newlinestring(name=label, coords=list(line.coords))
                        item.description = html_table
                        item.style.linestyle.color = config["color"]
                        item.style.linestyle.width = config["width"]
                        
                elif geom.geom_type == 'Polygon':
                    item = folder.newpolygon(name=label, outerboundaryis=list(geom.exterior.coords))
                    item.description = html_table
                    item.style.linestyle.color = config["color"]
                    item.style.polystyle.color = "44" + config["color"][2:]
                    
                elif geom.geom_type == 'MultiPolygon':
                    for poly in geom.geoms:
                        item = folder.newpolygon(name=label, outerboundaryis=list(poly.exterior.coords))
                        item.description = html_table
                        item.style.linestyle.color = config["color"]
                        item.style.polystyle.color = "44" + config["color"][2:]
    
    os.makedirs(os.path.dirname(dest_kmz), exist_ok=True)
    kml.savekmz(dest_kmz)
    logger.info(f"KMZ généré : {dest_kmz}")


# =====================================================================
# GÉNÉRATION AUTOMATIQUE DES CÂBLES (étude APD FO)
# ---------------------------------------------------------------------
# Principe métier : le réseau SUPPORT est une chaîne de tronçons ordonnés
# (champ ORDRE). Les "boîtes optiques" (BPE + site BTS) posées sur cette
# chaîne la découpent : entre deux boîtes consécutives, tous les tronçons
# SUPPORT sont fusionnés en UN seul câble. N boîtes -> N-1 câbles.
# =====================================================================

COLONNES_CABLES = ["NOM", "CODE", "EMPRISE", "PROPRIETAI", "GESTIONNAI", "POSE",
                   "ETAT", "FABRICANT", "REFERENCE", "LONGUEUR_R", "NB_TUBE", "CAPACITE"]


def _cle_noeud(x, y, precision=2):
    """Clé de nœud = coordonnée arrondie (les tronçons SUPPORT se touchent exactement)."""
    return (round(x, precision), round(y, precision))


def _ordre_val(v):
    """Convertit le champ ORDRE en entier ; renvoie une grande valeur si absent."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 10 ** 9


def _proche(a, b, tol=0.5):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 <= tol


def _premier_non_vide(vals):
    for v in vals:
        if v not in (None, "") and str(v).strip():
            return str(v).strip()
    return None


def _pose_dominante(rows):
    """POSE du câble = type de support dominant (par longueur) : AERIEN ou SOUTERRAIN."""
    par_type = {}
    for r in rows:
        ts = str(r.get("TYPE_STRUC") or "").upper()
        pose = "AERIEN" if "AER" in ts else "SOUTERRAIN"
        lg = r.geometry.length if r.geometry is not None else 0
        par_type[pose] = par_type.get(pose, 0) + lg
    return max(par_type, key=par_type.get) if par_type else ""


def _fusion_sequentielle(geoms):
    """Fusion de secours : chaîne les tronçons bout-à-bout dans l'ordre fourni."""
    from shapely.geometry import LineString
    coords = [tuple(c[:2]) for c in geoms[0].coords]
    for g in geoms[1:]:
        gc = [tuple(c[:2]) for c in g.coords]
        if _proche(coords[-1], gc[0]):
            coords += gc[1:]
        elif _proche(coords[-1], gc[-1]):
            coords += gc[::-1][1:]
        elif _proche(coords[0], gc[-1]):
            coords = gc[:-1] + coords
        elif _proche(coords[0], gc[0]):
            coords = gc[::-1][:-1] + coords
        else:
            coords += gc  # discontinuité : on concatène quand même
    return LineString(coords)


def _orienter_par_ordre(merged, rows):
    """Oriente la ligne fusionnée dans le sens des ORDRE croissants."""
    from shapely.geometry import Point, LineString
    if merged.geom_type != "LineString" or len(rows) < 2:
        return merged
    fc = list(rows[0].geometry.coords)
    e1, e2 = Point(fc[0][:2]), Point(fc[-1][:2])
    last_geom = rows[-1].geometry
    depart = e1 if e1.distance(last_geom) >= e2.distance(last_geom) else e2
    mc = list(merged.coords)
    if Point(mc[0][:2]).distance(depart) <= Point(mc[-1][:2]).distance(depart):
        return merged
    return LineString(mc[::-1])


def _cables_vide(modele_cables_gdf, crs):
    cols = [c for c in (list(modele_cables_gdf.columns) if modele_cables_gdf is not None else [])
            if c != "geometry"] or COLONNES_CABLES
    gdf = gpd.GeoDataFrame({c: [] for c in cols}, geometry=[], crs=crs)
    return gdf


def construire_cables(support_gdf, bpe_gdf, bts_gdf=None, modele_cables_gdf=None,
                      tol_snap=2.0, defaults=None):
    """
    Construit la couche CABLES à partir du réseau SUPPORT et des boîtes (BPE + BTS).

    Règle : chaque câble = fusion des tronçons SUPPORT situés entre deux boîtes
    consécutives (BPE ou BTS), tronçons ordonnés par ORDRE croissant (= direction).

    Args:
        support_gdf: GeoDataFrame des tronçons SUPPORT (LineString, champ ORDRE).
        bpe_gdf:     GeoDataFrame des boîtes BPE (Point).
        bts_gdf:     GeoDataFrame du site BTS (Point) ou None.
        modele_cables_gdf: GeoDataFrame modèle (pour le schéma de colonnes).
        tol_snap:    tolérance de rattachement boîte -> nœud support (mètres).
        defaults:    dict d'attributs par défaut (CAPACITE, NB_TUBE, ETAT, ...).

    Returns:
        GeoDataFrame CABLES au schéma du modèle, CRS = celui de support_gdf.
    """
    from collections import defaultdict
    from shapely.geometry import Point
    from shapely.ops import linemerge, unary_union

    defaults = defaults or {}
    cap = defaults.get("CAPACITE", 48)
    nbt = defaults.get("NB_TUBE", 4)
    etat = defaults.get("ETAT", "EN ETUDE")
    prop = defaults.get("PROPRIETAI", "FREE MOBILE")
    gest = defaults.get("GESTIONNAI", "FREE MOBILE")

    if support_gdf is None or len(support_gdf) == 0:
        logger.warning("SUPPORT vide : aucun câble généré.")
        return _cables_vide(modele_cables_gdf, support_gdf.crs if support_gdf is not None else None)

    support_gdf = support_gdf.reset_index(drop=True)
    # Normaliser les tronçons multi-parties (MultiLineString) en LineString :
    # sinon geom.coords lève NotImplementedError plus bas (graphe / fusion).
    try:
        if (support_gdf.geometry.geom_type == "MultiLineString").any():
            support_gdf = support_gdf.explode(index_parts=False).reset_index(drop=True)
    except Exception as _e:
        logger.warning(f"Normalisation SUPPORT (explode) ignorée : {_e}")
    crs = support_gdf.crs

    def _harmoniser(gdf):
        if gdf is None or len(gdf) == 0:
            return gdf
        if crs is not None and gdf.crs is not None and gdf.crs != crs:
            return gdf.to_crs(crs)
        return gdf

    bpe_gdf = _harmoniser(bpe_gdf)
    bts_gdf = _harmoniser(bts_gdf)

    # 1. Graphe des supports (nœuds = extrémités, arêtes = tronçons)
    incident = defaultdict(list)
    aretes = {}
    noeuds = {}
    for idx, row in support_gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        c = list(geom.coords)
        a = _cle_noeud(*c[0][:2]); b = _cle_noeud(*c[-1][:2])
        noeuds[a] = c[0][:2]; noeuds[b] = c[-1][:2]
        aretes[idx] = (a, b)
        incident[a].append(idx); incident[b].append(idx)

    if not noeuds:
        return _cables_vide(modele_cables_gdf, crs)

    cles = list(noeuds.keys())
    pts = {k: Point(noeuds[k]) for k in cles}

    # 2. Rattacher chaque boîte au nœud le plus proche (coupures)
    boites = set()
    noeud_bts = set()

    def _rattacher(gdf, is_bts=False):
        if gdf is None:
            return
        for _, r in gdf.iterrows():
            g = r.geometry
            if g is None or g.is_empty:
                continue
            p = g if g.geom_type == "Point" else g.centroid
            best, bestd = None, None
            for k in cles:
                d = p.distance(pts[k])
                if bestd is None or d < bestd:
                    bestd, best = d, k
            if best is None:
                continue
            if bestd > tol_snap:
                logger.warning(f"Boîte à {bestd:.2f} m du réseau (> {tol_snap} m) : "
                               f"rattachée au nœud le plus proche malgré tout.")
            boites.add(best)
            if is_bts:
                noeud_bts.add(best)

    _rattacher(bpe_gdf, False)
    _rattacher(bts_gdf, True)

    if len(boites) < 2:
        logger.warning(f"{len(boites)} boîte(s) rattachée(s) : au moins 2 nécessaires. Aucun câble.")
        return _cables_vide(modele_cables_gdf, crs)

    # 3. Parcours : chaque "run" de tronçons entre deux boîtes = un câble
    visites = set()
    runs = []
    for depart in boites:
        for idx0 in incident[depart]:
            if idx0 in visites:
                continue
            chemin = []
            courant, arete, fin = depart, idx0, depart
            while arete is not None and arete not in visites:
                visites.add(arete)
                chemin.append(arete)
                a, b = aretes[arete]
                suivant = b if a == courant else a
                fin = suivant
                if suivant in boites:
                    break
                voisines = [e for e in incident[suivant] if e != arete and e not in visites]
                if len(voisines) == 1:
                    courant, arete = suivant, voisines[0]
                else:
                    if len(voisines) > 1:
                        logger.warning("Jonction sans boîte rencontrée : câble coupé ici.")
                    break
            if chemin:
                runs.append({"edges": chemin, "n0": depart, "n1": fin})

    # 4. Construire chaque câble (géométrie + attributs)
    lignes = []
    for run in runs:
        rows = [support_gdf.loc[i] for i in run["edges"]]
        rows.sort(key=lambda r: _ordre_val(r.get("ORDRE")))
        geoms = [r.geometry for r in rows]
        if len(geoms) == 1:
            merged = geoms[0]
        else:
            merged = linemerge(unary_union(geoms))
            if merged.geom_type != "LineString":
                merged = _fusion_sequentielle(geoms)
        merged = _orienter_par_ordre(merged, rows)
        touche_bts = run["n0"] in noeud_bts or run["n1"] in noeud_bts
        lignes.append({
            "geometry": merged,
            "EMPRISE": _premier_non_vide([r.get("EMPRISE") for r in rows]) or "",
            "POSE": _pose_dominante(rows),
            "LONGUEUR_R": round(merged.length, 1),
            "_ordre_min": min(_ordre_val(r.get("ORDRE")) for r in rows),
            "_bts": touche_bts,
        })

    # 5. Nommage (CTR/CAD + numéro) : numérotation par ORDRE minimal croissant
    lignes.sort(key=lambda d: d["_ordre_min"])
    enregistrements = []
    for i, d in enumerate(lignes, start=1):
        prefixe = "CAD" if d["_bts"] else "CTR"
        nom = f"{prefixe}_{d['EMPRISE']}_{i:02d}" if d["EMPRISE"] else f"{prefixe}_{i:02d}"
        enregistrements.append({
            "NOM": nom, "CODE": nom, "EMPRISE": d["EMPRISE"],
            "PROPRIETAI": prop, "GESTIONNAI": gest, "POSE": d["POSE"],
            "ETAT": etat, "FABRICANT": "", "REFERENCE": "",
            "LONGUEUR_R": d["LONGUEUR_R"], "NB_TUBE": nbt, "CAPACITE": cap,
            "geometry": d["geometry"],
        })

    # 6. GeoDataFrame au schéma du modèle
    colonnes = [c for c in (list(modele_cables_gdf.columns) if modele_cables_gdf is not None else [])
                if c != "geometry"] or COLONNES_CABLES
    gdf = gpd.GeoDataFrame(enregistrements, geometry="geometry", crs=crs)
    for c in colonnes:
        if c not in gdf.columns:
            gdf[c] = None
    gdf = gdf[colonnes + ["geometry"]]
    logger.info(f"{len(gdf)} câble(s) construit(s) depuis {len(support_gdf)} supports "
                f"et {len(boites)} boîtes.")
    return gdf
