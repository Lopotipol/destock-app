# -*- coding: utf-8 -*-
"""
DeStock App - modules/stock.py (v3 - Shopify-like)
UI basee sur :
  - Tabs par statut avec compteurs (Tous / A tester / Prets / En ligne / Vendus)
  - Grande barre de recherche
  - Table dataframe cliquable (une ligne selectionnable)
  - Panneau de detail sous la table (pour editer l'article selectionne)
"""

from __future__ import annotations

import re
import time
from datetime import datetime

import pandas as pd
import streamlit as st

from database import Article, Lot, Vente, get_session


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
COEFFS_ETAT_BSTOCK = {
    "Warehouse Damage": 0.65,
    "Customer Damage":  0.45,
    "Carrier Damage":   0.50,
    "Defective":        0.30,
}
COEFFS_ETAT = {
    "Neuf":            0.85,
    "Tres bon etat":   0.65,
    "Bon etat":        0.50,
    "Satisfaisant":    0.35,
    "HS":              0.15,
}
ETATS_LIST = list(COEFFS_ETAT.keys())

BSTOCK_TO_VINTED = {
    "Warehouse Damage": "Tres bon etat",
    "Customer Damage":  "Bon etat",
    "Carrier Damage":   "Bon etat",
    "Defective":        "HS",
}

COMMISSIONS = {
    "LBC":     0.0,
    "Vinted":  5.0,
    "eBay":    10.0,
    "Whatnot": 0.0,
    "Autre":   0.0,
}

STATUT_LABEL = {
    "en_stock":         "A tester",
    "en_attente_ligne": "Pret a publier",
    "publie":           "En ligne",
    "annonce_publiee":  "En ligne",
    "vendu":            "Vendu",
}


def _calc_prix_cible(retail: float, condition: str, teste_neuf: bool = False) -> float:
    if condition in COEFFS_ETAT:
        coeff = COEFFS_ETAT[condition]
    elif condition in COEFFS_ETAT_BSTOCK:
        coeff = COEFFS_ETAT_BSTOCK[condition]
    else:
        coeff = 0.45
    prix = retail * coeff
    if teste_neuf:
        prix *= 1.20
    return max(round(prix, 2), 5.0)


def _clean_desc(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\*\*", "", s).strip()


def _norm_statut(s: str) -> str:
    """Normalise annonce_publiee -> publie pour coherence."""
    if s == "annonce_publiee":
        return "publie"
    return s or "en_stock"


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


def _load_articles(lot_id: str) -> tuple[list[dict], dict]:
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
        nb_vendus = sum(1 for a in arts if _norm_statut(a.statut) == "vendu")
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
        arts_data = []
        for a in arts:
            cond_init = a.condition or "Customer Damage"
            cond_reelle = a.condition_reelle
            if not cond_reelle:
                cond_reelle = BSTOCK_TO_VINTED.get(cond_init, "Bon etat")
            arts_data.append({
                "id": a.id,
                "lpn": a.lpn or "",
                "description": _clean_desc(a.description or ""),
                "condition_initiale": cond_init,
                "condition": cond_reelle,
                "retail_price": a.retail_price or 0,
                "cout_reel": a.cout_reel or 0,
                "prix_cible": a.prix_cible or 0,
                "prix_affiche": a.prix_affiche if a.prix_affiche and a.prix_affiche > 0 else (a.prix_cible or 0),
                "teste_neuf": bool(a.teste_neuf),
                "statut": _norm_statut(a.statut),
                "notes": a.notes or "",
                "commentaire_test": a.commentaire_test or "",
                "plateformes": [p for p in (a.plateformes_publie or "").split(",") if p],
            })
        return arts_data, stats
    finally:
        session.close()


def _update_article(art_id: int, **kwargs) -> None:
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


def _delete_lot(lot_id: str) -> tuple[int, int]:
    session = get_session()
    try:
        arts = session.query(Article).filter_by(lot_id=lot_id).all()
        art_ids = [a.id for a in arts]
        n_ventes = 0
        if art_ids:
            n_ventes = session.query(Vente).filter(Vente.article_id.in_(art_ids)).delete(synchronize_session=False)
        n_arts = session.query(Article).filter_by(lot_id=lot_id).delete()
        session.query(Lot).filter_by(lot_id=lot_id).delete()
        session.commit()
        return n_arts, n_ventes
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
            article_id=art_id, canal=canal, prix_vente=prix,
            date_vente=datetime.utcnow(),
            commission_pct=commission_pct, commission_montant=commission_montant,
            frais_supplementaires=frais_supp, benefice_net=benef,
        ))
        art.statut = "vendu"
        art.marge_reelle = round(benef / cout_unitaire * 100, 2) if cout_unitaire > 0 else 0
        session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Panneau de detail d'un article (sous la table)
