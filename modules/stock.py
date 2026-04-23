# -*- coding: utf-8 -*-
"""
DeStock App - modules/stock.py (v3.1 CRM-style)
Liste d'articles en cards HTML + filtres + edition inline.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

import streamlit as st

from database import Article, Lot, Vente, get_session


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
COMMISSIONS = {"LBC": 0.0, "Vinted": 5.0, "eBay": 10.0, "Whatnot": 0.0, "Autre": 0.0}

STATUT_INFO = {
    "en_stock":         ("A tester", "gray"),
    "en_attente_ligne": ("Pret a publier", "orange"),
    "publie":           ("En ligne", "blue"),
    "annonce_publiee":  ("En ligne", "blue"),
    "vendu":            ("Vendu", "green"),
}

PAGE_SIZE = 25


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------
def _calc_prix_cible(retail: float, condition: str, teste_neuf: bool = False) -> float:
    coeff = COEFFS_ETAT.get(condition) or COEFFS_ETAT_BSTOCK.get(condition) or 0.45
    prix = retail * coeff
    if teste_neuf:
        prix *= 1.20
    return max(round(prix, 2), 5.0)


def _clean_desc(s: str) -> str:
    return re.sub(r"\*\*", "", s or "").strip()


def _norm_statut(s: str) -> str:
    return "publie" if s == "annonce_publiee" else (s or "en_stock")


# ---------------------------------------------------------------------------
# DB helpers
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
        ventes = (session.query(Vente).filter(Vente.article_id.in_(ids)).all()
                  if ids else [])
        lot = session.query(Lot).filter_by(lot_id=lot_id).first()
        ca = sum(v.prix_vente or 0 for v in ventes)
        nb_vendus = sum(1 for a in arts if _norm_statut(a.statut) == "vendu")
        nb_publie = sum(1 for a in arts if _norm_statut(a.statut) == "publie")
        frais = lot.cout_total if lot else 0
        stats = {
            "nb_articles": len(arts),
            "en_stock":    len(arts) - nb_vendus,
            "nb_publie":   nb_publie,
            "nb_vendus":   nb_vendus,
            "ca_encaisse": ca,
            "benefice":    ca - frais,
            "frais_total": frais,
            "cout_unitaire": (frais / len(arts)) if arts else 0,
        }
        arts_data = []
        for idx, a in enumerate(arts, 1):
            cond_init = a.condition or "Customer Damage"
            cond_reelle = a.condition_reelle or BSTOCK_TO_VINTED.get(cond_init, "Bon etat")
            arts_data.append({
                "id": a.id,
                "rang": idx,
                "lpn": a.lpn or "",
                "asin": a.asin or "",
                "description": _clean_desc(a.description or ""),
                "condition_initiale": cond_init,
                "condition": cond_reelle,
                "retail_price": a.retail_price or 0,
                "cout_reel": a.cout_reel or 0,
                "prix_cible": a.prix_cible or 0,
                "prix_affiche": (a.prix_affiche if a.prix_affiche and a.prix_affiche > 0
                                  else (a.prix_cible or 0)),
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
        n_v = 0
        if art_ids:
            n_v = session.query(Vente).filter(Vente.article_id.in_(art_ids)).delete(synchronize_session=False)
        n_a = session.query(Article).filter_by(lot_id=lot_id).delete()
        session.query(Lot).filter_by(lot_id=lot_id).delete()
        session.commit()
        return n_a, n_v
    finally:
        session.close()


def _enregistrer_vente(art_id: int, prix: float, canal: str,
                        commission_pct: float, frais_supp: float) -> None:
    session = get_session()
    try:
        art = session.query(Article).filter_by(id=art_id).first()
        if not art:
            return
        comm = round(prix * commission_pct / 100, 2)
        cout = art.cout_reel or 0
        benef = round(prix - comm - frais_supp - cout, 2)
        session.add(Vente(
            article_id=art_id, canal=canal, prix_vente=prix,
            date_vente=datetime.utcnow(),
            commission_pct=commission_pct, commission_montant=comm,
            frais_supplementaires=frais_supp, benefice_net=benef,
        ))
        art.statut = "vendu"
        art.marge_reelle = round(benef / cout * 100, 2) if cout > 0 else 0
        session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Rendu carte article (HTML) + boutons (Streamlit)
# ---------------------------------------------------------------------------
def _prix_color_class(prix: float, cible: float) -> str:
    if cible <= 0:
        return ""
    ratio = prix / cible
    if ratio >= 1.0:
        return "article-col-val-green"
    if ratio >= 0.8:
        return "article-col-val-orange"
    return "article-col-val-red"


def _render_card(art: dict) -> None:
    art_id = art["id"]
    label, color = STATUT_INFO.get(art["statut"], ("A tester", "gray"))
    plats_html = ""
    for p in art["plateformes"]:
        plats_html += f"<span class='badge badge-blue' style='margin-left:4px;font-size:10px;'>{p}</span>"

    prix_class = _prix_color_class(art["prix_affiche"], art["prix_cible"])
    etat_init = art["condition_initiale"]
    etat_reel = art["condition"]
    etat_txt = f"{etat_init} -> {etat_reel}" if etat_init != etat_reel else etat_init
    asin_txt = f"· ASIN {art['asin']}" if art["asin"] else ""

    st.markdown(
        f"""
        <div class='article-card'>
          <div class='article-rank'>#{art['rang']:03d}</div>
          <div>
            <div class='article-desc'>{art['description'][:80]}</div>
            <div class='article-meta'>{etat_txt} {asin_txt}</div>
          </div>
          <div style='text-align:center;'>
            <div class='article-col-label'>Prix cible</div>
            <div class='article-col-val article-col-val-blue'>{art['prix_cible']:.0f} EUR</div>
          </div>
          <div style='text-align:center;'>
            <div class='article-col-label'>Prix affiche</div>
            <div class='article-col-val {prix_class}'>{art['prix_affiche']:.0f} EUR</div>
          </div>
          <div style='text-align:center;'>
            <span class='badge badge-{color}'>{label}</span>{plats_html}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Boutons sous la card
    bc1, bc2, bc3, _ = st.columns([1, 1, 1, 3])
    with bc1:
        if st.button("Modifier", key=f"art_mod_{art_id}", use_container_width=True):
            st.session_state[f"art_edit_{art_id}"] = not st.session_state.get(f"art_edit_{art_id}", False)
    if art["statut"] != "vendu":
        with bc2:
            if st.button("Vendu", key=f"art_vendu_{art_id}", use_container_width=True, type="primary"):
                st.session_state[f"art_sell_{art_id}"] = True
    with bc3:
        if st.button("Supprimer", key=f"art_del_{art_id}", use_container_width=True):
            st.session_state[f"art_del_confirm_{art_id}"] = True

    # Formulaire edit
    if st.session_state.get(f"art_edit_{art_id}"):
        _render_edit_form(art)

    # Formulaire vente
    if st.session_state.get(f"art_sell_{art_id}"):
        _render_sell_form(art)

    # Confirmation suppression
    if st.session_state.get(f"art_del_confirm_{art_id}"):
        st.error(f"Confirmer la suppression de '{art['description'][:40]}' ?")
        dc1, dc2, _ = st.columns([1, 1, 3])
        if dc1.button("OUI, supprimer", key=f"art_del_ok_{art_id}", type="primary"):
            _delete_article(art_id)
            st.session_state.pop(f"art_del_confirm_{art_id}", None)
            st.rerun()
        if dc2.button("Annuler", key=f"art_del_no_{art_id}"):
            st.session_state.pop(f"art_del_confirm_{art_id}", None)
            st.rerun()


