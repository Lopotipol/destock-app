# -*- coding: utf-8 -*-
"""
DeStock App - modules/stock.py (v2.1)
Workflow :
  1. Article recu   -> statut "en_stock"
  2. Teste / comment -> statut "en_attente_ligne"
  3. Publie sur plateformes -> statut "publie" + liste plateformes
  4. Vendu -> statut "vendu"
Recherche + vue compacte + detail au clic (expander).
"""

from __future__ import annotations

import re
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

STATUT_LABEL = {
    "en_stock":         ("#94a3b8", "A tester"),
    "en_attente_ligne": ("#f59e0b", "Pret a publier"),
    "publie":           ("#2563eb", "En ligne"),
    "annonce_publiee":  ("#2563eb", "En ligne"),
    "vendu":            ("#16a34a", "Vendu"),
}


def _calc_prix_cible(retail: float, condition: str, teste_neuf: bool = False) -> float:
    coeff = COEFFS_ETAT.get(condition, 0.45)
    prix = retail * coeff
    if teste_neuf:
        prix *= 1.20
    return max(round(prix, 2), 5.0)


def _clean_desc(s: str) -> str:
    """Retire les ** markdown, **espaces en trop."""
    if not s:
        return ""
    return re.sub(r"\*\*", "", s).strip()


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
        arts_data = []
        for a in arts:
            # condition   = etat initial du manifeste (jamais modifie apres import)
            # condition_reelle = etat constate par l'utilisateur (modifiable)
            cond_init = a.condition or "Customer Damage"
            cond_reelle = a.condition_reelle or cond_init
            arts_data.append({
                "id": a.id,
                "lpn": a.lpn or "",
                "description": _clean_desc(a.description or ""),
                "condition_initiale": cond_init,        # Manifeste (readonly)
                "condition": cond_reelle,                # Reelle (editable)
                "retail_price": a.retail_price or 0,
                "cout_reel": a.cout_reel or 0,
                "prix_cible": a.prix_cible or 0,
                "prix_affiche": a.prix_affiche if a.prix_affiche and a.prix_affiche > 0 else (a.prix_cible or 0),
                "teste_neuf": bool(a.teste_neuf),
                "statut": a.statut or "en_stock",
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
    """Supprime un lot + tous ses articles + ventes liees. Retourne (nb_arts, nb_ventes)."""
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
def _statut_badge(statut: str) -> str:
    c, label = STATUT_LABEL.get(statut, ("#64748b", statut or "-"))
    return (
        f"<span style='background:{c};color:white;padding:3px 10px;"
        f"border-radius:5px;font-weight:600;font-size:11px;'>{label}</span>"
    )


def _platform_badges(plats: list[str]) -> str:
    if not plats:
        return ""
    colors = {"LBC": "#f97316", "Vinted": "#14b8a6", "eBay": "#2563eb"}
    html = ""
    for p in plats:
        c = colors.get(p, "#64748b")
        html += (
            f"<span style='background:{c};color:white;padding:2px 7px;"
            f"border-radius:4px;font-weight:600;font-size:10px;margin-left:3px;'>{p}</span>"
        )
    return html


def _prix_color(prix_affiche: float, prix_cible: float) -> str:
    if prix_cible <= 0:
        return "#64748b"
    ratio = prix_affiche / prix_cible
    if ratio >= 1.0:
        return "#16a34a"
    if ratio >= 0.8:
        return "#ea580c"
    return "#dc2626"


# ---------------------------------------------------------------------------
# Vue detail d'un article (dans un expander)
# ---------------------------------------------------------------------------
def _render_article_detail(art: dict, cout_unitaire: float) -> None:
    art_id = art["id"]

    # En-tete infos
    st.markdown(f"**{art['description']}**")
    st.caption(
        f"LPN : {art['lpn']} | Retail Amazon : {art['retail_price']:.0f} EUR | "
        f"Cout : {art['cout_reel']:.2f} EUR"
    )

    # --- Etats : initial (manifeste) vs constate (editable) ---
    cond_init = art.get("condition_initiale", art["condition"])
    change = cond_init != art["condition"]
    badge_init = (
        f"<span style='background:#e2e8f0;color:#475569;padding:3px 10px;"
        f"border-radius:5px;font-weight:600;font-size:11px;'>{cond_init}</span>"
    )
    st.markdown(
        f"**Etat initial (manifeste)** : {badge_init}"
        + ("  —  *modifie apres test*" if change else ""),
        unsafe_allow_html=True,
    )

    c1, c2, _ = st.columns([2, 1, 1])
    new_cond = c1.selectbox(
        "Etat constate (apres test)",
        ETATS_LIST,
        index=ETATS_LIST.index(art["condition"]) if art["condition"] in ETATS_LIST else 1,
        key=f"stk_cond_{art_id}",
    )
    new_teste = c2.checkbox(
        "Teste neuf (+20%)",
        value=art["teste_neuf"],
        key=f"stk_neuf_{art_id}",
    )
    # Recalcul prix cible si etat reel ou teste change
    if new_cond != art["condition"] or new_teste != art["teste_neuf"]:
        new_cible = _calc_prix_cible(art["retail_price"], new_cond, new_teste)
        _update_article(
            art_id,
            condition_reelle=new_cond,
            teste_neuf=int(new_teste),
            prix_cible=new_cible,
            prix_affiche=new_cible,
        )
        st.rerun()

    # --- Ligne 2 : Prix cible vs prix affiche ---
    p1, p2 = st.columns(2)
    p1.metric("Prix cible recommande", f"{art['prix_cible']:.0f} EUR")
    new_prix = p2.number_input(
        "Prix affiche (LBC/Vinted/eBay)",
        min_value=0.0, step=5.0,
        value=float(art["prix_affiche"]),
        key=f"stk_prix_{art_id}",
    )
    if abs(new_prix - art["prix_affiche"]) > 0.01:
        _update_article(art_id, prix_affiche=new_prix)
        st.rerun()

    # --- Commentaire de test ---
    new_comment = st.text_area(
        "Commentaire test (ex: fonctionne parfaitement, brosse cassee...)",
        value=art["commentaire_test"],
        key=f"stk_comm_{art_id}",
        height=70,
    )
    if new_comment != art["commentaire_test"]:
        _update_article(art_id, commentaire_test=new_comment)

    # --- Statut actuel ---
    st.markdown(
        f"Statut actuel : {_statut_badge(art['statut'])} {_platform_badges(art['plateformes'])}",
        unsafe_allow_html=True,
    )

    # --- Actions workflow ---
    st.markdown("**Workflow**")
    a1, a2, a3, a4 = st.columns(4)

    # Marquer comme teste / en attente
    if a1.button(
        "Pret a publier" if art["statut"] == "en_stock" else "Retirer du pret",
        key=f"stk_wait_{art_id}",
        use_container_width=True,
    ):
        new_statut = "en_attente_ligne" if art["statut"] == "en_stock" else "en_stock"
        _update_article(art_id, statut=new_statut)
        st.rerun()

    # Toggle publication par plateforme
    for col, plat in zip([a2, a3, a4], ["LBC", "Vinted", "eBay"]):
        is_on = plat in art["plateformes"]
        label = f"Retirer {plat}" if is_on else f"Publie sur {plat}"
        if col.button(label, key=f"stk_pub_{plat}_{art_id}", use_container_width=True):
            plats = list(art["plateformes"])
            if is_on:
                plats.remove(plat)
            else:
                plats.append(plat)
            new_statut = "publie" if plats else "en_attente_ligne"
            _update_article(art_id,
                             plateformes_publie=",".join(plats),
                             statut=new_statut)
            st.rerun()

    # --- Bouton vente + suppression ---
    b1, b2 = st.columns(2)
    if art["statut"] != "vendu":
        if b1.button("Enregistrer la vente", key=f"stk_sell_{art_id}",
                     use_container_width=True, type="primary"):
            st.session_state[f"stk_sell_form_{art_id}"] = True
    if b2.button("Supprimer l'article", key=f"stk_del_{art_id}", use_container_width=True):
        st.session_state[f"stk_del_confirm_{art_id}"] = True

    # Formulaire vente
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


# ---------------------------------------------------------------------------
# Ligne compacte (vue liste)
# ---------------------------------------------------------------------------
def _render_article_compact(art: dict, cout_unitaire: float) -> None:
    """Ligne compacte dans un expander (click-to-expand)."""
    prix = art["prix_affiche"]
    desc_short = art["description"][:55]
    statut_label = STATUT_LABEL.get(art["statut"], ("", art["statut"]))[1]
    plats_str = " ".join(f"[{p}]" for p in art["plateformes"]) if art["plateformes"] else ""

    # Affiche l'etat : initial (manifeste) + constate si different
    cond_init = art.get("condition_initiale", art["condition"])
    if art["condition"] != cond_init:
        etat_txt = f"{cond_init} -> {art['condition']}"
    else:
        etat_txt = cond_init

    titre = f"{desc_short}  —  {etat_txt}  —  {prix:.0f} EUR  —  {statut_label}  {plats_str}"

    with st.expander(titre, expanded=False):
        _render_article_detail(art, cout_unitaire)


# ---------------------------------------------------------------------------
# Ajouter un article manuellement
# ---------------------------------------------------------------------------
def _section_add_manual(lot_id: str) -> None:
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

    articles, stats = _load_articles(lot["lot_id"])

    # --- Stats ---
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

    # --- Recherche + filtres ---
    fc1, fc2, fc3 = st.columns([3, 1, 1])
    search = fc1.text_input(
        "Rechercher un article (description, LPN)",
        placeholder="dreame, Nespresso, EQ6...",
        key="stk_search",
    )
    filtre_statut = fc2.selectbox(
        "Statut",
        ["Tous", "A tester", "Pret a publier", "En ligne", "Vendu"],
        key="stk_filtre_statut",
    )
    filtre_etat = fc3.selectbox(
        "Etat",
        ["Tous"] + ETATS_LIST,
        key="stk_filtre_etat",
    )

    # Applique les filtres
    filtered = []
    for a in articles:
        if search:
            q = search.lower()
            if q not in a["description"].lower() and q not in a["lpn"].lower():
                continue
        if filtre_statut != "Tous":
            label_map = {
                "A tester": "en_stock",
                "Pret a publier": "en_attente_ligne",
                "En ligne": "publie",
                "Vendu": "vendu",
            }
            target = label_map.get(filtre_statut, filtre_statut)
            # "publie" matche aussi "annonce_publiee" (legacy)
            if target == "publie" and a["statut"] not in ("publie", "annonce_publiee"):
                continue
            elif target != "publie" and a["statut"] != target:
                continue
        if filtre_etat != "Tous" and a["condition"] != filtre_etat:
            continue
        filtered.append(a)

    st.caption(f"{len(filtered)} / {len(articles)} articles affiches")

    st.divider()

    # --- Ajout manuel ---
    _section_add_manual(lot["lot_id"])

    # --- Zone dangereuse : supprimer le lot ---
    with st.expander("Zone dangereuse — Supprimer ce lot", expanded=False):
        st.warning(
            f"La suppression du lot **{lot['nom']}** effacera definitivement "
            f"ses {stats['nb_articles']} articles et toutes les ventes liees."
        )
        confirm_key = f"stk_del_lot_confirm_{lot['lot_id']}"
        if st.button(
            "Supprimer ce lot",
            key=f"stk_del_lot_{lot['lot_id']}",
            use_container_width=True,
        ):
            st.session_state[confirm_key] = True
        if st.session_state.get(confirm_key):
            st.error("Cette action est irreversible.")
            dc1, dc2 = st.columns(2)
            if dc1.button(
                f"OUI, supprimer {lot['nom'][:30]}",
                key=f"stk_del_lot_ok_{lot['lot_id']}",
                type="primary",
                use_container_width=True,
            ):
                n_arts, n_ventes = _delete_lot(lot["lot_id"])
                st.session_state.pop(confirm_key, None)
                st.session_state.pop("lot_selectionne", None)
                st.success(
                    f"Lot supprime : {n_arts} articles et {n_ventes} ventes effaces."
                )
                st.rerun()
            if dc2.button("Annuler", key=f"stk_del_lot_no_{lot['lot_id']}", use_container_width=True):
                st.session_state.pop(confirm_key, None)
                st.rerun()

    # --- Liste compacte avec expanders ---
    if not filtered:
        st.info("Aucun article ne correspond aux filtres.")
        return

    for art in filtered:
        _render_article_compact(art, stats["cout_unitaire"])
