"""
models.py - Modèles de données pour le CRM et le SIG.

Chaque classe Python ci-dessous correspond à une table dans la base de données SQLite.
SQLAlchemy (l'ORM) traduit automatiquement ces classes en tables SQL.

Entités principales :
  - Client  : Informations sur le donneur d'ordres.
  - Projet  : L'affaire / le chantier avec son statut et son chemin de dossier local.
  - CoucheSIG : Un fichier géospatial (.shp) rattaché à un projet.
"""

from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class Client(Base):
    """Table des clients / donneurs d'ordres."""
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    nom = Column(String(255), nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True)
    telephone = Column(String(50), nullable=True)
    adresse = Column(Text, nullable=True)
    nomenclature = Column(JSON, nullable=True) # Stocke les règles de nommage par client
    date_creation = Column(DateTime, default=datetime.utcnow)

    # Relation : un client peut avoir plusieurs projets
    projets = relationship("Projet", back_populates="client", lazy="selectin")

    def __repr__(self):
        return f"<Client(id={self.id}, nom='{self.nom}')>"


class Projet(Base):
    """
    Table des projets / affaires.
    Chaque projet possède un dossier physique sur le disque dur
    (créé automatiquement par le module CRM).
    """
    __tablename__ = "projets"

    id = Column(Integer, primary_key=True, index=True)
    nom = Column(String(255), nullable=False, index=True)
    reference = Column(String(100), unique=True, nullable=True)  # Ex: AFF-2026-0001
    description = Column(Text, nullable=True)
    statut = Column(String(50), default="Nouveau")  # Nouveau, En cours, Terminé, Archivé
    date_creation = Column(DateTime, default=datetime.utcnow)
    date_modification = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    chemin_dossier = Column(String(500))  # Chemin absolu vers le dossier physique sur le PC

    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)

    # Relations
    client = relationship("Client", back_populates="projets")
    couches = relationship("CoucheSIG", back_populates="projet", cascade="all, delete-orphan", lazy="selectin")
    logs = relationship("LogGeneration", back_populates="projet", cascade="all, delete-orphan", lazy="selectin")

    def __repr__(self):
        return f"<Projet(id={self.id}, nom='{self.nom}', statut='{self.statut}')>"


class LogGeneration(Base):
    """
    Journal d'activité pour les livrables générés.
    Qui a généré quoi, quand et sur quel projet.
    """
    __tablename__ = "logs_generation"

    id = Column(Integer, primary_key=True, index=True)
    projet_id = Column(Integer, ForeignKey("projets.id"), nullable=False)
    utilisateur_id = Column(Integer, ForeignKey("utilisateurs.id"), nullable=True) # Optionnel si l'auth n'est pas encore stricte
    type_livrable = Column(String(100), nullable=False) # ex: "SHP", "KMZ", "Rapport APD_HTL", "Plan de Câblage"
    details = Column(Text, nullable=True) # Infos supplémentaires
    date_generation = Column(DateTime, default=datetime.utcnow)

    projet = relationship("Projet", back_populates="logs")
    utilisateur = relationship("Utilisateur")

    def __repr__(self):
        return f"<LogGeneration(projet_id={self.projet_id}, type_livrable='{self.type_livrable}')>"


class CoucheSIG(Base):
    """
    Table des couches géospatiales.
    Chaque couche est un fichier Shapefile (.shp) rattaché à un projet.
    On stocke les métadonnées extraites pour le panneau latéral de la carte.
    """
    __tablename__ = "couches_sig"

    id = Column(Integer, primary_key=True, index=True)
    nom = Column(String(255), nullable=False)
    type_geometrie = Column(String(50))   # Point, LineString, Polygon, MultiPolygon...
    chemin_fichier = Column(String(500))   # Chemin relatif vers le .shp dans le dossier du projet
    systeme_projection = Column(String(50))  # Code EPSG (ex: "4326", "2154")
    nb_entites = Column(Integer, default=0)   # Nombre d'objets géographiques
    couleur = Column(String(20), default="#3388ff")  # Couleur d'affichage sur la carte
    date_import = Column(DateTime, default=datetime.utcnow)

    projet_id = Column(Integer, ForeignKey("projets.id"), nullable=False)

    # Relation
    projet = relationship("Projet", back_populates="couches")

    def __repr__(self):
        return f"<CoucheSIG(id={self.id}, nom='{self.nom}', type='{self.type_geometrie}')>"


class Utilisateur(Base):
    """
    Table des utilisateurs (Accès).
    Gère les collaborateurs ayant accès à la plateforme.
    """
    __tablename__ = "utilisateurs"

    id = Column(Integer, primary_key=True, index=True)
    nom = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    role = Column(String(50), default="Lecteur")  # Admin, Editeur, Lecteur
    date_creation = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Utilisateur(id={self.id}, nom='{self.nom}', role='{self.role}')>"