def _render_edit_form(art: dict) -> None:
    art_id = art["id"]
    with st.container():
        st.markdown(
            "<div style='background:#f8fafc;padding:14px;border-radius:8px;"
            "border-left:3px solid #2563eb;margin:8px 0;'>",
            unsafe_allow_html=True,
        )
        st.markdown("**Modifier l'article**")
        c1, c2 = st.columns([2, 1])
        new_cond = c1.selectbox(
            "Etat constate",
            ETATS_LIST,
            index=ETATS_LIST.index(art["condition"]) if art["condition"] in ETATS_LIST else 2,
            key=f"art_edit_cond_{art_id}",
        )
        new_teste = c2.checkbox("Teste neuf (+20%)", value=art["teste_neuf"],
                                  key=f"art_edit_neuf_{art_id}")

        p1, p2 = st.columns(2)
        p1.metric("Prix cible", f"{art['prix_cible']:.0f} EUR")
        new_prix = p2.number_input(
            "Prix affiche (EUR)",
            min_value=0.0, step=5.0,
            value=float(art["prix_affiche"]),
            key=f"art_edit_prix_{art_id}",
        )

        new_comm = st.text_area(
            "Commentaire (fonctionne OK / defaut / ...)",
            value=art["commentaire_test"],
            key=f"art_edit_comm_{art_id}",
            height=70,
        )

        # Workflow : statut + plateformes
        st.markdown("**Workflow**")
        w1, w2, w3, w4 = st.columns(4)
        is_attente = art["statut"] == "en_attente_ligne"
        if w1.button(
            "Retirer de la file" if is_attente else "Pret a publier",
            key=f"art_wf_wait_{art_id}",
            use_container_width=True,
            type="primary" if not is_attente and art["statut"] == "en_stock" else "secondary",
        ):
            _update_article(art_id, statut="en_stock" if is_attente else "en_attente_ligne")
            st.rerun()

        for col, plat in zip([w2, w3, w4], ["LBC", "Vinted", "eBay"]):
            on = plat in art["plateformes"]
            if col.button(
                f"{'Retirer' if on else 'Publier'} {plat}",
                key=f"art_wf_{plat}_{art_id}",
                use_container_width=True,
                type="primary" if on else "secondary",
            ):
                plats = list(art["plateformes"])
                if on:
                    plats.remove(plat)
                else:
                    plats.append(plat)
                st_new = "publie" if plats else "en_attente_ligne"
                _update_article(art_id, plateformes_publie=",".join(plats), statut=st_new)
                st.rerun()

        # Save
        s1, s2, _ = st.columns([1, 1, 3])
        if s1.button("Sauvegarder", key=f"art_save_{art_id}",
                      type="primary", use_container_width=True):
            new_cible = _calc_prix_cible(art["retail_price"], new_cond, new_teste)
            _update_article(
                art_id,
                condition_reelle=new_cond,
                teste_neuf=int(new_teste),
                prix_cible=new_cible,
                prix_affiche=new_prix,
                commentaire_test=new_comm,
            )
            st.session_state[f"art_edit_{art_id}"] = False
            st.rerun()
        if s2.button("Fermer", key=f"art_close_{art_id}", use_container_width=True):
            st.session_state[f"art_edit_{art_id}"] = False
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)


