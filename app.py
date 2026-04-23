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


# Modules metier (v2 simplifie : 4 ecrans + reglages)
from modules import accueil, lots, stock, revenus, parametres


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


# ---------------------------------------------------------------------------
# Design system : CSS global (fonts, couleurs, composants)
# ---------------------------------------------------------------------------
_GLOBAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700;800&family=DM+Mono:wght@400;500;600&display=swap');

html, body, [class*="css"] {
  font-family: 'DM Sans', sans-serif !important;
}
.main { background-color: #f0f2f5 !important; }
.block-container {
  padding: 24px 28px !important;
  max-width: 100% !important;
}

/* STATUS BADGES */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.badge-green  { background:#dcfce7; color:#16a34a; }
.badge-blue   { background:#dbeafe; color:#2563eb; }
.badge-orange { background:#ffedd5; color:#ea580c; }
.badge-red    { background:#fee2e2; color:#dc2626; }
.badge-gray   { background:#f1f5f9; color:#64748b; }

/* MODULE HEADER */
.module-header {
  background: white;
  border-radius: 12px;
  padding: 18px 22px;
  margin-bottom: 18px;
  border: 1px solid #e2e8f0;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.module-title {
  font-size: 20px;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.02em;
}
.module-subtitle {
  font-size: 13px;
  color: #64748b;
  margin-top: 2px;
}

/* ARTICLE CARD (stock) */
.article-card {
  background: white;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  padding: 12px 16px;
  margin-bottom: 6px;
  display: grid;
  grid-template-columns: 40px 1fr 110px 110px 110px;
  gap: 14px;
  align-items: center;
  box-shadow: 0 1px 2px rgba(0,0,0,0.03);
}
.article-card:hover { background: #fafbfc; }
.article-rank {
  font-size: 12px;
  font-weight: 700;
  color: #94a3b8;
  font-family: 'DM Mono', monospace;
}
.article-desc {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
  line-height: 1.3;
}
.article-meta {
  font-size: 11px;
  color: #64748b;
  margin-top: 2px;
}
.article-col-label {
  font-size: 10px;
  color: #94a3b8;
  margin-bottom: 2px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-weight: 600;
}
.article-col-val {
  font-size: 15px;
  font-weight: 700;
  color: #0f172a;
  font-family: 'DM Mono', monospace;
}
.article-col-val-blue { color: #2563eb; }
.article-col-val-green { color: #16a34a; }
.article-col-val-orange { color: #ea580c; }
.article-col-val-red { color: #dc2626; }

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
  font-size: 13px !important;
}
[data-testid="stDataFrame"] th {
  background: #f8fafc !important;
  color: #64748b !important;
  font-size: 11px !important;
  font-weight: 600 !important;
  text-transform: uppercase !important;
  letter-spacing: 0.06em !important;
  padding: 10px 14px !important;
}
[data-testid="stDataFrame"] td {
  padding: 10px 14px !important;
  color: #1e293b !important;
}
[data-testid="stDataFrame"] tr:hover td {
  background: #f8fafc !important;
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
    "Accueil":     accueil.render,
    "Mes lots":    lots.render,
    "Mon stock":   stock.render,
    "Mes revenus": revenus.render,
    "Reglages":    parametres.render,
}


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def _render_sidebar() -> str:
    """Construit la sidebar et retourne la page selectionnee."""
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

        page = st.radio(
            "Navigation",
            list(PAGES.keys()),
            label_visibility="collapsed",
            key="sidebar_nav",
        )

        st.divider()
        st.caption(f"Connecte : **{current_user_nom()}**")
        if st.button("Se deconnecter", use_container_width=True, key="sidebar_logout"):
            logout()

    return page


# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------
def main() -> None:
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)

    if not is_logged_in():
        login_form()
        return

    page = _render_sidebar()
    renderer = PAGES.get(page)

    main_area = st.container()
    with main_area:
        if renderer is not None:
            renderer()


main()
