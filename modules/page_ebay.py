# -*- coding: utf-8 -*-
"""
DeStock App - modules/page_ebay.py
Page eBay : tableau de bord, publication, commandes recentes.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from database import Article, get_session
from modules.ebay_manager import (
    EbayListing,
    get_recent_orders,
    render_ebay_dashboard,
    render_ebay_publish,
)


def _articles_publies() -> list[Article]:
    """Articles deja publies sur eBay (jointure via EbayListing)."""
    s = get_session()
    try:
        ids = [r.article_id for r in s.query(EbayListing).all()]
        if not ids:
            return []
        arts = s.query(Article).filter(Article.id.in_(ids)).all()
        for a in arts:
            s.expunge(a)
        return arts
    finally:
        s.close()


def _articles_publiables() -> list[Article]:
    """Articles eligibles a publication eBay (statut != vendu)."""
    s = get_session()
    try:
        arts = (
            s.query(Article)
            .filter(Article.statut != "vendu")
            .order_by(Article.id.desc())
            .all()
        )
        for a in arts:
            s.expunge(a)
        return arts
    finally:
        s.close()


def render() -> None:
    st.markdown(
        """
        <div class='module-header'>
          <div class='module-title'>eBay</div>
          <div class='module-subtitle'>Publication, suivi des annonces et commandes</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab_dash, tab_pub, tab_cmd = st.tabs(
        ["Tableau de bord", "Publier", "Commandes"]
    )

    # -----------------------------------------------------------------------
    # Tab 1 : Tableau de bord (annonces actives + commandes recentes via API)
    # -----------------------------------------------------------------------
    with tab_dash:
        render_ebay_dashboard()

    # -----------------------------------------------------------------------
    # Tab 2 : Publier — articles deja publies + bouton publier pour les autres
    # -----------------------------------------------------------------------
    with tab_pub:
        st.markdown("### Articles deja publies sur eBay")
        publies = _articles_publies()
        if not publies:
            st.caption("Aucun article publie sur eBay pour l'instant.")
        else:
            for art in publies:
                with st.expander(
                    f"#{art.id} — {(art.description or '')[:60]} "
                    f"({art.statut or 'en_stock'})"
                ):
                    render_ebay_publish(art)

        st.divider()
        st.markdown("### Publier un nouvel article")
        publiables = _articles_publiables()
        if not publiables:
            st.caption("Aucun article disponible.")
            return

        ids_publies = {a.id for a in publies}
        a_publier = [a for a in publiables if a.id not in ids_publies]
        if not a_publier:
            st.caption("Tous les articles non vendus sont deja publies.")
            return

        labels = {
            a.id: f"#{a.id} — {(a.description or '')[:55]} "
                  f"[{a.condition_reelle or a.condition or '-'}]"
            for a in a_publier
        }
        choix = st.selectbox(
            "Choisir un article",
            list(labels.keys()),
            format_func=lambda i: labels[i],
            key="ebay_pub_pick",
        )
        article = next((a for a in a_publier if a.id == choix), None)
        if article:
            render_ebay_publish(article)

    # -----------------------------------------------------------------------
    # Tab 3 : Commandes recentes (via Fulfillment API)
    # -----------------------------------------------------------------------
    with tab_cmd:
        st.markdown("### Commandes eBay (30 derniers jours)")
        orders = get_recent_orders(days=30)
        if not orders:
            st.caption("Aucune commande recente (ou token invalide).")
            return
        rows = []
        for o in orders:
            total = o.get("pricingSummary", {}).get("total", {})
            rows.append({
                "Date": o.get("creationDate", "")[:10],
                "Order": o.get("orderId", ""),
                "Acheteur": o.get("buyer", {}).get("username", ""),
                "Statut paiement": o.get("orderPaymentStatus", ""),
                "Statut envoi": o.get("orderFulfillmentStatus", ""),
                "Montant": f"{total.get('value', '-')} {total.get('currency', '')}",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
