"""
exporter.py - Module SIG : Export et génération de nouvelles couches.

Ce module gère l'export des couches géospatiales.
Il applique les règles métier (normes) avant l'export via le PATTERN STRATEGY :
  - Les règles sont définies dans des dictionnaires de configuration.
  - Pour changer de norme, il suffit de passer une configuration différente.
  - Le cœur de l'application n'est JAMAIS modifié.

PATTERN STRATEGY (pour non-codeur) :
  Imaginez un formulaire avec une liste déroulante "Norme à appliquer".
  Chaque norme est un petit fichier de configuration.
  Quand on en sélectionne un, l'export applique les règles correspondantes.
"""

import os
import logging
import geopandas as gpd
from app.gis.gis_handler import verifier_projection

logger = logging.getLogger("crm_sig.gis.export")

# =====================================================================
# CONFIGURATIONS DE NORMES (Pattern Strategy)
# Pour ajouter une nouvelle norme, il suffit de créer un nouveau dictionnaire
# et de le passer à la fonction exporter_couche_normee().
# =====================================================================

NORME_PAR_DEFAUT = {
    "nom": "Standard",
    "epsg_sortie": 2154,          # Lambert-93 (France métropolitaine)
    "colonnes_obligatoires": [],   # Liste des colonnes requises dans la table attributaire
    "renommer_colonnes": {},       # Dictionnaire ancien_nom -> nouveau_nom
    "filtrer_types_geometrie": None,  # None = tout accepter, ou "Point", "LineString", etc.
}

NORME_ORANGE_FTTH = {
    "nom": "Orange FTTH",
    "epsg_sortie": 2154,
    "colonnes_obligatoires": ["ID_CABLE", "TYPE", "NB_FIBRES"],
    "renommer_colonnes": {},
    "filtrer_types_geometrie": None,
}


def exporter_couche_normee(
    gdf: gpd.GeoDataFrame,
    chemin_dossier_sortie: str,
    nom_fichier: str,
    norme: dict = None
) -> str:
    """
    Exporte un GeoDataFrame en Shapefile après application des règles métier.

    Args:
        gdf: Les données géospatiales à exporter.
        chemin_dossier_sortie: Le dossier de destination (ex: .../03_Livrables_PDF).
        nom_fichier: Le nom du fichier de sortie (sans extension).
        norme: Le dictionnaire de configuration de la norme à appliquer.
               Par défaut, la norme standard est utilisée.

    Returns:
        Le chemin complet vers le fichier exporté.
    """
    if norme is None:
        norme = NORME_PAR_DEFAUT

    logger.info(f"Export avec la norme '{norme['nom']}' -> {nom_fichier}")

    try:
        gdf_export = gdf.copy()

        # 1. Reprojection si nécessaire
        epsg_sortie = norme.get("epsg_sortie", 2154)
        gdf_export = verifier_projection(gdf_export, epsg_sortie)

        # 2. Filtrer par type de géométrie si la norme l'exige
        filtre_geom = norme.get("filtrer_types_geometrie")
        if filtre_geom:
            gdf_export = gdf_export[gdf_export.geom_type == filtre_geom]
            logger.info(f"Filtrage géométrie '{filtre_geom}' : {len(gdf_export)} entités conservées")

        # 3. Renommer les colonnes selon la norme
        renommage = norme.get("renommer_colonnes", {})
        if renommage:
            gdf_export = gdf_export.rename(columns=renommage)

        # 4. Vérifier les colonnes obligatoires
        colonnes_obligatoires = norme.get("colonnes_obligatoires", [])
        colonnes_manquantes = [c for c in colonnes_obligatoires if c not in gdf_export.columns]
        if colonnes_manquantes:
            logger.warning(f"Colonnes manquantes selon la norme '{norme['nom']}': {colonnes_manquantes}")

        # 5. Export en Shapefile
        os.makedirs(chemin_dossier_sortie, exist_ok=True)
        chemin_complet = os.path.join(chemin_dossier_sortie, f"{nom_fichier}.shp")
        gdf_export.to_file(chemin_complet, driver='ESRI Shapefile')

        logger.info(f"Couche exportée avec succès : {chemin_complet}")
        return chemin_complet

    except Exception as e:
        logger.error(f"Erreur lors de l'export de la couche '{nom_fichier}': {str(e)}")
        raise RuntimeError(f"Erreur lors de l'export : {str(e)}")


def convertir_geojson_pour_carte(gdf: gpd.GeoDataFrame) -> str:
    """
    Convertit un GeoDataFrame en GeoJSON (WGS84) pour affichage Leaflet.js.
    Fonction utilitaire séparée de l'export métier.
    """
    gdf_wgs84 = verifier_projection(gdf.copy(), 4326)
    return gdf_wgs84.to_json()
