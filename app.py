# -*- coding: utf-8 -*-
"""
DeStock App - app.py
Point d'entree Streamlit :
  - initialise la base de donnees
  - gere l'authentification
  - applique le design system (CSS global, fonts DM Sans/Mono)
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


# ---------------------------------------------------------------------------
# Seed des parametres depuis les variables d'environnement
# ---------------------------------------------------------------------------
def init_env_params() -> None:
    """Ecrit les env vars BSTOCK_* / TELEGRAM_* / ANTHROPIC_API_KEY en base."""
    from modules.parametres import set_param
    mappings = {
        "BSTOCK_EMAIL":      "api_bstock_email",
        "BSTOCK_PASSWORD":   "api_bstock_password",
        "TELEGRAM_TOKEN":    "api_telegram_token",
        "TELEGRAM_CHAT_ID":  "api_telegram_chat_id",
        "ANTHROPIC_API_KEY": "api_anthropic_key",
    }
    for env_key, param_key in mappings.items():
        env_val = os.environ.get(env_key)
        if env_val:
            set_param(param_key, env_val)
            print(f"Set {param_key} from env")


# Modules metier
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
    page_title="DeStock",
    page_icon="D",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()
init_env_params()

from modules.alertes import run_monitoring
run_monitoring()


# ---------------------------------------------------------------------------
# Design system : CSS global (fonts, couleurs, composants)
# ---------------------------------------------------------------------------
_GLOBAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Mono:wght@400;500;600&display=swap');

html, body, [class*="css"] {
  font-family: 'DM Sans', sans-serif !important;
}
.main { background-color: #f4f5f7 !important; }
.block-container {
  padding: 28px 32px !important;
  max-width: 100% !important;
}

/* SIDEBAR */
[data-testid="stSidebar"] {
  background: #0f1623 !important;
  border-right: none !important;
}
[data-testid="stSidebar"] * {
  color: #8b95a7 !important;
  font-family: 'DM Sans', sans-serif !important;
}
[data-testid="stSidebar"] .stRadio label {
  padding: 8px 10px !important;
  border-radius: 5px !important;
  font-size: 13.5px !important;
  font-weight: 400 !important;
  margin-bottom: 1px !important;
  cursor: pointer !important;
  transition: all 0.12s !important;
  display: block !important;
}
[data-testid="stSidebar"] .stRadio label:hover {
  background: #1a2540 !important;
  color: #cbd5e1 !important;
}

/* TITRES */
h1, .stApp h1 {
  color: #0f172a !important;
  font-size: 22px !important;
  font-weight: 700 !important;
  letter-spacing: -0.02em !important;
  font-family: 'DM Sans', sans-serif !important;
}
h2, .stApp h2 {
  color: #0f172a !important;
  font-size: 15px !important;
  font-weight: 600 !important;
  font-family: 'DM Sans', sans-serif !important;
}
h3, .stApp h3 {
  color: #64748b !important;
  font-size: 13px !important;
  font-weight: 600 !important;
  font-family: 'DM Sans', sans-serif !important;
}

/* METRIQUES */
[data-testid="metric-container"] {
  background: white !important;
  border: 1px solid #e2e8f0 !important;
  border-radius: 12px !important;
  padding: 16px 18px !important;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06) !important;
  border-top: 3px solid #2563eb !important;
}
[data-testid="stMetricLabel"] {
  color: #94a3b8 !important;
  font-size: 10.5px !important;
  font-weight: 600 !important;
  text-transform: uppercase !important;
  letter-spacing: 0.06em !important;
  font-family: 'DM Sans', sans-serif !important;
}
[data-testid="stMetricValue"] {
  color: #0f172a !important;
  font-size: 26px !important;
  font-weight: 700 !important;
  font-family: 'DM Mono', monospace !important;
  letter-spacing: -0.02em !important;
}
[data-testid="stMetricDelta"] {
  font-size: 12px !important;
  font-weight: 500 !important;
}

/* BOUTONS */
.stButton > button {
  border-radius: 6px !important;
  font-weight: 600 !important;
  font-size: 13px !important;
  border: 1px solid #e2e8f0 !important;
  background: white !important;
  color: #64748b !important;
  padding: 7px 16px !important;
  transition: all 0.12s !important;
  font-family: 'DM Sans', sans-serif !important;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05) !important;
}
.stButton > button:hover {
  background: #f8fafc !important;
  border-color: #2563eb !important;
  color: #2563eb !important;
  transform: translateY(-1px) !important;
}
.stButton > button[kind="primary"] {
  background: #2563eb !important;
  color: white !important;
  border-color: #2563eb !important;
}
.stButton > button[kind="primary"]:hover {
  background: #1d4ed8 !important;
  color: white !important;
}

/* TABS */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
  background: transparent !important;
  border-bottom: 1px solid #e2e8f0 !important;
  gap: 0 !important;
}
[data-testid="stTabs"] [data-baseweb="tab"] {
  font-weight: 600 !important;
  font-size: 13px !important;
  color: #94a3b8 !important;
  padding: 10px 18px !important;
  border-radius: 0 !important;
  background: transparent !important;
  font-family: 'DM Sans', sans-serif !important;
}
[data-testid="stTabs"] [aria-selected="true"] {
  color: #2563eb !important;
  border-bottom: 2px solid #2563eb !important;
}

/* TABLEAUX */
[data-testid="stDataFrame"] {
  border-radius: 10px !important;
  overflow: hidden !important;
  border: 1px solid #e2e8f0 !important;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04) !important;
}
[data-testid="stDataFrame"] table {
  font-family: 'DM Sans', sans-serif !important;
}

/* INPUTS */
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input {
  border-radius: 6px !important;
  border: 1px solid #e2e8f0 !important;
  font-family: 'DM Sans', sans-serif !important;
  font-size: 13px !important;
  background: white !important;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stNumberInput"] input:focus {
  border-color: #2563eb !important;
  box-shadow: 0 0 0 3px rgba(37,99,235,0.08) !important;
}
[data-testid="stSelectbox"] > div > div {
  border-radius: 6px !important;
  border: 1px solid #e2e8f0 !important;
  font-family: 'DM Sans', sans-serif !important;
  font-size: 13px !important;
  background: white !important;
}
[data-testid="stAlert"] {
  border-radius: 8px !important;
  border: none !important;
  font-family: 'DM Sans', sans-serif !important;
  font-size: 13px !important;
}
[data-testid="stExpander"] {
  border: 1px solid #e2e8f0 !important;
  border-radius: 10px !important;
  background: white !important;
  box-shadow: 0 1px 2px rgba(0,0,0,0.04) !important;
}
hr { border-color: #e2e8f0 !important; margin: 20px 0 !important; }
[data-testid="stProgressBar"] > div {
  background: #dbeafe !important;
  border-radius: 99px !important;
}
[data-testid="stProgressBar"] > div > div {
  background: #2563eb !important;
  border-radius: 99px !important;
}
[data-testid="stMultiSelect"] span {
  background: #dbeafe !important;
  color: #1d4ed8 !important;
  border-radius: 4px !important;
  font-size: 11px !important;
  font-weight: 600 !important;
}
[data-testid="stCheckbox"] {
  font-size: 13px !important;
  font-family: 'DM Sans', sans-serif !important;
}
[data-testid="stFileUploader"] {
  border: 1.5px dashed #e2e8f0 !important;
  border-radius: 10px !important;
  background: #fafafa !important;
}
</style>
"""


