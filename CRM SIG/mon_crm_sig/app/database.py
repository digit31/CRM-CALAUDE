"""
database.py - Configuration de la base de données SQLite locale.

Ce module gère la connexion à la base de données via SQLAlchemy (ORM).
SQLite est utilisé pour la simplicité (un seul fichier .db sur le PC).
Si un jour on veut migrer vers PostgreSQL/PostGIS, il suffit de changer
la variable SQLITE_DATABASE_URL ici sans toucher au reste de l'application.
"""

import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger("crm_sig.database")

# Chemin absolu vers la base de données, dans le dossier du projet
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE_PATH = os.path.join(BASE_DIR, "crm_sig.db")
SQLITE_DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

# Dossier racine où seront stockés les projets physiquement sur le disque
PROJECTS_DATA_DIR = os.path.join(BASE_DIR, "app", "static", "projects_data")
os.makedirs(PROJECTS_DATA_DIR, exist_ok=True)

# Paramètre check_same_thread=False nécessaire pour SQLite + FastAPI (multi-thread)
engine = create_engine(
    SQLITE_DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False  # Mettre à True pour débugger les requêtes SQL
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

logger.info(f"Base de données configurée : {DATABASE_PATH}")


def get_db():
    """
    Dépendance FastAPI : ouvre une session de base de données
    et la ferme automatiquement à la fin de la requête.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
