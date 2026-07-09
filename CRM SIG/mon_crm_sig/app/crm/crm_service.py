"""
crm_service.py - Module CRM : Logique de gestion de projet.

Ce module est le "cerveau" de la partie CRM. Il gère :
  - La création d'un projet (avec génération automatique du dossier physique).
  - La mise à jour du statut d'un projet.
  - La génération d'une référence unique pour chaque affaire.
  - Le listing et la suppression des projets.

PRINCIPE D'INDÉPENDANCE :
  Ce module ne sait RIEN de la carte ou du SIG.
  Il ne fait que gérer des données CRM et des dossiers sur le disque.
"""

import os
import re
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app import models
from app.database import PROJECTS_DATA_DIR

logger = logging.getLogger("crm_sig.crm")

# Sous-dossiers standards créés pour chaque nouveau projet
SOUS_DOSSIERS_PROJET = [
    "01_Inputs_SHP",       # Fichiers sources (Shapefiles d'entrée)
    "02_Traitement",       # Fichiers intermédiaires de travail
    "03_Livrables_PDF",    # Plans et rapports exportés en PDF
]


def generer_reference(db: Session) -> str:
    """
    Génère une référence unique pour un projet au format AFF-AAAA-NNNN.
    Exemple : AFF-2026-0001, AFF-2026-0002, etc.

    Basé sur le plus grand numéro déjà utilisé cette année + 1 (robuste aux
    suppressions : un simple count réutiliserait un numéro déjà pris et
    violerait la contrainte d'unicité), avec garde-fou d'unicité.
    """
    annee = datetime.utcnow().year
    prefixe = f"AFF-{annee}-"
    refs = [r for (r,) in db.query(models.Projet.reference).filter(
        models.Projet.reference.like(f"{prefixe}%")
    ).all() if r]
    max_n = 0
    for r in refs:
        try:
            max_n = max(max_n, int(r.rsplit("-", 1)[-1]))
        except (ValueError, IndexError):
            pass
    existantes = set(refs)
    n = max_n + 1
    while f"{prefixe}{n:04d}" in existantes:  # garde-fou anti-collision
        n += 1
    return f"{prefixe}{n:04d}"


def creer_dossier_projet(projet_id: int, nom_projet: str) -> str:
    """
    Crée l'arborescence physique sur le disque dur pour un nouveau projet.
    Retourne le chemin absolu vers le dossier racine du projet.
    """
    # Nom de dossier sécurisé : whitelist stricte (évite les caractères interdits
    # Windows \ / : * ? " < > | qui feraient échouer os.makedirs), tronqué.
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", nom_projet or "").strip("_") or "projet"
    nom_dossier = f"{projet_id:04d}_{safe[:60]}"
    chemin_base = os.path.join(PROJECTS_DATA_DIR, nom_dossier)

    for dossier in SOUS_DOSSIERS_PROJET:
        chemin_complet = os.path.join(chemin_base, dossier)
        os.makedirs(chemin_complet, exist_ok=True)
        logger.info(f"Dossier créé : {chemin_complet}")

    return chemin_base


def creer_projet(db: Session, nom: str, description: str = "", client_id: int = None) -> models.Projet:
    """
    Crée un nouveau projet dans la base de données ET génère
    automatiquement le dossier physique avec ses sous-dossiers.
    """
    # 1) Persister la ligne (référence UNIQUE) avec retry : deux créations
    #    concurrentes peuvent calculer la même référence -> IntegrityError -> on
    #    régénère et on réessaie au lieu d'un 500.
    nouveau_projet = None
    for tentative in range(5):
        try:
            reference = generer_reference(db)
            nouveau_projet = models.Projet(
                nom=nom, reference=reference, description=description,
                client_id=client_id, chemin_dossier="",
            )
            db.add(nouveau_projet)
            db.commit()
            db.refresh(nouveau_projet)
            break
        except IntegrityError:
            db.rollback()
            nouveau_projet = None
            logger.warning(f"Collision de référence à la création de '{nom}' "
                           f"(tentative {tentative + 1}), nouvel essai…")
    if nouveau_projet is None:
        raise RuntimeError(f"Impossible de générer une référence unique pour '{nom}'.")

    # 2) Créer le dossier APRÈS le commit ; si ça échoue, supprimer la ligne
    #    (pas de projet orphelin avec chemin_dossier vide).
    try:
        chemin_dossier = creer_dossier_projet(nouveau_projet.id, nom)
        nouveau_projet.chemin_dossier = chemin_dossier
        db.commit()
        db.refresh(nouveau_projet)
        logger.info(f"Projet créé : {reference} - '{nom}' -> {chemin_dossier}")
        return nouveau_projet
    except Exception as e:
        logger.error(f"Création du dossier du projet '{nom}' échouée : {e} — "
                     "suppression de la ligne pour éviter un orphelin.")
        try:
            db.delete(nouveau_projet)
            db.commit()
        except Exception:
            db.rollback()
        raise