# ---------------------------------------------------------------------------
# Helpers visuels reutilisables dans tous les modules
# ---------------------------------------------------------------------------
def section_header(title: str, subtitle: str | None = None) -> None:
    """Titre de section avec sous-titre optionnel."""
    html = (
        "<div style='margin-bottom: 20px;'>"
        f"<div style='font-size: 22px; font-weight: 700; color: #0f172a; "
        f"letter-spacing: -0.02em; font-family: DM Sans, sans-serif;'>{title}</div>"
    )
    if subtitle:
        html += (
            f"<div style='font-size: 13px; color: #64748b; margin-top: 3px; "
            f"font-weight: 400;'>{subtitle}</div>"
        )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def info_card(title: str, content: str, color: str = "#2563eb") -> None:
    """Card coloree pour mettre en avant une info."""
    st.markdown(
        f"""
        <div style='background: white; border-radius: 10px;
                    border: 1px solid #e2e8f0;
                    border-left: 4px solid {color};
                    padding: 14px 18px;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
                    margin-bottom: 12px;
                    font-family: DM Sans, sans-serif;'>
          <div style='font-size: 11px; font-weight: 700;
                      color: #94a3b8; text-transform: uppercase;
                      letter-spacing: 0.06em; margin-bottom: 4px;'>
            {title}
          </div>
          <div style='color: #0f172a; font-size: 13.5px; line-height: 1.5;'>
            {content}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Table de routage : nom affiche -> fonction du module a appeler
# ---------------------------------------------------------------------------
PAGES = {
    "Accueil":              None,                # Dashboard gere inline
    "Trouver un lot":       marketplace.render,
    "Mes achats":           encheres.render,
    "Reception palette":    reception.render,
    "Mon stock":            stock.render,
    "Mes annonces":         annonces.render,
    "Mes revenus":          pnl.render,
    "Notifications":        alertes.render,
    "Reglages":             parametres.render,
}


# ---------------------------------------------------------------------------
# Tableau de bord (Accueil)
# ---------------------------------------------------------------------------
def _render_dashboard() -> None:
    """Accueil : vue d'ensemble temps reel."""
    from datetime import datetime
    from database import Article, Annonce, Vente, Lot, get_session

    section_header("Accueil", "Vue d'ensemble de votre activite")

    session = get_session()
    try:
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

        # Ligne 1 : Metriques principales
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Articles en stock", len(en_stock))
        m2.metric("Ventes ce mois", len(ventes_mois))
        m3.metric("Revenus du mois", f"{ca_mois:,.0f} EUR")
        m4.metric("Gains du mois", f"{benef_mois:,.0f} EUR")

        st.divider()

        # Ligne 2 : Actions urgentes
        st.markdown("**Actions urgentes**")
        stock_mort = sum(
            1 for a in en_stock
            if a.date_reception and (now - a.date_reception).days > 30
        )
        annonces_gen = session.query(Annonce).filter_by(statut="generee").count()
        annonces_pub = sum(1 for a in all_articles if a.statut == "annonce_publiee")
        nb_lots = session.query(Lot).count()

        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Invendus longue duree", stock_mort)
        a2.metric("Annonces a publier", annonces_gen)
        a3.metric("Ventes a enregistrer", annonces_pub)
        a4.metric("Commandes en base", nb_lots)

        st.divider()

        # Ligne 3 : Activite recente
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

        # Ligne 4 : Stock par etat
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

    # Ligne 5 : Raccourcis rapides
    st.markdown("**Raccourcis**")

    def go_marketplace():
        st.session_state["sidebar_nav"] = "Trouver un lot"

    def go_annonces():
        st.session_state["sidebar_nav"] = "Mes annonces"

    def go_stock():
        st.session_state["sidebar_nav"] = "Mon stock"

    def go_pnl():
        st.session_state["sidebar_nav"] = "Mes revenus"

    r1, r2, r3, r4 = st.columns(4)
    with r1:
        st.button("Trouver un lot", on_click=go_marketplace, use_container_width=True, key="dash_marketplace")
    with r2:
        st.button("Creer une annonce", on_click=go_annonces, use_container_width=True, key="dash_annonces")
    with r3:
        st.button("Nouvelle vente", on_click=go_stock, use_container_width=True, key="dash_stock")
    with r4:
        st.button("Voir les revenus", on_click=go_pnl, use_container_width=True, key="dash_pnl")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def _render_sidebar() -> str:
    """Construit la sidebar et retourne la page selectionnee."""
    from scrapers import bstock as bstock_scraper
    from modules.parametres import get_param

    with st.sidebar:
        # Logo + tagline
        st.markdown(
            """
            <div style='padding: 18px 20px 14px;
                        border-bottom: 1px solid #1e2d47;
                        margin-bottom: 8px;'>
              <div style='font-size: 18px; font-weight: 700;
                          color: white; letter-spacing: -0.03em;
                          font-family: DM Sans, sans-serif;'>
                DeStock
              </div>
              <div style='font-size: 11px; color: #475569;
                          margin-top: 2px; font-weight: 400;'>
                Pilotage B2B
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.caption(f"Connecte en tant que **{current_user_nom()}**")

        # Indicateur d'etat B-Stock (different selon env)
        is_cloud = os.environ.get("ENVIRONMENT") == "cloud"
        if is_cloud:
            if get_param("api_bstock_email", ""):
                st.success("B-Stock : API connectee")
            else:
                st.warning("B-Stock : identifiants non configures")
        else:
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
    # Injection du CSS global en premier (avant tout rendu)
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)

    if not is_logged_in():
        login_form()
        return

    page = _render_sidebar()
    renderer = PAGES.get(page)

    main_area = st.container()
    with main_area:
        if renderer is None:
            _render_dashboard()
        else:
            renderer()


main()
