import logging
import os

# Configuration globale du système de logs
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILE = "error.log"

# On crée un logger racine pour l'application
logger = logging.getLogger("crm_sig")
logger.setLevel(logging.DEBUG)

# Handler pour écrire dans le fichier error.log
file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
file_handler.setLevel(logging.ERROR) # Seules les erreurs vont dans le fichier par défaut
file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

# Handler pour la console (pour voir les infos en temps réel quand on lance run.py)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(LOG_FORMAT))

logger.addHandler(file_handler)
logger.addHandler(console_handler)
