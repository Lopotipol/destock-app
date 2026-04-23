# -*- coding: utf-8 -*-
"""
DeStock App - modules/stock.py (v2 simplifie)
Edition inline du stock d'un lot : etat reel, prix affiche, ventes.
"""

from __future__ import annotations

import time
from datetime import datetime, date

import streamlit as st

from database import Article, Lot, Vente, get_session


COEFFS_ETAT = {
    "Warehouse Damage": 0.65,
    "Customer Damage":  0.45,
    "Carrier Damage":   0.50,
    "Defective":        0.30,
}
ETATS_LIST = list(COEFFS_ETAT.keys())

COMMISSIONS = {
    "LBC":     0.0,
    "Vinted":  5.0,
    "eBay":    10.0,
    "Whatnot": 0.0,
    "Autre":   0.0,
}


def _calc_prix_cible(retail: float, condition: str, teste_neuf: bool = False) -> float:
    coeff = COEFFS_ETAT.get(condition, 0.45)
    prix = retail * coeff
    if teste_neuf:
        prix *= 1.20
    return max(round(prix, 2), 5.0)


# ---------------------------------------------------------------------------
# Helpers DB
# ---------------------------------------------------------------------------
def _load_lots() -> list[dict]:
    session = get_session()
    try:
        return [
            {"lot_id": l.lot_id, "nom": l.notes or l.lot_id, "cout_total": l.cout_total or 0}
            for l in session.query(Lot).order_by(Lot.id.desc()).all()
        ]
    finally:
        session.close()


def _load_articles_and_stats(lot_id: str) -> tuple[list[Article], dict]:
    session = get_session()
    try:
        arts = session.query(Article).filter_by(lot_id=lot_id).order_by(Article.id).all()
        ids = [a.id for a in arts]
        ventes = (
            session.query(Vente).filter(Vente.article_id.in_(ids)).all()
            if ids else []
        )
        lot = session.query(Lot).filter_by(lot_id=lot_id).first()
        ca = sum(v.prix_vente or 0 for v in ventes)
        nb_vendus = sum(1 for a in arts if a.statut == "vendu")
        frais = lot.cout_total if lot else 0
        stats = {
            "nb_articles": len(arts),
            "nb_vendus": nb_vendus,
            "en_stock": len(arts) - nb_vendus,
            "ca_encaisse": ca,
            "benefice": ca - frais,
            "frais_total": frais,
            "cout_unitaire": (frais / len(arts)) if arts else 0,
        }
        # On detache les articles pour pouvoir les utiliser hors session
        arts_data = []
        for a in arts:
            arts_data.append({
                "id": a.id,
                "lpn": a.lpn or "",
                "description": a.description or "",
                "condition": a.condition or "Customer Damage",
                "retail_price": a.retail_price or 0,
                "cout_reel": a.cout_reel or 0,
                "prix_cible": a.prix_cible or 0,
                "prix_affiche": a.prix_affiche if a.prix_affiche and a.prix_affiche > 0 else (a.prix_cible or 0),
                "teste_neuf": bool(a.teste_neuf),
                "statut": a.statut or "en_stock",
                "notes": a.notes or "",
            })
        return arts_data, stats
    finally:
        session.close()


def _update_article_field(art_id: int, **kwargs) -> None:
    session = get_session()
    try:
        row = session.query(Article).filter_by(id=art_id).first()
        if row:
            for k, v in kwargs.items():
                setattr(row, k, v)
            session.commit()
    finally:
        session.close()


def _delete_article(art_id: int) -> None:
    session = get_session()
    try:
        session.query(Vente).filter_by(article_id=art_id).delete()
        session.query(Article).filter_by(id=art_id).delete()
        session.commit()
    finally:
        session.close()


def _enregistrer_vente(art_id: int, prix: float, canal: str,
                        commission_pct: float, frais_supp: float) -> None:
    session = get_session()
    try:
        art = session.query(Article).filter_by(id=art_id).first()
        if not art:
            return
        commission_montant = round(prix * commission_pct / 100, 2)
        cout_unitaire = art.cout_reel or 0
        benef = round(prix - commission_montant - frais_supp - cout_unitaire, 2)
        session.add(Vente(
            article_id=art_id,
            canal=canal,
            prix_vente=prix,
            date_vente=datetime.utcnow(),
            commission_pct=commission_pct,
            commission_montant=commission_montant,
            frais_supplementaires=frais_supp,
            benefice_net=benef,
        ))
        art.statut = "vendu"
        art.marge_reelle = round(benef / cout_unitaire * 100, 2) if cout_unitaire > 0 else 0
        session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Helpers UI
