"""
pdf_generator.py - Module Reporting : Génération de livrables PDF.

Utilise la bibliothèque fpdf2 pour produire des fichiers PDF professionnels.
Ce module génère :
  - Des fiches de synthèse pour chaque projet.
  - (Futur) Des plans techniques avec cartouche.

PRINCIPE D'INDÉPENDANCE :
  Ce module reçoit des données (dictionnaires, texte) et produit un PDF.
  Il ne connaît ni la base de données ni la carte.
"""

import os
import logging
from datetime import datetime
from fpdf import FPDF

logger = logging.getLogger("crm_sig.reporting")


class FicheSynthesePDF(FPDF):
    """
    Classe personnalisée pour générer des PDF avec en-tête et pied de page
    automatiques. Hérite de FPDF (fpdf2).
    """

    def __init__(self, titre_projet: str):
        super().__init__()
        self.titre_projet = titre_projet

    def header(self):
        """En-tête affiché en haut de chaque page."""
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(15, 118, 110)  # Couleur brand-700
        self.cell(0, 10, "GeoCRM - Fiche de Synthese", new_x="LMARGIN", new_y="NEXT", align="L")
        self.set_font("Helvetica", "", 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 6, f"Projet : {self.titre_projet}", new_x="LMARGIN", new_y="NEXT", align="L")
        self.line(10, self.get_y() + 2, 200, self.get_y() + 2)
        self.ln(8)

    def footer(self):
        """Pied de page affiché en bas de chaque page."""
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}} - Genere le {datetime.now().strftime('%d/%m/%Y %H:%M')}", align="C")


def generer_fiche_synthese(
    projet_id: int,
    nom_projet: str,
    reference: str,
    statut: str,
    description: str,
    couches: list,
    chemin_sortie: str
) -> str:
    """
    Génère un PDF de synthèse récapitulant les informations d'un projet
    et la liste des couches SIG associées.

    Args:
        projet_id: L'identifiant du projet.
        nom_projet: Le nom du projet.
        reference: La référence unique (AFF-2026-XXXX).
        statut: Le statut actuel du projet.
        description: La description du projet.
        couches: Liste de dictionnaires {nom, type_geometrie, nb_entites}.
        chemin_sortie: Le dossier où sauvegarder le PDF.

    Returns:
        Le chemin complet vers le PDF généré.
    """
    try:
        pdf = FicheSynthesePDF(nom_projet)
        pdf.alias_nb_pages()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)

        # --- Section : Informations Générales ---
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(0, 10, "1. Informations Generales", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(60, 60, 60)

        infos = [
            ("Reference", reference or "N/A"),
            ("Statut", statut),
            ("Description", description or "Aucune description"),
        ]
        for label, valeur in infos:
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(45, 8, f"{label} :")
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(0, 8, str(valeur), new_x="LMARGIN", new_y="NEXT")

        pdf.ln(6)

        # --- Section : Couches SIG ---
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(0, 10, "2. Couches SIG Associees", new_x="LMARGIN", new_y="NEXT")

        if couches:
            # En-têtes du tableau
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_fill_color(240, 253, 250)  # brand-50
            pdf.set_text_color(30, 30, 30)
            pdf.cell(80, 8, "Nom de la couche", border=1, fill=True)
            pdf.cell(50, 8, "Type Geometrie", border=1, fill=True)
            pdf.cell(40, 8, "Nb Entites", border=1, fill=True, new_x="LMARGIN", new_y="NEXT")

            # Lignes du tableau
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(60, 60, 60)
            for couche in couches:
                pdf.cell(80, 7, str(couche.get("nom", "?")), border=1)
                pdf.cell(50, 7, str(couche.get("type_geometrie", "?")), border=1)
                pdf.cell(40, 7, str(couche.get("nb_entites", "?")), border=1, new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(150, 150, 150)
            pdf.cell(0, 8, "Aucune couche SIG importee pour ce projet.", new_x="LMARGIN", new_y="NEXT")

        # --- Sauvegarder ---
        os.makedirs(chemin_sortie, exist_ok=True)
        nom_fichier = f"Synthese_{reference or nom_projet.replace(' ', '_')}.pdf"
        chemin_complet = os.path.join(chemin_sortie, nom_fichier)
        pdf.output(chemin_complet)

        logger.info(f"PDF genere avec succes : {chemin_complet}")
        return chemin_complet

    except Exception as e:
        logger.error(f"Erreur lors de la generation du PDF pour le projet '{nom_projet}': {str(e)}")
        raise RuntimeError(f"Erreur generation PDF : {str(e)}")