# ---------------------------------------------------------------------------
def _render_detail(art: dict, cout_unitaire: float) -> None:
    art_id = art["id"]

    st.markdown("---")
    st.markdown(f"### {art['description']}")
    st.caption(
        f"LPN {art['lpn']} | Retail Amazon {art['retail_price']:.0f} EUR | "
        f"Cout {art['cout_reel']:.2f} EUR"
    )

    # Etat initial (badge readonly)
    cond_init = art["condition_initiale"]
    badge_init = (
        f"<span style='background:#e2e8f0;color:#475569;padding:3px 10px;"
        f"border-radius:5px;font-weight:600;font-size:11px;'>{cond_init}</span>"
    )
    st.markdown(f"Etat manifeste : {badge_init}", unsafe_allow_html=True)

    # --- Etat constate + teste neuf ---
    c1, c2 = st.columns([2, 1])
    new_cond = c1.selectbox(
        "Etat constate apres test",
        ETATS_LIST,
        index=ETATS_LIST.index(art["condition"]) if art["condition"] in ETATS_LIST else 2,
        key=f"det_cond_{art_id}",
    )
    new_teste = c2.checkbox(
        "Bonus neuf (+20%)",
        value=art["teste_neuf"],
        key=f"det_neuf_{art_id}",
    )
    if new_cond != art["condition"] or new_teste != art["teste_neuf"]:
        new_cible = _calc_prix_cible(art["retail_price"], new_cond, new_teste)
        _update_article(art_id, condition_reelle=new_cond, teste_neuf=int(new_teste),
                        prix_cible=new_cible, prix_affiche=new_cible)
        st.rerun()

    # --- Prix ---
    p1, p2 = st.columns(2)
    p1.metric("Prix cible recommande", f"{art['prix_cible']:.0f} EUR")
    new_prix = p2.number_input(
        "Prix affiche (ce que vous mettez en ligne)",
        min_value=0.0, step=5.0,
        value=float(art["prix_affiche"]),
        key=f"det_prix_{art_id}",
    )
    if abs(new_prix - art["prix_affiche"]) > 0.01:
        _update_article(art_id, prix_affiche=new_prix)
        st.rerun()

    # --- Commentaire test ---
    new_comm = st.text_area(
        "Commentaire (ex: fonctionne parfaitement, brosse cassee, teste OK...)",
        value=art["commentaire_test"],
        key=f"det_comm_{art_id}",
        height=80,
    )
    if new_comm != art["commentaire_test"]:
        _update_article(art_id, commentaire_test=new_comm)

    # --- Workflow : statut + plateformes ---
    st.markdown("**Workflow**")
    b1, b2, b3, b4 = st.columns(4)
    is_en_attente = art["statut"] == "en_attente_ligne"
    if b1.button(
        "Retirer de la file" if is_en_attente else "Pret a publier",
        key=f"det_wait_{art_id}",
        use_container_width=True,
        type="primary" if not is_en_attente and art["statut"] == "en_stock" else "secondary",
    ):
        _update_article(art_id,
                         statut="en_stock" if is_en_attente else "en_attente_ligne")
        st.rerun()

    for col, plat in zip([b2, b3, b4], ["LBC", "Vinted", "eBay"]):
        is_on = plat in art["plateformes"]
        if col.button(
            f"{'Retirer' if is_on else 'Publier'} {plat}",
            key=f"det_plat_{plat}_{art_id}",
            use_container_width=True,
            type="primary" if is_on else "secondary",
        ):
            plats = list(art["plateformes"])
            if is_on:
                plats.remove(plat)
            else:
                plats.append(plat)
            new_statut = "publie" if plats else "en_attente_ligne"
            _update_article(art_id,
                             plateformes_publie=",".join(plats), statut=new_statut)
            st.rerun()

    # --- Actions finales ---
    st.markdown("**Actions**")
    a1, a2 = st.columns(2)
    if art["statut"] != "vendu":
        if a1.button("Marquer vendu", key=f"det_sell_{art_id}",
                     use_container_width=True, type="primary"):
            st.session_state[f"det_sell_form_{art_id}"] = True
    if a2.button("Supprimer l'article", key=f"det_del_{art_id}",
                 use_container_width=True):
        st.session_state[f"det_del_confirm_{art_id}"] = True

    # Formulaire vente
    if st.session_state.get(f"det_sell_form_{art_id}"):
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
                "Commission %", min_value=0.0, max_value=100.0, step=0.5,
                value=COMMISSIONS.get(canal, 0.0), key=f"v_comm_{art_id}",
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
            if bc1.form_submit_button("Confirmer", use_container_width=True, type="primary"):
                _enregistrer_vente(art_id, prix_vente, canal, comm_pct, frais_sup)
                st.session_state.pop(f"det_sell_form_{art_id}", None)
                st.session_state.pop("stk_selected_art_id", None)
                st.success("Vente enregistree.")
                st.rerun()
            if bc2.form_submit_button("Annuler", use_container_width=True):
                st.session_state.pop(f"det_sell_form_{art_id}", None)
                st.rerun()

    # Suppression
    if st.session_state.get(f"det_del_confirm_{art_id}"):
        st.error("Confirmer la suppression ?")
        dc1, dc2 = st.columns(2)
        if dc1.button("OUI, supprimer", key=f"det_del_ok_{art_id}", type="primary"):
            _delete_article(art_id)
            st.session_state.pop(f"det_del_confirm_{art_id}", None)
            st.session_state.pop("stk_selected_art_id", None)
            st.rerun()
        if dc2.button("Annuler", key=f"det_del_no_{art_id}"):
            st.session_state.pop(f"det_del_confirm_{art_id}", None)
            st.rerun()