def _render_sell_form(art: dict) -> None:
    art_id = art["id"]
    cout_unit = art["cout_reel"]
    with st.form(f"form_sell_{art_id}"):
        st.markdown(
            "<div style='background:#f0fdf4;padding:14px;border-radius:8px;"
            "border-left:3px solid #16a34a;margin:8px 0;'>",
            unsafe_allow_html=True,
        )
        st.markdown("**Enregistrer la vente**")
        vc1, vc2 = st.columns(2)
        prix = vc1.number_input("Prix encaisse (EUR)", min_value=0.0, step=5.0,
                                  value=float(art["prix_affiche"]), key=f"vf_prix_{art_id}")
        canal = vc2.selectbox("Plateforme", list(COMMISSIONS.keys()),
                                key=f"vf_canal_{art_id}")
        vc3, vc4 = st.columns(2)
        comm_pct = vc3.number_input("Commission %",
                                      min_value=0.0, max_value=100.0, step=0.5,
                                      value=COMMISSIONS[canal], key=f"vf_comm_{art_id}")
        frais = vc4.number_input("Frais supp. (EUR)", min_value=0.0, step=1.0,
                                   value=0.0, key=f"vf_frais_{art_id}")
        comm_eur = round(prix * comm_pct / 100, 2)
        benef = round(prix - comm_eur - frais - cout_unit, 2)
        benef_color = "#16a34a" if benef >= 0 else "#dc2626"
        st.markdown(
            f"<div style='display:flex;gap:16px;margin-top:8px;'>"
            f"<div><div style='font-size:10px;color:#94a3b8;'>COMMISSION</div>"
            f"<div style='font-size:14px;font-weight:700;'>{comm_eur:.2f} EUR</div></div>"
            f"<div><div style='font-size:10px;color:#94a3b8;'>COUT</div>"
            f"<div style='font-size:14px;font-weight:700;'>{cout_unit:.2f} EUR</div></div>"
            f"<div><div style='font-size:10px;color:#94a3b8;'>BENEFICE NET</div>"
            f"<div style='font-size:16px;font-weight:800;color:{benef_color};'>"
            f"{benef:.2f} EUR</div></div></div>",
            unsafe_allow_html=True,
        )
        bc1, bc2 = st.columns(2)
        if bc1.form_submit_button("Confirmer", use_container_width=True, type="primary"):
            _enregistrer_vente(art_id, prix, canal, comm_pct, frais)
            st.session_state.pop(f"art_sell_{art_id}", None)
            st.success("Vente enregistree.")
            st.rerun()
        if bc2.form_submit_button("Annuler", use_container_width=True):
            st.session_state.pop(f"art_sell_{art_id}", None)
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sections secondaires
# ---------------------------------------------------------------------------
def _section_add_manual(lot_id: str) -> None:
    with st.expander("Ajouter un article manuellement"):
        with st.form("form_add_art", clear_on_submit=True):
            desc = st.text_input("Description")
            c1, c2 = st.columns(2)
            cond = c1.selectbox("Etat", ETATS_LIST, index=2)
            retail = c2.number_input("Retail Amazon (EUR)", min_value=0.0, step=10.0)
            c3, c4 = st.columns(2)
            prix_aff = c3.number_input("Prix affiche (EUR)", min_value=0.0, step=5.0)
            notes = c4.text_input("Notes")
            if st.form_submit_button("Ajouter", use_container_width=True, type="primary"):
                if not desc:
                    st.warning("Description obligatoire.")
                    return
                session = get_session()
                try:
                    session.add(Article(
                        lot_id=lot_id, lpn=f"MANUEL_{int(time.time())}",
                        description=desc, condition="Customer Damage",
                        condition_reelle=cond,
                        retail_price=retail, cout_reel=0.0,
                        prix_cible=_calc_prix_cible(retail, cond),
                        prix_affiche=prix_aff or _calc_prix_cible(retail, cond),
                        notes=notes, statut="en_stock",
                        date_reception=datetime.utcnow(),
                    ))
                    session.commit()
                    st.success("Article ajoute.")
                    st.rerun()
                finally:
                    session.close()


