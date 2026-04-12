# -*- coding: utf-8 -*-
"""
DeStock App - config.py
Centralisation des parametres d'environnement et des constantes applicatives.
Charge le fichier .env et expose les variables utilisees par le reste de l'app.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Racine du projet (le dossier qui contient ce fichier)
BASE_DIR = Path(__file__).resolve().parent

# Chargement du .env si present
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Base de donnees
# ---------------------------------------------------------------------------
# SQLAlchemy URL : SQLite par defaut, switch PostgreSQL possible via .env
# Exemple PostgreSQL : postgresql+psycopg2://user:password@localhost:5432/destock
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'destock.db'}")

# ---------------------------------------------------------------------------
# Securite
# ---------------------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET", "destock-dev-secret-change-me")

# ---------------------------------------------------------------------------
# Cles API externes (chargees depuis le .env, editables via l'UI Parametres)
# ---------------------------------------------------------------------------
BSTOCK_EMAIL = os.getenv("BSTOCK_EMAIL", "")
BSTOCK_PASSWORD = os.getenv("BSTOCK_PASSWORD", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
EBAY_API_KEY = os.getenv("EBAY_API_KEY", "")

# ---------------------------------------------------------------------------
# Valeurs par defaut injectees dans la table `parametres` lors du 1er run
# ---------------------------------------------------------------------------
DEFAULT_PARAMETRES = [
    # (cle, valeur, description)
    ("profil_statut", "Auto-entrepreneur", "Statut juridique de l'activite"),
    ("profil_ca_annuel", "0", "CA annuel cumule (auto-entrepreneur)"),
    ("profil_taux_tva", "0", "Taux de TVA applicable en %"),

    ("business_frais_bstock_pct", "5", "Frais B-Stock en % sur le montant de l'enchere"),
    ("business_seuil_stock_mort_jours", "30", "Seuil en jours avant alerte stock mort"),
    ("business_marge_min_pct", "20", "Marge minimale cible en %"),
    ("business_canaux_actifs", "LBC,Vinted,eBay", "Canaux de revente actifs (liste CSV)"),
    ("business_categories_surveillees", "", "Categories B-Stock surveillees (liste CSV)"),

    ("api_bstock_email", "", "Identifiant B-Stock (email)"),
    ("api_bstock_password", "", "Mot de passe B-Stock"),
    ("api_telegram_token", "", "Token bot Telegram"),
    ("api_telegram_chat_id", "", "Chat ID Telegram"),
    ("api_anthropic_key", "", "Cle API Anthropic"),
    ("api_ebay_key", "", "Cle API eBay"),
]

# Liste des categories B-Stock disponibles dans le multiselect de l'UI
CATEGORIES_BSTOCK = [
    "Electronics",
    "Home & Kitchen",
    "Tools & Home Improvement",
    "Toys & Games",
    "Sports & Outdoors",
    "Beauty & Personal Care",
    "Clothing, Shoes & Jewelry",
    "Books",
    "Office Products",
    "Pet Supplies",
    "Baby Products",
    "Automotive",
]

# Statuts juridiques proposes dans l'UI
STATUTS_JURIDIQUES = ["Aucun", "Auto-entrepreneur", "SAS", "SARL"]