# ---------------------------------------------------------------------------
# Table articles (Shopify-style)
# ---------------------------------------------------------------------------
def _render_table(articles: list[dict]) -> int | None:
    """
    Affiche la table d'articles. Retourne l'id de l'article selectionne (ou None).
    """
    if not articles:
        st.info("Aucun article dans cette categorie.")
        return None

    # Construction du df
    rows = []
    for a in articles:
        plats = "".join(p[0] for p in a["plateformes"])  # "LVE" = LBC+Vinted+eBay
        rows.append({
            "_id": a["id"],
            "Description": a["description"][:65],
            "Etat manifeste": a["condition_initiale"],
            "Etat constate": a["condition"],
            "Prix cible": round(a["prix_cible"], 0),
            "Prix affiche": round(a["prix_affiche"], 0),
            "Plateformes": plats,
            "Statut": STATUT_LABEL.get(a["statut"], a["statut"]),
        })
    df = pd.DataFrame(rows)

    event = st.dataframe(
        df.drop(columns=["_id"]),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Prix cible":   st.column_config.NumberColumn(format="%.0f EUR"),
            "Prix affiche": st.column_config.NumberColumn(format="%.0f EUR"),
        },
        key="stk_table",
    )

    if event.selection.rows:
        idx = event.selection.rows[0]
        return int(df.iloc[idx]["_id"])
    return None


# ---------------------------------------------------------------------------
# Ajouter un article manuellement
# ---------------------------------------------------------------------------
def _section_add_manual(lot_id: str) -> None:
    with st.expander("Ajouter un article manuellement", expanded=False):
        with st.form("form_add_art", clear_on_submit=True):
            desc = st.text_input("Description")
            col1, col2 = st.columns(2)
            cond = col1.selectbox("Etat", ETATS_LIST, index=2)
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
                        description=desc, condition="Customer Damage",
                        condition_reelle=cond,
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
# Zone dangereuse (suppression lot)
# ---------------------------------------------------------------------------
def _section_zone_dangereuse(lot: dict, nb_articles: int) -> None:
    with st.expander("Zone dangereuse — Supprimer ce lot", expanded=False):
        st.warning(
            f"La suppression du lot **{lot['nom']}** effacera definitivement "
            f"ses {nb_articles} articles et toutes les ventes liees."
        )
        confirm_key = f"stk_del_lot_confirm_{lot['lot_id']}"
        if st.button("Supprimer ce lot", key=f"stk_del_lot_{lot['lot_id']}",
                     use_container_width=True):
            st.session_state[confirm_key] = True
        if st.session_state.get(confirm_key):
            st.error("Cette action est irreversible.")
            dc1, dc2 = st.columns(2)
            if dc1.button(
                f"OUI, supprimer {lot['nom'][:30]}",
                key=f"stk_del_lot_ok_{lot['lot_id']}",
                type="primary", use_container_width=True,
            ):
                n_arts, n_ventes = _delete_lot(lot["lot_id"])
                st.session_state.pop(confirm_key, None)
                st.session_state.pop("lot_selectionne", None)
                st.session_state.pop("stk_selected_art_id", None)
                st.success(f"Lot supprime : {n_arts} articles et {n_ventes} ventes effaces.")
                st.rerun()
            if dc2.button("Annuler", key=f"stk_del_lot_no_{lot['lot_id']}",
                          use_container_width=True):
                st.session_state.pop(confirm_key, None)
                st.rerun()


