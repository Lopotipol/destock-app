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

from database import init_db
from auth import (
    login_form,
    logout,
    is_logged_in,
    current_user_nom,
)


def init_env_params() -> None:
    """
    Au demarrage sur Render/Railway, lit les variables d'environnement
    et les sauvegarde en base si elles ne sont pas deja configurees.
    Les cles correspondent a celles utilisees dans l'app.
    """
    from modules.parametres import get_param, set_param

    mappings = {
        "BSTOCK_EMAIL":      "api_bstock_email",
        "BSTOCK_PASSWORD":   "api_bstock_password",
        "TELEGRAM_TOKEN":    "api_telegram_token",
        "TELEGRAM_CHAT_ID":  "api_telegram_chat_id",
        "ANTHROPIC_API_KEY": "api_anthropic_key",
    }
    for env_key, param_key in mappings.items():
        env_val = os.environ.get(env_key)
        if env_val and not get_param(param_key, ""):
            set_param(param_key, env_val)

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

# Seed des parametres depuis les variables d'environnement (cloud first run)
init_env_params()

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
    """Tableau de bord : vue d'ensemble temps reel."""
    from datetime import datetime
    from database import Article, Annonce, Vente, Lot, get_session

    st.title("Tableau de bord")

    session = get_session()
    try:
        # Donnees de base
        now = datetime.utcnow()
        debut_mois = datetime(now.year, now.month, 1)

        all_articles = session.query(Article).all()
        en_stock = [a for a in all_articles if a.statut == "en_stock"]
        ventes_mois = session.query(Vente).filter(Vente.date_vente >= debut_mois).all()
        ca_mois = sum(v.prix_vente or 0 for v in ventes_mois)
        benef_mois = sum(
            (v.prix_vente or 0)
            - (session.query(Article).filter_by(id=v.article_id).first().cout_reel or 0)
            for v in ventes_mois
            if session.query(Article).filter_by(id=v.article_id).first()
        )

        # --- LIGNE 1 : Metriques principales ---
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Articles en stock", len(en_stock))
        m2.metric("Vendus ce mois", len(ventes_mois))
        m3.metric("CA du mois", f"{ca_mois:,.0f} EUR")
        m4.metric("Benefice du mois", f"{benef_mois:,.0f} EUR")

        st.divider()

        # --- LIGNE 2 : Alertes & actions urgentes ---
        st.markdown("**Actions urgentes**")
        stock_mort = sum(
            1 for a in en_stock
            if a.date_reception and (now - a.date_reception).days > 30
        )
        annonces_gen = session.query(Annonce).filter_by(statut="generee").count()
        annonces_pub = sum(1 for a in all_articles if a.statut == "annonce_publiee")

        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Stock mort (+30j)", stock_mort)
        a2.metric("Annonces a publier", annonces_gen)
        a3.metric("Ventes a enregistrer", annonces_pub)
        nb_lots = session.query(Lot).count()
        a4.metric("Lots en base", nb_lots)

        st.divider()

        # --- LIGNE 3 : Activite recente ---
        st.markdown("**Activite recente**")
        ventes_recentes = (
            session.query(Vente)
            .order_by(Vente.date_vente.desc())
            .limit(5)
            .all()
        )
        if ventes_recentes:
            for v in ventes_recentes:
                art = session.query(Article).filter_by(id=v.article_id).first()
                desc = (art.description or "")[:45] if art else "?"
                date_str = v.date_vente.strftime("%d/%m") if v.date_vente else ""
                st.caption(
                    f"- {desc} — **{v.prix_vente or 0:,.0f} EUR** "
                    f"via {v.canal or '?'} ({date_str})"
                )
        else:
            st.caption("Aucune vente enregistree.")

        st.divider()

        # --- LIGNE 4 : Stock par etat ---
        st.markdown("**Stock par etat**")
        conditions: dict[str, int] = {}
        for a in en_stock:
            c = a.condition or "Autre"
            conditions[c] = conditions.get(c, 0) + 1
        total_stock = len(en_stock) or 1
        cols = st.columns(len(conditions) or 1)
        for i, (cond, count) in enumerate(sorted(conditions.items(), key=lambda x: -x[1])):
            with cols[i % len(cols)]:
                st.metric(cond, count)
                st.progress(count / total_stock)

    finally:
        session.close()

    st.divider()

    # --- LIGNE 5 : Raccourcis rapides ---
    st.markdown("**Raccourcis**")

    def go_marketplace():
        st.session_state["sidebar_nav"] = "Marketplace B-Stock"

    def go_annonces():
        st.session_state["sidebar_nav"] = "Annonces"

    def go_stock():
        st.session_state["sidebar_nav"] = "Stock & Articles"

    def go_pnl():
        st.session_state["sidebar_nav"] = "P&L / Finances"

    r1, r2, r3, r4 = st.columns(4)
    with r1:
        st.button("Analyser un lot", on_click=go_marketplace, use_container_width=True, key="dash_marketplace")
    with r2:
        st.button("Generer une annonce", on_click=go_annonces, use_container_width=True, key="dash_annonces")
    with r3:
        st.button("Enregistrer une vente", on_click=go_stock, use_container_width=True, key="dash_stock")
    with r4:
        st.button("Voir P&L", on_click=go_pnl, use_container_width=True, key="dash_pnl")


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
