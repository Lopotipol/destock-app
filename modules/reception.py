# -*- coding: utf-8 -*-
"""
DeStock App - modules/reception.py
Reception palette : controle article par article, rapport, export PDF.

Deux onglets :
  1. Receptionner un lot  : controle un par un, navigation, resume live
  2. Rapport de reception : synthese + export PDF
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

import streamlit as st

from database import Article, Lot, get_session

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
STATUTS_RECEPTION = ["non_controle", "conforme", "different", "manquant"]

FRAIS_RECON_DEFAUT = {
    "warehouse damage":  0.0,
    "customer damage":   15.0,
    "carrier damage":    10.0,
    "defective":         35.0,
    "inutilisable":      0.0,
}

ETATS_POSSIBLES = [
    "Warehouse Damage",
    "Customer Damage",
    "Carrier Damage",
    "Defective",
    "Inutilisable",
]

CONDITION_COEFFS = {
    "warehouse damage": 0.65,
    "customer damage":  0.45,
    "carrier damage":   0.45,
    "defective":        0.30,
    "inutilisable":     0.0,
}


# ---------------------------------------------------------------------------
# Helpers DB
# ---------------------------------------------------------------------------
def _load_lots() -> list[dict]:
    session = get_session()
    try:
        rows = session.query(Lot).all()
        return [
            {
                "lot_id": r.lot_id,
                "statut": r.statut or "",
                "nb_articles": r.nb_articles or 0,
                "cout_total": r.cout_total or 0,
                "notes": r.notes or "",
            }
            for r in rows
        ]
    finally:
        session.close()


def _load_articles_lot(lot_id: str) -> list[dict]:
    session = get_session()
    try:
        rows = (
            session.query(Article)
            .filter_by(lot_id=lot_id)
            .order_by(Article.id)
            .all()
        )
        return [
            {
                "id": r.id,
                "description": r.description or "",
                "condition": r.condition or "",
                "condition_reelle": r.condition_reelle or "",
                "retail_price": r.retail_price or 0,
                "cout_reel": r.cout_reel or 0,
                "cout_recon": r.cout_reconditionnnement or 0,
                "prix_cible": r.prix_cible or 0,
                "statut": r.statut or "",
                "statut_reception": r.statut_reception or "non_controle",
                "commentaire_reception": r.commentaire_reception or "",
                "asin": r.asin or "",
                "categorie": r.categorie or "",
            }
            for r in rows
        ]
    finally:
        session.close()


def _update_article_reception(
    article_id: int,
    statut_reception: str,
    condition_reelle: str = "",
    commentaire: str = "",
    cout_recon: float = 0.0,
) -> None:
    """Met a jour un article suite au controle de reception."""
    session = get_session()
    try:
        art = session.query(Article).filter_by(id=article_id).first()
        if art is None:
            return
        art.statut_reception = statut_reception
        art.date_reception_reelle = datetime.utcnow()
        if statut_reception == "conforme":
            art.condition_reelle = art.condition
        elif statut_reception == "different":
            art.condition_reelle = condition_reelle
            art.commentaire_reception = commentaire
            art.cout_reconditionnnement = cout_recon
            # Recalcul prix_cible avec la vraie condition
            cond_key = condition_reelle.strip().lower()
            coeff = CONDITION_COEFFS.get(cond_key, 0.40)
            for k, v in CONDITION_COEFFS.items():
                if k in cond_key:
                    coeff = v
                    break
            art.prix_cible = round((art.retail_price or 0) * coeff, 2)
        elif statut_reception == "manquant":
            art.statut = "manquant"
            art.condition_reelle = "Manquant"
            art.commentaire_reception = commentaire or "Article absent de la palette"
        session.commit()
    finally:
        session.close()


def _delete_article(article_id: int) -> None:
    session = get_session()
    try:
        session.query(Article).filter_by(id=article_id).delete()
        session.commit()
    finally:
        session.close()


def _finaliser_reception(lot_id: str) -> str:
    """Finalise la reception : recalcule les marges, met a jour le lot."""
    session = get_session()
    try:
        lot = session.query(Lot).filter_by(lot_id=lot_id).first()
        if not lot:
            return "Lot introuvable."
        arts = session.query(Article).filter_by(lot_id=lot_id).all()
        recus = [a for a in arts if a.statut != "manquant"]
        manquants = [a for a in arts if a.statut == "manquant"]

        # Redistribution du cout total sur les articles recus seulement
        cout_total = lot.cout_total or 0
        retail_recus = sum(a.retail_price or 0 for a in recus) or 1.0
        for a in recus:
            ratio = (a.retail_price or 0) / retail_recus
            a.cout_reel = round(ratio * cout_total, 2)
            recon = a.cout_reconditionnnement or 0
            benef = a.prix_cible - a.cout_reel - recon
            cout_art = a.cout_reel + recon
            a.marge_estimee = round(benef / cout_art * 100, 2) if cout_art > 0 else 0

        lot.statut = "recu"
        lot.date_reception = datetime.utcnow()
        lot.nb_articles = len(recus)
        session.commit()

        val_manquants = sum(a.retail_price or 0 for a in manquants)
        return (
            f"Reception finalisee : {len(recus)} articles recus, "
            f"{len(manquants)} manquants (valeur retail {val_manquants:,.0f} EUR). "
            f"Couts recalcules."
        )
    finally:
        session.close()


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------
def _generer_pdf(lot_id: str) -> bytes:
    """Genere un rapport de reception PDF via fpdf2."""
    from fpdf import FPDF

    session = get_session()
    try:
        lot = session.query(Lot).filter_by(lot_id=lot_id).first()
        arts = session.query(Article).filter_by(lot_id=lot_id).order_by(Article.id).all()
    finally:
        session.close()

    if not lot:
        return b""

    recus = [a for a in arts if a.statut != "manquant"]
    manquants = [a for a in arts if a.statut == "manquant"]

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "DeStock - Rapport de reception", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Lot : {lot_id}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Date : {datetime.utcnow().strftime('%d/%m/%Y %H:%M')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Titre : {(lot.notes or '')[:80]}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Articles attendus : {len(arts)} | Recus : {len(recus)} | Manquants : {len(manquants)}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # Tableau articles
    pdf.set_font("Helvetica", "B", 8)
    col_w = [8, 60, 25, 25, 40, 20]
    headers = ["N", "Description", "Etat CSV", "Etat reel", "Commentaire", "Recon."]
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 7, h, border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 7)
    for idx, a in enumerate(arts, 1):
        desc = (a.description or "")[:40].encode("latin-1", "replace").decode("latin-1")
        cond = (a.condition or "")[:16].encode("latin-1", "replace").decode("latin-1")
        cond_r = (a.condition_reelle or "")[:16].encode("latin-1", "replace").decode("latin-1")
        comm = (a.commentaire_reception or "")[:28].encode("latin-1", "replace").decode("latin-1")
        recon = f"{a.cout_reconditionnnement or 0:.0f}"
        pdf.cell(col_w[0], 5, str(idx), border=1)
        pdf.cell(col_w[1], 5, desc, border=1)
        pdf.cell(col_w[2], 5, cond, border=1)
        pdf.cell(col_w[3], 5, cond_r, border=1)
        pdf.cell(col_w[4], 5, comm, border=1)
        pdf.cell(col_w[5], 5, recon, border=1)
        pdf.ln()

    # Resume financier
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Resume financier", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    cout = lot.cout_total or 0
    val_manq = sum(a.retail_price or 0 for a in manquants)
    marge_init = sum((a.prix_cible or 0) - (a.cout_reel or 0) for a in arts)
    marge_reelle = sum((a.prix_cible or 0) - (a.cout_reel or 0) - (a.cout_reconditionnnement or 0) for a in recus)
    pdf.cell(0, 7, f"Cout lot paye : {cout:,.2f} EUR", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Articles recus : {len(recus)}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Articles manquants : {len(manquants)} (retail {val_manq:,.2f} EUR)", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Marge estimee initiale : {marge_init:,.2f} EUR", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Marge reelle apres reception : {marge_reelle:,.2f} EUR", new_x="LMARGIN", new_y="NEXT")
    if val_manq > cout * 0.05:
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 7, "RECOMMANDATION : ouvrir un litige B-Stock (manquants > 5% de la valeur)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    return pdf.output()


# =========================================================================
# ONGLET 1 - Receptionner un lot
# =========================================================================
def _tab_reception() -> None:
    # Section A — Selection lot
    st.markdown("**Selectionner un lot a receptionner**")
    lots = _load_lots()
    if not lots:
        st.info("Aucun lot en base. Importez-en un depuis la Marketplace.")
        return

    labels = [f"{l['lot_id']} — {l['notes'][:40]} ({l['nb_articles']} art., {l['statut']})" for l in lots]
    choix = st.selectbox("Lot", ["-"] + labels, key="rec_lot_select")
    if choix == "-":
        return

    lot = lots[labels.index(choix)]
    lot_id = lot["lot_id"]

    c1, c2, c3 = st.columns(3)
    c1.metric("Articles attendus", lot["nb_articles"])
    c2.metric("Cout total", f"{lot['cout_total']:,.0f} EUR")
    c3.metric("Statut", lot["statut"])

    st.divider()

    # Section B — Controle article par article
    articles = _load_articles_lot(lot_id)
    if not articles:
        st.warning("Aucun article pour ce lot.")
        return

    # Filtre rapide
    filtre = st.radio(
        "Filtre",
        ["Tous", "Non controles", "Conformes", "Differents", "Manquants"],
        horizontal=True,
        key="rec_filtre",
    )
    filtre_map = {
        "Tous": None,
        "Non controles": "non_controle",
        "Conformes": "conforme",
        "Differents": "different",
        "Manquants": "manquant",
    }
    f_val = filtre_map.get(filtre)
    if f_val:
        articles_filtre = [a for a in articles if a["statut_reception"] == f_val]
    else:
        articles_filtre = articles

    if not articles_filtre:
        st.info("Aucun article dans cette categorie.")
    else:
        # Navigation
        if "rec_idx" not in st.session_state:
            st.session_state["rec_idx"] = 0
        idx = st.session_state["rec_idx"]
        idx = max(0, min(idx, len(articles_filtre) - 1))
        st.session_state["rec_idx"] = idx

        nav1, nav2, nav3 = st.columns([1, 2, 1])
        with nav1:
            if st.button("Precedent", use_container_width=True, key="rec_prev"):
                st.session_state["rec_idx"] = max(0, idx - 1)
                st.rerun()
        with nav2:
            st.markdown(f"**Article {idx + 1} / {len(articles_filtre)}**")
        with nav3:
            if st.button("Suivant", use_container_width=True, key="rec_next"):
                st.session_state["rec_idx"] = min(len(articles_filtre) - 1, idx + 1)
                st.rerun()

        art = articles_filtre[idx]

        # Barre de progression
        controles = sum(1 for a in articles if a["statut_reception"] != "non_controle")
        st.progress(controles / len(articles), text=f"{controles} / {len(articles)} controles")

        # Fiche article
        st.markdown(f"**{art['description']}**")
        fc1, fc2, fc3, fc4 = st.columns(4)
        fc1.caption(f"Etat manifeste : **{art['condition']}**")
        fc2.caption(f"Retail : **{art['retail_price']:,.0f} EUR**")
        fc3.caption(f"Prix cible : **{art['prix_cible']:,.0f} EUR**")
        fc4.caption(f"Statut : **{art['statut_reception']}**")
        if art.get("commentaire_reception"):
            st.caption(f"Commentaire : {art['commentaire_reception']}")

        # 4 boutons d'action
        ba1, ba2, ba3, ba4 = st.columns(4)
        with ba1:
            if st.button("Conforme", use_container_width=True, key=f"rec_ok_{art['id']}"):
                _update_article_reception(art["id"], "conforme")
                st.session_state["rec_idx"] = min(len(articles_filtre) - 1, idx + 1)
                st.rerun()
        with ba2:
            if st.button("Etat different", use_container_width=True, key=f"rec_diff_{art['id']}"):
                st.session_state[f"rec_show_form_{art['id']}"] = True
        with ba3:
            if st.button("Manquant", use_container_width=True, key=f"rec_manq_{art['id']}"):
                _update_article_reception(art["id"], "manquant")
                st.session_state["rec_idx"] = min(len(articles_filtre) - 1, idx + 1)
                st.rerun()
        with ba4:
            if st.button("Supprimer", use_container_width=True, key=f"rec_del_{art['id']}"):
                _delete_article(art["id"])
                st.session_state["rec_idx"] = max(0, idx - 1)
                st.rerun()

        # Formulaire etat different
        if st.session_state.get(f"rec_show_form_{art['id']}"):
            with st.form(f"form_diff_{art['id']}"):
                etat_reel = st.selectbox("Etat reel", ETATS_POSSIBLES, key=f"rec_etat_{art['id']}")
                commentaire = st.text_input(
                    "Commentaire (utilise pour les annonces)",
                    placeholder="Ex: Brosse principale cassee, aspiration OK",
                    key=f"rec_comm_{art['id']}",
                )
                defaut_recon = FRAIS_RECON_DEFAUT.get(etat_reel.strip().lower(), 15.0)
                cout_recon = st.number_input(
                    "Cout reconditionnement (EUR)",
                    min_value=0.0, step=5.0, value=defaut_recon,
                    key=f"rec_recon_{art['id']}",
                )
                if st.form_submit_button("Valider", use_container_width=True):
                    _update_article_reception(
                        art["id"], "different",
                        condition_reelle=etat_reel,
                        commentaire=commentaire,
                        cout_recon=cout_recon,
                    )
                    st.session_state.pop(f"rec_show_form_{art['id']}", None)
                    st.session_state["rec_idx"] = min(len(articles_filtre) - 1, idx + 1)
                    st.rerun()

    # Section C — Resume live
    st.divider()
    st.markdown("**Resume reception**")
    all_arts = _load_articles_lot(lot_id)
    n_conf = sum(1 for a in all_arts if a["statut_reception"] == "conforme")
    n_diff = sum(1 for a in all_arts if a["statut_reception"] == "different")
    n_manq = sum(1 for a in all_arts if a["statut_reception"] == "manquant")
    n_rest = sum(1 for a in all_arts if a["statut_reception"] == "non_controle")
    rc1, rc2, rc3, rc4 = st.columns(4)
    rc1.metric("Conformes", n_conf)
    rc2.metric("Differents", n_diff)
    rc3.metric("Manquants", n_manq)
    rc4.metric("Restants", n_rest)

    # Impact financier
    recus = [a for a in all_arts if a["statut_reception"] != "manquant" and a["statut_reception"] != "non_controle"]
    if recus:
        marge_recus = sum(a["prix_cible"] - a["cout_reel"] - a.get("cout_recon", 0) for a in recus)
        marge_init = sum(a["prix_cible"] - a["cout_reel"] for a in all_arts)
        st.caption(
            f"Marge initiale estimee : **{marge_init:,.0f} EUR** -> "
            f"Marge reelle apres reception : **{marge_recus:,.0f} EUR**"
        )

    # Finalisation
    if n_rest == 0:
        st.success("Tous les articles ont ete controles.")
        if st.button("Finaliser la reception", use_container_width=True, type="primary", key="rec_finaliser"):
            msg = _finaliser_reception(lot_id)
            st.success(msg)
            st.rerun()
    else:
        st.info(f"{n_rest} articles restent a controler.")


# =========================================================================
# ONGLET 2 - Rapport de reception
# =========================================================================
def _tab_rapport() -> None:
    lots = _load_lots()
    lots_recus = [l for l in lots if l["statut"] == "recu"]
    if not lots_recus:
        st.info("Aucun lot receptionne. Finalisez une reception dans l'onglet precedent.")
        return

    labels = [f"{l['lot_id']} — {l['notes'][:40]}" for l in lots_recus]
    choix = st.selectbox("Lot", ["-"] + labels, key="rec_rap_select")
    if choix == "-":
        return

    lot = lots_recus[labels.index(choix)]
    lot_id = lot["lot_id"]
    articles = _load_articles_lot(lot_id)

    recus = [a for a in articles if a["statut"] != "manquant"]
    manquants = [a for a in articles if a["statut"] == "manquant"]

    st.markdown(f"### Rapport — {lot_id}")
    c1, c2, c3 = st.columns(3)
    c1.metric("Articles recus", len(recus))
    c2.metric("Manquants", len(manquants))
    c3.metric("Cout total", f"{lot['cout_total']:,.0f} EUR")

    # Tableau
    import pandas as pd
    rows = []
    for a in articles:
        rows.append({
            "Description": (a["description"] or "")[:45],
            "Etat CSV": a["condition"],
            "Etat reel": a.get("condition_reelle") or "-",
            "Commentaire": a.get("commentaire_reception") or "",
            "Recon. EUR": a.get("cout_recon", 0),
            "Statut": a["statut_reception"],
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Resume financier
    val_manq = sum(a["retail_price"] for a in manquants)
    marge_reelle = sum(a["prix_cible"] - a["cout_reel"] - a.get("cout_recon", 0) for a in recus)
    st.caption(
        f"Valeur manquants (retail) : **{val_manq:,.0f} EUR** | "
        f"Marge reelle estimee : **{marge_reelle:,.0f} EUR**"
    )
    if val_manq > lot["cout_total"] * 0.05:
        st.error("Manquants > 5% du cout — recommandation : ouvrir un litige B-Stock.")

    # Export PDF
    if st.button("Exporter PDF", use_container_width=True, key="rec_pdf"):
        pdf_bytes = _generer_pdf(lot_id)
        if pdf_bytes:
            st.download_button(
                "Telecharger le rapport PDF",
                data=bytes(pdf_bytes),
                file_name=f"reception_{lot_id}.pdf",
                mime="application/pdf",
                key="rec_pdf_dl",
            )


# =========================================================================
# Entree principale
# =========================================================================
def render() -> None:
    st.title("Reception")
    tab_rec, tab_rap = st.tabs(["Receptionner un lot", "Rapport de reception"])
    with tab_rec:
        _tab_reception()
    with tab_rap:
        _tab_rapport()
