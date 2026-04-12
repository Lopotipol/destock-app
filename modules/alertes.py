# -*- coding: utf-8 -*-
"""
DeStock App - modules/alertes.py
Configuration Telegram, historique alertes, monitoring automatique.

Deux onglets :
  1. Configuration : test connexion + toggles alertes automatiques
  2. Historique     : journal alertes_log
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from database import AlerteLog, Article, get_session
from modules.parametres import get_param, set_param
from scrapers import telegram_bot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_telegram_creds() -> tuple[str, str]:
    token = get_param("api_telegram_token", "")
    chat_id = get_param("api_telegram_chat_id", "")
    return token, chat_id


def _log_alerte(type_alerte: str, message: str) -> None:
    """Insere une entree dans alertes_log."""
    session = get_session()
    try:
        session.add(AlerteLog(
            type=type_alerte,
            message=message[:500],
            lu=False,
            date=datetime.utcnow(),
        ))
        session.commit()
    finally:
        session.close()


def verifier_stock_mort(seuil_jours: int = 30) -> list[dict]:
    """Retourne les articles en stock depuis plus de seuil_jours."""
    session = get_session()
    try:
        now = datetime.utcnow()
        rows = session.query(Article).filter(Article.statut == "en_stock").all()
        morts = []
        for r in rows:
            if r.date_reception:
                jours = (now - r.date_reception).days
                if jours > seuil_jours:
                    morts.append({
                        "id": r.id,
                        "description": r.description or "",
                        "prix_cible": r.prix_cible or 0,
                        "jours": jours,
                    })
        return morts
    finally:
        session.close()


def run_monitoring() -> None:
    """
    Verification automatique des alertes au chargement de l'app.
    Appelee 1x par session (garde un flag en session_state).
    Envoie les alertes Telegram si les toggles sont actifs.
    """
    if st.session_state.get("alertes_verifiees"):
        return
    st.session_state["alertes_verifiees"] = True

    token, chat_id = _get_telegram_creds()
    if not token or not chat_id:
        return

    # Alerte stock mort
    if get_param("alerte_stock_mort", "1") == "1":
        seuil = int(get_param("business_seuil_stock_mort_jours", "30") or 30)
        morts = verifier_stock_mort(seuil)
        for art in morts[:5]:
            ok = telegram_bot.alerte_stock_mort(token, chat_id, art)
            if ok:
                _log_alerte("stock_mort", f"Stock mort : {art['description'][:60]} ({art['jours']}j)")


# =========================================================================
# ONGLET 1 — Configuration
# =========================================================================
def _tab_config() -> None:
    token, chat_id = _get_telegram_creds()

    st.markdown("**Connexion Telegram**")
    if token and chat_id:
        st.success(f"Token : {token[:10]}...{token[-5:]} | Chat ID : {chat_id}")
    else:
        st.warning("Token ou Chat ID non configure. Allez dans Parametres > Connexions & API.")

    if st.button("Tester la connexion Telegram", use_container_width=True, key="alert_test"):
        if not token or not chat_id:
            st.error("Configurez d'abord le token et le chat ID.")
        else:
            ok = telegram_bot.test_connexion(token, chat_id)
            if ok:
                st.success("Message de test envoye sur Telegram.")
                _log_alerte("systeme", "Test connexion Telegram reussi")
            else:
                st.error("Echec envoi. Verifiez le token et le chat ID.")

    st.divider()

    # Toggles alertes automatiques
    st.markdown("**Alertes automatiques**")
    st.caption("Active les alertes que tu veux recevoir sur Telegram.")

    with st.form("form_alertes_toggles"):
        t1 = st.toggle("Nouveau lot detecte", value=get_param("alerte_nouveau_lot", "1") == "1")
        t2 = st.toggle("Enchere bientot fermee (< 2h)", value=get_param("alerte_enchere_urgente", "1") == "1")
        t3 = st.toggle("Article en stock > 30 jours", value=get_param("alerte_stock_mort", "1") == "1")
        t4 = st.toggle("Vente enregistree", value=get_param("alerte_vente", "1") == "1")

        if st.form_submit_button("Enregistrer", use_container_width=True):
            set_param("alerte_nouveau_lot", "1" if t1 else "0")
            set_param("alerte_enchere_urgente", "1" if t2 else "0")
            set_param("alerte_stock_mort", "1" if t3 else "0")
            set_param("alerte_vente", "1" if t4 else "0")
            st.success("Preferences d'alertes mises a jour.")

    # Bouton test rapide
    st.divider()
    if st.button("Envoyer une alerte test maintenant", use_container_width=True, key="alert_send_test"):
        if not token or not chat_id:
            st.error("Token/Chat ID manquant.")
        else:
            ok = telegram_bot.send_message(token, chat_id, "<b>Test DeStock</b>\nLes alertes fonctionnent.")
            (st.success if ok else st.error)("Alerte test " + ("envoyee." if ok else "echouee."))


# =========================================================================
# ONGLET 2 — Historique alertes
# =========================================================================
def _tab_historique() -> None:
    session = get_session()
    try:
        rows = session.query(AlerteLog).order_by(AlerteLog.date.desc()).limit(100).all()
        alertes = [
            {
                "id": r.id,
                "Type": r.type or "",
                "Message": (r.message or "")[:80],
                "Date": r.date.strftime("%d/%m %H:%M") if r.date else "",
                "Lu": "Oui" if r.lu else "Non",
            }
            for r in rows
        ]
    finally:
        session.close()

    if not alertes:
        st.info("Aucune alerte dans l'historique.")
        return

    import pandas as pd
    df = pd.DataFrame(alertes)
    st.dataframe(df.drop(columns=["id"]), use_container_width=True, hide_index=True)
    st.caption(f"{len(alertes)} alertes affichees.")

    if st.button("Marquer tout lu", use_container_width=True, key="alert_mark_read"):
        session = get_session()
        try:
            session.query(AlerteLog).filter(AlerteLog.lu == False).update({"lu": True})
            session.commit()
            st.success("Toutes les alertes marquees comme lues.")
            st.rerun()
        finally:
            session.close()


# =========================================================================
# Entree principale
# =========================================================================
def render() -> None:
    st.title("Alertes")
    tab_cfg, tab_hist = st.tabs(["Configuration", "Historique alertes"])
    with tab_cfg:
        _tab_config()
    with tab_hist:
        _tab_historique()