# ---------------------------------------------------------------------------
def _prix_color_badge(prix_affiche: float, prix_cible: float) -> str:
    """Badge HTML colore selon ratio prix_affiche / prix_cible."""
    if prix_cible <= 0:
        return f"{prix_affiche:.0f} EUR"
    ratio = prix_affiche / prix_cible
    if ratio >= 1.0:
        color = "#16a34a"  # vert
    elif ratio >= 0.8:
        color = "#ea580c"  # orange
    else:
        color = "#dc2626"  # rouge
    return (
        f"<span style='background:{color};color:white;padding:3px 9px;"
        f"border-radius:5px;font-weight:600;font-size:12px;'>"
        f"{prix_affiche:.0f} EUR</span>"
    )


def _statut_badge(statut: str) -> str:
    colors = {
        "en_stock":         ("#94a3b8", "En stock"),
        "annonce_publiee":  ("#2563eb", "Publie"),
        "publie":           ("#2563eb", "Publie"),
        "vendu":            ("#16a34a", "Vendu"),
    }
    c, label = colors.get(statut, ("#64748b", statut or "-"))
    return (
        f"<span style='background:{c};color:white;padding:3px 9px;"
        f"border-radius:5px;font-weight:600;font-size:11px;'>{label}</span>"
    )


# ---------------------------------------------------------------------------
# Rendu article par article
# ---------------------------------------------------------------------------
def _render_article_row(art: dict, cout_unitaire: float) -> None:
    art_id = art["id"]
    with st.container():
        c1, c2, c3, c4, c5 = st.columns([3, 2, 1.5, 1.5, 1])

        c1.markdown(f"**{art['description'][:55]}**")
        c1.caption(f"LPN {art['lpn']} | Retail {art['retail_price']:.0f} EUR | Cout {art['cout_reel']:.2f} EUR")

        # Etat reel (editable)
        new_cond = c2.selectbox(
            "Etat",
            ETATS_LIST,
            index=ETATS_LIST.index(art["condition"]) if art["condition"] in ETATS_LIST else 1,
            key=f"stk_cond_{art_id}",
            label_visibility="collapsed",
        )
        new_teste = c2.checkbox(
            "Teste neuf (+20%)", value=art["teste_neuf"], key=f"stk_neuf_{art_id}"
        )
        if new_cond != art["condition"] or new_teste != art["teste_neuf"]:
            new_cible = _calc_prix_cible(art["retail_price"], new_cond, new_teste)
            _update_article_field(art_id, condition=new_cond, teste_neuf=int(new_teste),
                                   prix_cible=new_cible, prix_affiche=new_cible)
            st.rerun()

        # Prix affiche (editable)
        new_prix = c3.number_input(
            "Prix affiche",
            min_value=0.0, step=5.0,
            value=float(art["prix_affiche"]),
            key=f"stk_prix_{art_id}",
            label_visibility="collapsed",
        )
        if abs(new_prix - art["prix_affiche"]) > 0.01:
            _update_article_field(art_id, prix_affiche=new_prix)
            st.rerun()

        # Badge prix vs cible
        c3.markdown(_prix_color_badge(art["prix_affiche"], art["prix_cible"]), unsafe_allow_html=True)

        # Statut
        c4.markdown(_statut_badge(art["statut"]), unsafe_allow_html=True)

        # Actions
        if art["statut"] != "vendu":
            if c5.button("Vendu", key=f"stk_sell_{art_id}", use_container_width=True):
                st.session_state[f"stk_sell_form_{art_id}"] = True
        if c5.button("Suppr.", key=f"stk_del_{art_id}", use_container_width=True):
            st.session_state[f"stk_del_confirm_{art_id}"] = True

        # Formulaire vente inline
        if st.session_state.get(f"stk_sell_form_{art_id}"):
            with st.form(f"form_sell_{art_id}"):
                st.markdown("**Enregistrer la vente**")
                vc1, vc2 = st.columns(2)
                prix_vente = vc1.number_input(
                    "Prix encaisse (EUR)", min_value=0.0, step=5.0,
                    value=float(art["prix_affiche"]), key=f"v_prix_{art_id}",
                )
                canal = vc2.selectbox(
                    "Plateforme", list(COMMISSIONS.keys()), key=f"v_canal_{art_id}",
                )
                vc3, vc4 = st.columns(2)
                comm_pct = vc3.number_input(
                    "Commission %",
                    min_value=0.0, max_value=100.0, step=0.5,
                    value=COMMISSIONS.get(canal, 0.0),
                    key=f"v_comm_{art_id}",
                )
                frais_sup = vc4.number_input(
                    "Frais supp. (EUR)", min_value=0.0, step=1.0, value=0.0,
                    key=f"v_frais_{art_id}",
                )
                comm_montant = round(prix_vente * comm_pct / 100, 2)
                benef = round(prix_vente - comm_montant - frais_sup - cout_unitaire, 2)
                st.info(
                    f"Commission : {comm_montant:.2f} EUR | "
                    f"Cout : {cout_unitaire:.2f} EUR | "
                    f"**Benefice net : {benef:.2f} EUR**"
                )
                bc1, bc2 = st.columns(2)
                if bc1.form_submit_button("Confirmer la vente", use_container_width=True, type="primary"):
                    _enregistrer_vente(art_id, prix_vente, canal, comm_pct, frais_sup)
                    st.session_state.pop(f"stk_sell_form_{art_id}", None)
                    st.success("Vente enregistree.")
                    st.rerun()
                if bc2.form_submit_button("Annuler", use_container_width=True):
                    st.session_state.pop(f"stk_sell_form_{art_id}", None)
                    st.rerun()

        # Confirmation suppression
        if st.session_state.get(f"stk_del_confirm_{art_id}"):
            st.warning(f"Confirmer la suppression de '{art['description'][:40]}' ?")
            dc1, dc2 = st.columns(2)
            if dc1.button("Oui, supprimer", key=f"stk_del_ok_{art_id}"):
                _delete_article(art_id)
                st.session_state.pop(f"stk_del_confirm_{art_id}", None)
                st.rerun()
            if dc2.button("Annuler", key=f"stk_del_no_{art_id}"):
                st.session_state.pop(f"stk_del_confirm_{art_id}", None)
                st.rerun()

        st.divider()


