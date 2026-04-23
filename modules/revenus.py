# -*- coding: utf-8 -*-
"""
DeStock App - modules/revenus.py
Vue financiere globale : investi, CA, benefice, par lot, detail ventes.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from database import Article, Lot, Vente, get_session


def _load_all():
    session = get_session()
    try:
        lots = session.query(Lot).all()
        articles = session.query(Article).all()
        ventes = session.query(Vente).all()
        # Map article_id -> article dict
        art_map = {a.id: a for a in articles}
        ventes_data = []
        for v in ventes:
            art = art_map.get(v.article_id)
            ventes_data.append({
                "date_vente": v.date_vente,
                "description": (art.description or "")[:60] if art else "?",
                "lot_id": art.lot_id if art else "",
                "prix_vente": v.prix_vente or 0,
                "canal": v.canal or "",
                "commission_pct": v.commission_pct or 0,
                "commission_montant": v.commission_montant or 0,
                "frais_supp": v.frais_supplementaires or 0,
                "benefice_net": v.benefice_net or 0,
                "cout_reel": (art.cout_reel or 0) if art else 0,
            })
        lots_data = [
            {
                "lot_id": l.lot_id,
                "nom": l.notes or l.lot_id,
                "cout_total": l.cout_total or 0,
            }
            for l in lots
        ]
        articles_data = [
            {
                "id": a.id,
                "lot_id": a.lot_id,
                "statut": a.statut or "",
                "prix_affiche": a.prix_affiche if a.prix_affiche and a.prix_affiche > 0 else (a.prix_cible or 0),
                "cout_reel": a.cout_reel or 0,
            }
            for a in articles
        ]
        return lots_data, articles_data, ventes_data
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Section A — Vue globale
# ---------------------------------------------------------------------------
def _section_globale(lots, articles, ventes) -> None:
    st.markdown("### Vue globale")

    total_investi = sum(l["cout_total"] for l in lots)
    ca_encaisse = sum(v["prix_vente"] for v in ventes)
    commissions_frais = sum(v["commission_montant"] + v["frais_supp"] for v in ventes)
    benefice_net = ca_encaisse - commissions_frais - total_investi

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total investi", f"{total_investi:,.0f} EUR")
    m2.metric("CA encaisse", f"{ca_encaisse:,.0f} EUR")
    m3.metric("Commissions et frais", f"{commissions_frais:,.0f} EUR")
    color = "normal" if benefice_net >= 0 else "inverse"
    m4.metric("BENEFICE NET", f"{benefice_net:,.0f} EUR", delta_color=color)

    # Progression ventes
    nb_vendus = sum(1 for a in articles if a["statut"] == "vendu")
    nb_total = len(articles)
    if nb_total > 0:
        st.progress(
            nb_vendus / nb_total,
            text=f"{nb_vendus} / {nb_total} articles vendus ({nb_vendus/nb_total*100:.0f}%)",
        )

    # Projection si tout vendu
    en_stock = [a for a in articles if a["statut"] != "vendu"]
    ca_potentiel = sum(a["prix_affiche"] for a in en_stock)
    restant_a_amortir = max(0, total_investi - ca_encaisse)
    benefice_potentiel = ca_potentiel + ca_encaisse - total_investi - commissions_frais

    p1, p2, p3 = st.columns(3)
    p1.metric("CA potentiel restant", f"{ca_potentiel:,.0f} EUR")
    p2.metric("Reste a amortir", f"{restant_a_amortir:,.0f} EUR")
    color_p = "normal" if benefice_potentiel >= 0 else "inverse"
    p3.metric("Benefice potentiel si tout vendu", f"{benefice_potentiel:,.0f} EUR", delta_color=color_p)


# ---------------------------------------------------------------------------
# Section B — Par lot
# ---------------------------------------------------------------------------
def _section_par_lot(lots, articles, ventes) -> None:
    st.markdown("### Par lot")
    if not lots:
        st.info("Aucun lot en base.")
        return
    rows = []
    for l in lots:
        arts_lot = [a for a in articles if a["lot_id"] == l["lot_id"]]
        ids_lot = {a["id"] for a in arts_lot}
        ventes_lot = [v for v in ventes if any(True for a in arts_lot)]  # filter below
        # Filtre les ventes dont l'article appartient au lot
        ventes_lot = [v for v in ventes if v.get("lot_id") == l["lot_id"]]
        ca = sum(v["prix_vente"] for v in ventes_lot)
        comm = sum(v["commission_montant"] + v["frais_supp"] for v in ventes_lot)
        vendus = sum(1 for a in arts_lot if a["statut"] == "vendu")
        reste = len(arts_lot) - vendus
        benef = ca - comm - l["cout_total"]
        rows.append({
            "Lot": l["nom"][:35],
            "Investi": round(l["cout_total"], 0),
            "CA encaisse": round(ca, 0),
            "Commission": round(comm, 0),
            "Benefice": round(benef, 0),
            "Vendus/Total": f"{vendus}/{len(arts_lot)}",
            "Reste": reste,
        })
    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True, hide_index=True,
        column_config={
            "Investi":      st.column_config.NumberColumn(format="%.0f EUR"),
            "CA encaisse":  st.column_config.NumberColumn(format="%.0f EUR"),
            "Commission":   st.column_config.NumberColumn(format="%.0f EUR"),
            "Benefice":     st.column_config.NumberColumn(format="%.0f EUR"),
        },
    )


# ---------------------------------------------------------------------------
# Section C — Detail des ventes
# ---------------------------------------------------------------------------
def _section_ventes(ventes) -> None:
    st.markdown("### Detail des ventes")
    if not ventes:
        st.info("Aucune vente enregistree.")
        return
    # Tri date decroissante
    ventes_tries = sorted(ventes, key=lambda v: v["date_vente"] or 0, reverse=True)
    rows = []
    for v in ventes_tries:
        date_str = v["date_vente"].strftime("%d/%m/%Y") if v["date_vente"] else "-"
        rows.append({
            "Date": date_str,
            "Description": v["description"][:50],
            "Prix vendu": round(v["prix_vente"], 2),
            "Plateforme": v["canal"],
            "Commission": round(v["commission_montant"], 2),
            "Frais": round(v["frais_supp"], 2),
            "Benefice net": round(v["benefice_net"], 2),
        })
    df = pd.DataFrame(rows)

    # Totaux en bas
    totals = pd.DataFrame([{
        "Date": "TOTAL",
        "Description": f"{len(rows)} ventes",
        "Prix vendu": df["Prix vendu"].sum(),
        "Plateforme": "",
        "Commission": df["Commission"].sum(),
        "Frais": df["Frais"].sum(),
        "Benefice net": df["Benefice net"].sum(),
    }])
    df_full = pd.concat([df, totals], ignore_index=True)

    st.dataframe(
        df_full,
        use_container_width=True, hide_index=True,
        column_config={
            "Prix vendu":   st.column_config.NumberColumn(format="%.2f EUR"),
            "Commission":   st.column_config.NumberColumn(format="%.2f EUR"),
            "Frais":        st.column_config.NumberColumn(format="%.2f EUR"),
            "Benefice net": st.column_config.NumberColumn(format="%.2f EUR"),
        },
    )


# ---------------------------------------------------------------------------
# Entree principale
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("Mes revenus")
    lots, articles, ventes = _load_all()

    _section_globale(lots, articles, ventes)
    st.divider()
    _section_par_lot(lots, articles, ventes)
    st.divider()
    _section_ventes(ventes)
