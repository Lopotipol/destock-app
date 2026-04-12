# -*- coding: utf-8 -*-
"""
DeStock App - database.py
Modeles SQLAlchemy (SQLite en dev, compatible PostgreSQL en prod).

Toutes les tables du Module 0 sont declarees ici. La fonction `init_db()`
cree les tables manquantes et injecte les valeurs par defaut (parametres
+ utilisateurs Paul / Mael).
"""

from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Text,
    Boolean,
    DateTime,
    ForeignKey,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from config import DATABASE_URL, DEFAULT_PARAMETRES

# ---------------------------------------------------------------------------
# Moteur SQLAlchemy
# ---------------------------------------------------------------------------
# `check_same_thread=False` uniquement pour SQLite (Streamlit fait du multi-thread).
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


# ---------------------------------------------------------------------------
# Modeles
# ---------------------------------------------------------------------------
class User(Base):
    """Utilisateur autorise a se connecter a l'application."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nom = Column(String(50), unique=True, nullable=False)
    mot_de_passe_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="admin")  # admin / user
    date_creation = Column(DateTime, default=datetime.utcnow)


class Parametre(Base):
    """Cle/valeur generique pour stocker la configuration de l'app."""
    __tablename__ = "parametres"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cle = Column(String(100), unique=True, nullable=False)
    valeur = Column(Text, default="")
    description = Column(Text, default="")


class Lot(Base):
    """Un lot achete sur B-Stock (regroupe plusieurs articles)."""
    __tablename__ = "lots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lot_id = Column(String(100), unique=True, nullable=False)   # identifiant B-Stock
    url_bstock = Column(Text, default="")
    statut = Column(String(30), default="en_enchere")
    # Statuts possibles : en_enchere / remporte / paye / en_transit / recu / liquide

    montant_enchere = Column(Float, default=0.0)
    frais_bstock_pct = Column(Float, default=5.0)
    frais_livraison = Column(Float, default=0.0)
    tva = Column(Float, default=0.0)
    cout_total = Column(Float, default=0.0)          # enchere + frais + transport + tva
    retail_total = Column(Float, default=0.0)        # somme des UNIT RETAIL
    nb_articles = Column(Integer, default=0)

    date_enchere = Column(DateTime, nullable=True)
    date_paiement = Column(DateTime, nullable=True)
    date_livraison_estimee = Column(DateTime, nullable=True)
    date_reception = Column(DateTime, nullable=True)

    notes = Column(Text, default="")

    articles = relationship("Article", back_populates="lot", cascade="all, delete-orphan")


class Article(Base):
    """Un article individuel issu d'un lot (une ligne du manifeste B-Stock)."""
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lot_id = Column(String(100), ForeignKey("lots.lot_id"), nullable=False)

    lpn = Column(String(100), unique=True, nullable=True)  # identifiant unique B-Stock
    asin = Column(String(50), default="")
    ean = Column(String(50), default="")
    description = Column(Text, default="")

    condition = Column(String(50), default="")          # Condition declaree (CSV)
    condition_reelle = Column(String(50), default="")   # Condition constatee a la reception
    categorie = Column(String(100), default="")
    sous_categorie = Column(String(100), default="")

    retail_price = Column(Float, default=0.0)           # UNIT RETAIL Amazon
    cout_reel = Column(Float, default=0.0)              # cout alloue (proportionnel + frais)
    cout_reconditionnnement = Column(Float, default=0.0)

    prix_cible = Column(Float, default=0.0)             # prix de revente estime
    prix_amazon = Column(Float, default=0.0)
    prix_lbc = Column(Float, default=0.0)
    prix_ebay = Column(Float, default=0.0)
    prix_vinted = Column(Float, default=0.0)

    marge_estimee = Column(Float, default=0.0)          # en %
    marge_reelle = Column(Float, default=0.0)           # en % (apres vente)
    score_roi = Column(Integer, default=0)              # 1 a 5
    canal_recommande = Column(String(20), default="")   # LBC / Vinted / eBay

    statut = Column(String(30), default="en_stock")
    # Statuts : en_stock / annonce_publiee / vendu / invendu / liquide / manquant

    statut_reception = Column(String(30), default="non_controle")
    # Statuts reception : non_controle / conforme / different / manquant
    commentaire_reception = Column(Text, default="")
    date_reception_reelle = Column(DateTime, nullable=True)

    date_reception = Column(DateTime, nullable=True)
    notes = Column(Text, default="")

    lot = relationship("Lot", back_populates="articles")