# ---------------------------------------------------------------------------
# Entree principale
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("Mon stock")

    lots = _load_lots()
    if not lots:
        st.info("Aucun lot en base. Creez-en un dans 'Mes lots'.")
        return

    # Selecteur lot
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

    # Si l'utilisateur change de lot -> reset article selectionne
    if st.session_state.get("stk_last_lot") != lot["lot_id"]:
        st.session_state["stk_last_lot"] = lot["lot_id"]
        st.session_state.pop("stk_selected_art_id", None)

    articles, stats = _load_articles(lot["lot_id"])

    # --- Stats globales du lot ---
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("En stock", stats["en_stock"])
    m2.metric("Vendus", stats["nb_vendus"])
    m3.metric("CA encaisse", f"{stats['ca_encaisse']:,.0f} EUR")
    benef = stats["benefice"]
    m4.metric("Benefice", f"{benef:,.0f} EUR",
               delta_color="normal" if benef >= 0 else "inverse")
    st.caption(
        f"Cout total du lot : {stats['frais_total']:,.2f} EUR | "
        f"Cout unitaire : {stats['cout_unitaire']:.2f} EUR / article"
    )

    st.divider()

    # --- Recherche ---
    search = st.text_input(
        "Rechercher un article",
        placeholder="Description, LPN, ASIN...",
        key="stk_search",
        label_visibility="collapsed",
    )
    filtered_by_search = [
        a for a in articles
        if not search
        or search.lower() in a["description"].lower()
        or search.lower() in a["lpn"].lower()
    ]

    # --- Tabs par statut avec compteurs ---
    def _count(articles_list, statut):
        return sum(1 for a in articles_list if a["statut"] == statut)

    n_total = len(filtered_by_search)
    n_test = _count(filtered_by_search, "en_stock")
    n_pret = _count(filtered_by_search, "en_attente_ligne")
    n_ligne = _count(filtered_by_search, "publie")
    n_vendu = _count(filtered_by_search, "vendu")

    tab_all, tab_test, tab_pret, tab_ligne, tab_vendu = st.tabs([
        f"Tous ({n_total})",
        f"A tester ({n_test})",
        f"Prets a publier ({n_pret})",
        f"En ligne ({n_ligne})",
        f"Vendus ({n_vendu})",
    ])

    tabs_map = [
        (tab_all,   None),
        (tab_test,  "en_stock"),
        (tab_pret,  "en_attente_ligne"),
        (tab_ligne, "publie"),
        (tab_vendu, "vendu"),
    ]

    selected_id: int | None = None
    for tab, filtre in tabs_map:
        with tab:
            if filtre:
                arts_tab = [a for a in filtered_by_search if a["statut"] == filtre]
            else:
                arts_tab = filtered_by_search
            sid = _render_table(arts_tab)
            if sid is not None:
                selected_id = sid

    # --- Panneau de detail ---
    if selected_id is not None:
        st.session_state["stk_selected_art_id"] = selected_id

    art_id = st.session_state.get("stk_selected_art_id")
    if art_id:
        art = next((a for a in articles if a["id"] == art_id), None)
        if art:
            _render_detail(art, stats["cout_unitaire"])
            if st.button("Fermer le detail", key="stk_close_detail"):
                st.session_state.pop("stk_selected_art_id", None)
                st.rerun()

    st.divider()

    # --- Sections secondaires ---
    _section_add_manual(lot["lot_id"])
    _section_zone_dangereuse(lot, stats["nb_articles"])