# ---------------------------------------------------------------------------
# Ajouter un article manuellement
# ---------------------------------------------------------------------------
def _section_add_manual(lot_id: str, cout_total: float) -> None:
    with st.expander("Ajouter un article manuellement", expanded=False):
        with st.form("form_add_art"):
            desc = st.text_input("Description")
            col1, col2 = st.columns(2)
            cond = col1.selectbox("Etat", ETATS_LIST, index=1)
            retail = col2.number_input("Retail Amazon (EUR)", min_value=0.0, step=10.0, value=0.0)
            col3, col4 = st.columns(2)
            prix_aff = col3.number_input("Prix affiche (EUR)", min_value=0.0, step=5.0, value=0.0)
            notes = col4.text_input("Notes")
            if st.form_submit_button("Ajouter", use_container_width=True, type="primary"):
                if not desc:
                    st.warning("Description obligatoire.")
                    return
                lpn = f"MANUEL_{int(time.time())}"
                session = get_session()
                try:
                    session.add(Article(
                        lot_id=lot_id, lpn=lpn,
                        description=desc, condition=cond,
                        retail_price=retail, cout_reel=0.0,
                        prix_cible=_calc_prix_cible(retail, cond),
                        prix_affiche=prix_aff or _calc_prix_cible(retail, cond),
                        notes=notes,
                        statut="en_stock",
                        date_reception=datetime.utcnow(),
                    ))
                    session.commit()
                    st.success(f"Article '{desc[:40]}' ajoute.")
                    st.rerun()
                finally:
                    session.close()


# ---------------------------------------------------------------------------
# Entree principale
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("Mon stock")

    lots = _load_lots()
    if not lots:
        st.info("Aucun lot en base. Creez-en un dans 'Mes lots'.")
        return

    # Selecteur lot (avec pre-selection si vient de Mes lots)
    labels = [l["nom"] for l in lots]
    default_idx = 0
    pre_id = st.session_state.get("lot_selectionne")
    if pre_id:
        for i, l in enumerate(lots):
            if l["lot_id"] == pre_id:
                default_idx = i
                break
    choix = st.selectbox("Lot", labels, index=default_idx, key="stk_lot_select")
    lot = lots[labels.index(choix)]

    # Stats
    articles, stats = _load_articles_and_stats(lot["lot_id"])
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("En stock", stats["en_stock"])
    m2.metric("Vendus", stats["nb_vendus"])
    m3.metric("CA encaisse", f"{stats['ca_encaisse']:,.0f} EUR")
    benef = stats["benefice"]
    m4.metric("Benefice", f"{benef:,.0f} EUR", delta_color="normal" if benef >= 0 else "inverse")
    st.caption(
        f"Cout total du lot : {stats['frais_total']:,.2f} EUR | "
        f"Cout unitaire : {stats['cout_unitaire']:.2f} EUR / article"
    )

    st.divider()

    # Ajout manuel
    _section_add_manual(lot["lot_id"], stats["frais_total"])

    # Liste articles
    if not articles:
        st.info("Aucun article dans ce lot. Importez-en via 'Mes lots' ou ajoutez-en manuellement.")
        return

    for art in articles:
        _render_article_row(art, stats["cout_unitaire"])