class Reception(Base):
    """Journal des receptions physiques d'articles."""
    __tablename__ = "receptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False)
    lot_id = Column(String(100), ForeignKey("lots.lot_id"), nullable=False)
    statut_reception = Column(String(30), default="ok")   # ok / manquant / casse / non_conforme
    condition_recue = Column(String(50), default="")
    note = Column(Text, default="")
    date = Column(DateTime, default=datetime.utcnow)


class Vente(Base):
    """Historique des ventes realisees."""
    __tablename__ = "ventes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False)
    canal = Column(String(20), default="")               # LBC / Vinted / eBay / Autre
    prix_vente = Column(Float, default=0.0)
    date_vente = Column(DateTime, default=datetime.utcnow)
    lien_annonce = Column(Text, default="")


class Annonce(Base):
    """Annonces publiees sur les differents canaux."""
    __tablename__ = "annonces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False)
    canal = Column(String(20), default="")
    titre = Column(Text, default="")
    description = Column(Text, default="")
    prix = Column(Float, default=0.0)
    lien = Column(Text, default="")
    statut = Column(String(20), default="brouillon")     # brouillon / publiee / expiree / vendue
    date_publication = Column(DateTime, nullable=True)


class AlerteLog(Base):
    """Journal des alertes envoyees (Telegram, UI, etc.)."""
    __tablename__ = "alertes_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(50), default="")                # stock_mort / prix / opportunite / systeme
    message = Column(Text, default="")
    lu = Column(Boolean, default=False)
    date = Column(DateTime, default=datetime.utcnow)


class PrixCache(Base):
    """
    Cache local des prix marche (Amazon / LBC / eBay) par ASIN.
    Evite de re-interroger les memes articles dans la meme journee.
    Le champ `data` contient le dict JSON retourne par analyser_article().
    """
    __tablename__ = "prix_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    asin = Column(String(50), unique=True, nullable=False, index=True)
    date = Column(DateTime, default=datetime.utcnow)
    data = Column(Text, default="")                     # JSON serialise


class Template(Base):
    """Templates d'annonces par canal et condition."""
    __tablename__ = "templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canal = Column(String(20), nullable=False)       # LBC / eBay / Vinted
    condition = Column(String(50), nullable=False)    # Warehouse Damage / etc.
    nom = Column(String(100), default="")
    template_titre = Column(Text, default="")
    template_description = Column(Text, default="")
    date_modif = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_session():
    """Retourne une nouvelle session SQLAlchemy (a fermer par l'appelant)."""
    return SessionLocal()


def init_db():
    """
    Cree les tables manquantes et injecte :
      - les parametres par defaut (si la table est vide)
      - les 2 utilisateurs par defaut (Paul / Mael) si absents

    Import tardif de `auth` pour eviter un cycle d'import database <-> auth.
    """
    Base.metadata.create_all(engine)

    from auth import hash_password

    session = get_session()
    try:
        # --- Parametres par defaut ---
        if session.query(Parametre).count() == 0:
            for cle, valeur, description in DEFAULT_PARAMETRES:
                session.add(Parametre(cle=cle, valeur=valeur, description=description))

        # --- Utilisateurs par defaut ---
        defaults = [("Paul", "paul123"), ("Mael", "mael123")]
        for nom, mdp in defaults:
            existing = session.query(User).filter_by(nom=nom).first()
            if not existing:
                session.add(User(
                    nom=nom,
                    mot_de_passe_hash=hash_password(mdp),
                    role="admin",
                ))

        session.commit()
    finally:
        session.close()

    seed_templates()


