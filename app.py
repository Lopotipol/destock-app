# -*- coding: utf-8 -*-
"""
DeStock App - app.py
Point d'entree Streamlit :
  - initialise la base de donnees
  - gere l'authentification
  - affiche la navigation sidebar et route vers chaque module
"""

import os
import streamlit as st

# Configuration cloud (Railway, Render, etc.)
if os.environ.get("ENVIRONMENT") == "cloud":
    st.set_option("server.enableCORS", False)
    st.set_option("server.enableXsrfProtection", False)

from database import init_db
from auth import (
    login_form,
    logout,
    is_logged_in,
    current_user_nom,
)

# Modules metier (tous importes, meme les placeholders)
from modules import (
    parametres,
    marketplace,
    encheres,
    reception,
    stock,
    annonces,
    pnl,
    alertes,
)

# ---------------------------------------------------------------------------
# Configuration globale Streamlit
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="DeStock App",
    page_icon="D",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialisation de la base (cree tables + parametres + users par defaut)
init_db()

# Monitoring automatique des alertes (1x par session, non-bloquant)
from modules.alertes import run_monitoring
run_monitoring()

# ---------------------------------------------------------------------------
# Table de routage : nom affiche -> fonction du module a appeler
# ---------------------------------------------------------------------------
PAGES = {
    "Tableau de bord": None,              # placeholder gere inline
    "Marketplace B-Stock": marketplace.render,
    "Encheres & Lots": encheres.render,
    "Reception": reception.render,
    "Stock & Articles": stock.render,
    "Annonces": annonces.render,
    "P&L / Finances": pnl.render,
    "Alertes": alertes.render,
    "Parametres": parametres.render,
}


def _render_dashboard() -> None:
    """Placeholder du tableau de bord (sera developpe au Module suivant)."""
    st.title("Tableau de bord")
    st.info("Module en construction - vue d'ensemble des lots, ventes et alertes.")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Lots actifs", "-")
    col2.metric("Articles en stock", "-")
    col3.metric("CA du mois", "-")
    col4.metric("Alertes non lues", "-")


def _render_sidebar() -> str:
    """Construit la sidebar et retourne la page selectionnee."""
    # Import tardif pour eviter de charger Playwright au demarrage
    from scrapers import bstock as bstock_scraper

    with st.sidebar:
        st.markdown("## DeStock App")
        st.caption(f"Connecte en tant que **{current_user_nom()}**")

        # Indicateur d'etat du profil B-Stock
        if bstock_scraper.is_profile_configured():
            st.success("B-Stock : connecte", icon=None)
        else:
            st.error("B-Stock : non configure", icon=None)

        st.divider()

        page = st.radio(
            "Navigation",
            list(PAGES.keys()),
            label_visibility="collapsed",
            key="sidebar_nav",
        )

        st.divider()
        if st.button("Se deconnecter", use_container_width=True, key="sidebar_logout"):
            logout()

    return page


# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------
def main() -> None:
    if not is_logged_in():
        login_form()
        return

    page = _render_sidebar()
    renderer = PAGES.get(page)

    # Tout le contenu principal est isole dans un container dedie.
    # Streamlit efface le DOM entre runs, mais le container rend l'intention
    # explicite et evite tout melange accidentel entre modules.
    main_area = st.container()
    with main_area:
        if renderer is None:
            _render_dashboard()
        else:
            renderer()


main()
