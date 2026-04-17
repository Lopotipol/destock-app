# -*- coding: utf-8 -*-
"""
DeStock App - modules/encheres.py
Suivi des encheres, lots achetes et livraisons.

Trois onglets :
  1. Mes lots          : tableau + actions (modifier statut, liens)
  2. Ajouter un lot    : formulaire saisie manuelle
  3. Suivi livraisons  : lots en transit, date estimee, marquer recu
"""

from __future__ import annotations

from datetime import datetime, date

import pandas as pd
import streamlit as st

from database import Article, Lot, get_session


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
STATUTS_LOT = [
    "en_surveillance",
    "remporte",
    "en_transit",
    "recu",
    "liquide",
]

BADGE_COULEUR = {
    "en_surveillance": "blue",
    "remporte":        "green",
    "en_transit":      "orange",
    "recu":            "gray",
    "liquide":         "violet",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_lots() -> list[dict]:
    session = get_session()
    try:
        rows = session.query(Lot).order_by(Lot.id.desc()).all()
        return [
            {
                "id": r.id,
                "lot_id": r.lot_id,
                "url": r.url_bstock or "",
                "statut": r.statut or "",
                "cout_total": r.cout_total or 0,
                "nb_articles": r.nb_articles or 0,
                "notes": r.notes or "",
                "date_reception": r.date_reception,
                "date_livraison_estimee": r.date_livraison_estimee,
            }
            for r in rows
        ]
    finally:
        session.close()


def _update_statut(lot_id: str, new_statut: str) -> None:
    session = get_session()
    try:
        row = session.query(Lot).filter_by(lot_id=lot_id).first()
        if row:
            row.statut = new_statut
            if new_statut == "recu":
                row.date_reception = datetime.utcnow()
            session.commit()
    finally:
        session.close()


def _creer_lot(
    lot_id: str, url: str, enchere: float, frais_bstock: float,
    frais_supp: float, livraison: float, statut: str,
    date_livraison_est: date | None, titre: str,
) -> str:
    """Cree un lot manuellement. Retourne message."""
    session = get_session()
    try:
        if session.query(Lot).filter_by(lot_id=lot_id).first():
            return f"Le lot {lot_id} existe deja en base."
        cout_total = round(enchere + frais_bstock + frais_supp + livraison, 2)
        dt_est = datetime(date_livraison_est.year, date_livraison_est.month, date_livraison_est.day) if date_livraison_est else None
        session.add(Lot(
            lot_id=lot_id,
            url_bstock=url,
            statut=statut,
            montant_enchere=enchere,
            frais_bstock_pct=round(frais_bstock / enchere * 100, 2) if enchere > 0 else 5,
            frais_livraison=livraison,
            tva=frais_supp,
            cout_total=cout_total,
            notes=titre[:500],
            date_livraison_estimee=dt_est,
        ))
        session.commit()
        return f"Lot {lot_id} cree (cout total {cout_total:,.2f} EUR)."
    finally:
        session.close()


# =========================================================================
# ONGLET 1 — Mes lots
# =========================================================================
def _tab_mes_lots() -> None:
    lots = _load_lots()
    if not lots:
        st.info("Aucun lot en base. Importez-en un depuis la Marketplace ou ajoutez-en un manuellement.")
        return

    st.markdown(f"**{len(lots)} lots en base**")

    rows = []
    for l in lots:
        rows.append({
            "Lot": l["lot_id"],
            "Titre": (l["notes"] or "")[:40],
            "Statut": l["statut"],
            "Cout total": round(l["cout_total"], 0),
            "Articles": l["nb_articles"],
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True, column_config={
        "Cout total": st.column_config.NumberColumn(format="%.0f EUR"),
    })

    # Actions par lot
    st.divider()
    labels = [f"{l['lot_id']} — {(l['notes'] or '')[:35]} ({l['statut']})" for l in lots]
    choix = st.selectbox("Selectionner un lot", ["-"] + labels, key="ench_lot_select")
    if choix == "-":
        return

    lot = lots[labels.index(choix)]

    c1, c2, c3 = st.columns(3)
    with c1:
        new_statut = st.selectbox("Modifier statut", STATUTS_LOT,
                                   index=STATUTS_LOT.index(lot["statut"]) if lot["statut"] in STATUTS_LOT else 0,
                                   key="ench_statut_select")
        if st.button("Appliquer", key="ench_appliquer_statut", use_container_width=True):
            _update_statut(lot["lot_id"], new_statut)
            st.success(f"Statut du lot {lot['lot_id']} mis a jour : {new_statut}")
            st.rerun()
    with c2:
        if lot["url"]:
            st.link_button("Voir sur B-Stock", lot["url"], use_container_width=True)
    with c3:
        st.caption(f"Cout : {lot['cout_total']:,.0f} EUR | {lot['nb_articles']} articles")


# =========================================================================
# ONGLET 2 — Ajouter un lot manuellement
# =========================================================================
def _tab_ajouter() -> None:
    st.markdown("**Ajouter un lot manuellement**")
    st.caption("Pour les lots non importes depuis la Marketplace.")

    with st.form("form_ajout_lot", clear_on_submit=True):
        url = st.text_input("URL B-Stock", placeholder="https://bstock.com/amazoneu/auction/.../id/XXXXX")
        # Extrait l'ID de l'URL si possible
        import re
        lot_id_default = ""
        m = re.search(r"/id/(\d+)", url)
        if m:
            lot_id_default = m.group(1)
        lot_id = st.text_input("ID du lot", value=lot_id_default)
        titre = st.text_input("Titre / description du lot")

        fc1, fc2 = st.columns(2)
        enchere = fc1.number_input("Enchere payee (EUR)", 0.0, step=50.0)
        frais_bstock = fc2.number_input("Frais B-Stock (EUR)", 0.0, step=10.0)
        fc3, fc4 = st.columns(2)
        frais_supp = fc3.number_input("Frais supplementaires (EUR)", 0.0, step=50.0)
        livraison = fc4.number_input("Frais livraison (EUR)", 0.0, step=50.0)

        cout = round(enchere + frais_bstock + frais_supp + livraison, 2)
        st.metric("COUT TOTAL", f"{cout:,.0f} EUR")

        fc5, fc6 = st.columns(2)
        statut = fc5.selectbox("Statut actuel", STATUTS_LOT, index=1)
        date_liv = fc6.date_input("Date livraison estimee", value=None)

        submitted = st.form_submit_button("Creer le lot", use_container_width=True)

    if submitted:
        if not lot_id:
            st.warning("L'ID du lot est obligatoire.")
        elif enchere <= 0:
            st.warning("Saisissez le montant de l'enchere.")
        else:
            msg = _creer_lot(lot_id, url, enchere, frais_bstock, frais_supp, livraison, statut, date_liv, titre)
            if "cree" in msg:
                st.success(msg)
            else:
                st.error(msg)


# =========================================================================
# ONGLET 3 — Suivi livraisons
# =========================================================================
def _tab_livraisons() -> None:
    lots = _load_lots()
    en_attente = [l for l in lots if l["statut"] in ("remporte", "en_transit")]

    if not en_attente:
        st.info("Aucun lot en attente de livraison.")
        return

    st.markdown(f"**{len(en_attente)} lots en attente**")

    for l in en_attente:
        with st.container():
            c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
            c1.markdown(f"**{l['lot_id']}** — {(l['notes'] or '')[:40]}")
            color = BADGE_COULEUR.get(l["statut"], "gray")
            c2.markdown(f":{color}[{l['statut']}]")
            if l.get("date_livraison_estimee"):
                dt = l["date_livraison_estimee"]
                jours_restants = (dt - datetime.utcnow()).days
                c3.caption(f"Livraison : {dt.strftime('%d/%m/%Y')}")
                if jours_restants > 0:
                    c4.caption(f"Dans {jours_restants}j")
                else:
                    c4.caption(f"En retard de {abs(jours_restants)}j")
            else:
                c3.caption("Date non definie")

            btn1, btn2 = st.columns(2)
            with btn1:
                if st.button("Marquer recu", key=f"liv_recu_{l['lot_id']}", use_container_width=True):
                    _update_statut(l["lot_id"], "recu")
                    st.success(f"Lot {l['lot_id']} marque recu. Allez dans Reception pour controler.")
                    st.rerun()
            with btn2:
                if l["url"]:
                    st.link_button("Voir B-Stock", l["url"], use_container_width=True)
            st.divider()


# =========================================================================
# Entree principale
# =========================================================================
def render() -> None:
    st.title("Mes achats")
    tab1, tab2, tab3 = st.tabs(["Mes commandes", "Ajouter manuellement", "Livraisons en cours"])
    with tab1:
        _tab_mes_lots()
    with tab2:
        _tab_ajouter()
    with tab3:
        _tab_livraisons()