# ---------------------------------------------------------------------------
# Templates d'annonces par defaut
# ---------------------------------------------------------------------------
_DEFAULT_TEMPLATES = [
    # --- LBC ---
    ("LBC", "Warehouse Damage", "LBC - Comme neuf",
     "{marque} {modele} — Comme neuf — Retour Amazon",
     "Bonjour,\n\nJe vends un {marque} {modele} en parfait etat.\n\nProvenance : retour entrepot Amazon — produit jamais utilise par un particulier.\n\nEtat constate : Comme neuf\nPrix neuf Amazon : {retail} EUR\n{commentaire_reception}\n\nEnvoi possible via Mondial Relay ou remise en main propre sur Paris et region.\n\nPrix : {prix_cible} EUR (legerement negociable)\nSerieux uniquement, merci."),

    ("LBC", "Customer Damage", "LBC - Bon etat",
     "{marque} {modele} — Bon etat — Retour Amazon",
     "Bonjour,\n\nJe vends un {marque} {modele} retour Amazon en bon etat de fonctionnement.\n\nEtat constate : {commentaire_reception}\nPrix neuf Amazon : {retail} EUR\n\nEnvoi Mondial Relay ou main propre.\nPrix : {prix_cible} EUR"),

    ("LBC", "Carrier Damage", "LBC - Etat correct",
     "{marque} {modele} — Etat correct — Prix reduit",
     "Bonjour,\n\nJe vends un {marque} {modele}.\nEmballage abime lors du transport, produit intact a l'interieur.\n\nEtat constate : {commentaire_reception}\nPrix neuf Amazon : {retail} EUR\n\nPrix ferme : {prix_cible} EUR\nEnvoi ou main propre."),

    ("LBC", "Defective", "LBC - Defectueux",
     "{marque} {modele} — Defectueux — Pieces / Reparation",
     "Bonjour,\n\nJe vends un {marque} {modele} EN L'ETAT.\nVendu pour pieces ou remise en etat uniquement.\n\nDefaut constate : {commentaire_reception}\nPrix neuf Amazon : {retail} EUR\nReference ASIN : {asin}\n\nVendu sans garantie, prix non negociable.\nPrix : {prix_cible} EUR"),

    # --- eBay ---
    ("eBay", "Warehouse Damage", "eBay - Comme neuf",
     "{marque} {modele} — Comme neuf — Jamais utilise — {asin}",
     "<b>Etat :</b> Comme neuf — Retour entrepot Amazon<br>\n<b>Jamais utilise</b> par un particulier<br>\n<b>Prix neuf Amazon :</b> {retail} EUR<br>\n<b>Reference ASIN :</b> {asin}<br>\n<br>\n<b>Detail :</b> {commentaire_reception}<br>\n<br>\nExpedition Colissimo ou Mondial Relay.<br>\nRetour accepte sous 14 jours."),

    ("eBay", "Customer Damage", "eBay - Bon etat",
     "{marque} {modele} — Bon etat — Retour Amazon — {asin}",
     "<b>Etat :</b> Bon etat — Retour client Amazon<br>\n<b>Reference ASIN :</b> {asin}<br>\n<b>Prix neuf :</b> {retail} EUR<br>\n<b>Detail :</b> {commentaire_reception}<br>\n<br>\nExpedition rapide.<br>\nRetour accepte 14 jours."),

    ("eBay", "Carrier Damage", "eBay - Etat correct",
     "{marque} {modele} — Etat correct — Emballage abime — {asin}",
     "<b>Etat :</b> Correct — Emballage abime, produit intact<br>\n<b>Reference ASIN :</b> {asin}<br>\n<b>Prix neuf :</b> {retail} EUR<br>\n<b>Detail :</b> {commentaire_reception}<br>\n<br>\nVendu en l'etat.<br>\nExpedition Colissimo."),

    ("eBay", "Defective", "eBay - Defectueux",
     "{marque} {modele} — Defectueux — Pour pieces — {asin}",
     "<b>Etat :</b> Defectueux — Pour pieces uniquement<br>\n<b>Defaut constate :</b> {commentaire_reception}<br>\n<b>Reference ASIN :</b> {asin}<br>\n<b>Prix neuf :</b> {retail} EUR<br>\n<br>\nVendu en l'etat, sans garantie.<br>\nSans retour possible."),

    # --- Vinted ---
    ("Vinted", "Warehouse Damage", "Vinted - Comme neuf",
     "{marque} {modele} neuf jamais utilise",
     "Retour Amazon, jamais utilise.\nComme neuf, carton simplement ouvert.\n{commentaire_reception}\nPrix neuf : {retail} EUR\nEnvoi rapide ou main propre."),

    ("Vinted", "Customer Damage", "Vinted - Bon etat",
     "{marque} {modele} bon etat",
     "Retour Amazon en bon etat.\n{commentaire_reception}\nPrix neuf : {retail} EUR\nEnvoi ou main propre."),

    ("Vinted", "Carrier Damage", "Vinted - Etat correct",
     "{marque} {modele} etat correct prix reduit",
     "Emballage abime lors du transport, produit intact.\n{commentaire_reception}\nPrix neuf : {retail} EUR"),

    ("Vinted", "Defective", "Vinted - Defectueux",
     "{marque} {modele} defectueux pieces",
     "Vendu pour pieces ou remise en etat.\n{commentaire_reception}\nEn l'etat, sans garantie."),
]


def seed_templates() -> None:
    """Insere les templates par defaut si la table est vide."""
    session = get_session()
    try:
        if session.query(Template).count() > 0:
            return
        for canal, condition, nom, titre, desc in _DEFAULT_TEMPLATES:
            session.add(Template(
                canal=canal,
                condition=condition,
                nom=nom,
                template_titre=titre,
                template_description=desc,
            ))
        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    init_db()
    print("Base de donnees initialisee.")