def lister_projets(db: Session) -> list:
    """Retourne la liste de tous les projets, du plus récent au plus ancien."""
    return db.query(models.Projet).order_by(models.Projet.date_creation.desc()).all()


def obtenir_projet(db: Session, projet_id: int) -> models.Projet:
    """Retourne un projet par son ID, ou None s'il n'existe pas."""
    return db.query(models.Projet).filter(models.Projet.id == projet_id).first()


def mettre_a_jour_statut(db: Session, projet_id: int, nouveau_statut: str) -> models.Projet:
    """
    Met à jour le statut d'un projet.
    Statuts possibles : Nouveau, En cours, Terminé, Archivé.
    """
    projet = obtenir_projet(db, projet_id)
    if projet:
        ancien_statut = projet.statut
        projet.statut = nouveau_statut
        projet.date_modification = datetime.utcnow()
        db.commit()
        db.refresh(projet)
        logger.info(f"Projet #{projet_id} : statut '{ancien_statut}' -> '{nouveau_statut}'")
    return projet


def supprimer_projet(db: Session, projet_id: int) -> bool:
    """
    Supprime un projet de la base de données.
    Note : Ne supprime PAS le dossier physique (sécurité).
    """
    projet = obtenir_projet(db, projet_id)
    if projet:
        logger.info(f"Suppression du projet #{projet_id} - '{projet.nom}'")
        db.delete(projet)
        db.commit()
        return True
    return False


# --- Gestion des Clients ---

def creer_client(db: Session, nom: str, email: str = None, telephone: str = None, adresse: str = None) -> models.Client:
    """Crée un nouveau client dans la base de données."""
    try:
        # Normaliser les champs vides en NULL : email est UNIQUE, or deux chaînes
        # vides '' entreraient en collision (contrainte UNIQUE), alors que
        # plusieurs NULL sont autorisés (client sans email = cas courant).
        email = (email or "").strip() or None
        telephone = (telephone or "").strip() or None
        adresse = (adresse or "").strip() or None
        nom = (nom or "").strip()
        nouveau_client = models.Client(
            nom=nom,
            email=email,
            telephone=telephone,
            adresse=adresse,
        )
        db.add(nouveau_client)
        db.commit()
        db.refresh(nouveau_client)
        logger.info(f"Client créé : '{nom}'")
        return nouveau_client
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la création du client '{nom}': {str(e)}")
        raise


def lister_clients(db: Session) -> list:
    """Retourne la liste de tous les clients."""
    return db.query(models.Client).order_by(models.Client.nom).all()


def obtenir_client(db: Session, client_id: int) -> models.Client:
    return db.query(models.Client).filter(models.Client.id == client_id).first()


def supprimer_client(db: Session, client_id: int) -> bool:
    client = obtenir_client(db, client_id)
    if client:
        db.delete(client)
        db.commit()
        return True
    return False

def maj_nomenclature_client(db: Session, client_id: int, nomenclature: dict):
    client = db.query(models.Client).filter(models.Client.id == client_id).first()
    if client:
        client.nomenclature = nomenclature
        db.commit()
        db.refresh(client)
        return client
    return None

def enregistrer_log(db: Session, projet_id: int, type_livrable: str, utilisateur_id: int = None, details: str = None):
    log = models.LogGeneration(
        projet_id=projet_id,
        utilisateur_id=utilisateur_id,
        type_livrable=type_livrable,
        details=details
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log

def lister_logs(db: Session, limit: int = 50):
    return db.query(models.LogGeneration).order_by(models.LogGeneration.date_generation.desc()).limit(limit).all()


# --- Gestion des Utilisateurs (Accès) ---

def creer_utilisateur(db: Session, nom: str, email: str, role: str = "Lecteur") -> models.Utilisateur:
    try:
        nouvel_utilisateur = models.Utilisateur(
            nom=nom,
            email=email,
            role=role
        )
        db.add(nouvel_utilisateur)
        db.commit()
        db.refresh(nouvel_utilisateur)
        logger.info(f"Utilisateur créé : '{nom}' avec rôle '{role}'")
        return nouvel_utilisateur
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la création de l'utilisateur '{nom}': {str(e)}")
        raise


def lister_utilisateurs(db: Session) -> list:
    return db.query(models.Utilisateur).order_by(models.Utilisateur.nom).all()


def obtenir_utilisateur(db: Session, user_id: int) -> models.Utilisateur:
    return db.query(models.Utilisateur).filter(models.Utilisateur.id == user_id).first()


def supprimer_utilisateur(db: Session, user_id: int) -> bool:
    user = obtenir_utilisateur(db, user_id)
    if user:
        db.delete(user)
        db.commit()
        return True
    return False