def _section_zone_dangereuse(lot: dict, nb: int) -> None:
    with st.expander("Zone dangereuse — Supprimer ce lot"):
        st.warning(f"Supprimer le lot **{lot['nom']}** ({nb} articles + ventes liees).")
        ck = f"stk_del_lot_confirm_{lot['lot_id']}"
        if st.button("Supprimer ce lot", key=f"stk_del_lot_{lot['lot_id']}", use_container_width=True):
            st.session_state[ck] = True
        if st.session_state.get(ck):
            st.error("Action irreversible.")
            d1, d2 = st.columns(2)
            if d1.button(f"OUI, supprimer", key=f"stk_del_lot_ok_{lot['lot_id']}",
                         type="primary", use_container_width=True):
                n_a, n_v = _delete_lot(lot["lot_id"])
                st.session_state.pop(ck, None)
                st.session_state.pop("lot_selectionne", None)
                st.success(f"{n_a} articles + {n_v} ventes supprimes.")
                st.rerun()
            if d2.button("Annuler", key=f"stk_del_lot_no_{lot['lot_id']}", use_container_width=True):
                st.session_state.pop(ck, None)
                st.rerun()


# ---------------------------------------------------------------------------
# Entree principale
# ---------------------------------------------------------------------------
def render() -> None:
    # Header du module
    st.markdown(
        """
        <div class='module-header'>
          <div class='module-title'>Mon stock</div>
          <div class='module-subtitle'>Gestion des articles par lot : test, publication, vente</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

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

    # Reset selection si changement de lot
    if st.session_state.get("stk_last_lot") != lot["lot_id"]:
        st.session_state["stk_last_lot"] = lot["lot_id"]
        st.session_state["stk_page"] = 0

    articles, stats = _load_articles(lot["lot_id"])

    # Metriques
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("En stock", stats["en_stock"])
    m2.metric("Publies", stats["nb_publie"])
    m3.metric("Vendus", stats["nb_vendus"])
    m4.metric("CA encaisse", f"{stats['ca_encaisse']:,.0f} EUR")
    st.caption(
        f"Cout total lot : {stats['frais_total']:,.0f} EUR | "
        f"Cout unitaire : {stats['cout_unitaire']:.2f} EUR/article | "
        f"Benefice : {stats['benefice']:,.0f} EUR"
    )

    st.divider()

    # Filtres
    fc1, fc2, fc3 = st.columns([2, 1, 1])
    search = fc1.text_input("Rechercher", placeholder="Description, LPN, ASIN...",
                              key="stk_search", label_visibility="collapsed")
    filtre_statut = fc2.selectbox(
        "Statut", ["Tous", "A tester", "Pret a publier", "En ligne", "Vendu"],
        key="stk_filtre_statut",
    )
    filtre_etat = fc3.selectbox(
        "Etat", ["Tous"] + ETATS_LIST,
        key="stk_filtre_etat",
    )

    # Application filtres
    filtered = []
    statut_map = {
        "A tester": "en_stock",
        "Pret a publier": "en_attente_ligne",
        "En ligne": "publie",
        "Vendu": "vendu",
    }
    for a in articles:
        if search:
            q = search.lower()
            if q not in a["description"].lower() and q not in a["lpn"].lower() and q not in a["asin"].lower():
                continue
        if filtre_statut != "Tous" and a["statut"] != statut_map.get(filtre_statut, filtre_statut):
            continue
        if filtre_etat != "Tous" and a["condition"] != filtre_etat:
            continue
        filtered.append(a)

    # Pagination
    total = len(filtered)
    nb_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = st.session_state.get("stk_page", 0)
    page = max(0, min(page, nb_pages - 1))

    st.caption(f"{total} articles — page {page + 1} / {nb_pages}")

    if nb_pages > 1:
        pc1, pc2, pc3 = st.columns([1, 3, 1])
        if pc1.button("< Precedent", disabled=(page == 0), key="stk_prev"):
            st.session_state["stk_page"] = page - 1
            st.rerun()
        pc2.markdown(f"<div style='text-align:center;padding-top:8px;'>Page {page + 1} / {nb_pages}</div>",
                      unsafe_allow_html=True)
        if pc3.button("Suivant >", disabled=(page >= nb_pages - 1), key="stk_next"):
            st.session_state["stk_page"] = page + 1
            st.rerun()

    start = page * PAGE_SIZE
    page_arts = filtered[start:start + PAGE_SIZE]

    # Rendu des cards
    if not page_arts:
        st.info("Aucun article ne correspond aux filtres.")
    else:
        for a in page_arts:
            _render_card(a)

    st.divider()
    _section_add_manual(lot["lot_id"])
    _section_zone_dangereuse(lot, stats["nb_articles"])
