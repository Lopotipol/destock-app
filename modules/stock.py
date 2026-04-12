# -*- coding: utf-8 -*-
"""
DeStock App - modules/stock.py
Gestion du stock quotidien : inventaire, enregistrement de ventes, performance.

Trois onglets :
  1. Vue stock     : tableau filtrable avec jours en stock, metriques, actions
  2. Vente         : formulaire d'enregistrement de vente
  3. Performance   : CA, marge, vitesse de vente, tops/flops
"""

from datetime import datetime, date, timedelta

import pandas as pd
import streamlit as st

from database import Annonce, Article, Lot, Vente, get_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def importer_lot_en_stock(
    lot_id: str,
    articles: list[dict],
    url: str = "",
    enchere: float = 0,
    frais_bstock: float = 0,
    frais_supp: float = 0,
    livraison: float = 0,
    retail_total: float = 0,
    titre: str = "",
) -> tuple[int, int]:
    """
    Insere un lot et ses articles en base.
    Retourne (nb_articles_inseres, nb_doublons_ignores).
    """
    session = get_session()
    try:
        cout_total = round(enchere + frais_bstock + frais_supp + livraison, 2)

        row = session.query(Lot).filter_by(lot_id=lot_id).first()
        if row is None:
            row = Lot(lot_id=lot_id)
            session.add(row)
        row.url_bstock = url
        row.statut = "recu"
        row.montant_enchere = enchere
        row.frais_bstock_pct = (frais_bstock / enchere * 100) if enchere > 0 else 5.0
        row.frais_livraison = livraison
        row.tva = frais_supp
        row.cout_total = cout_total
        row.retail_total = retail_total
        row.nb_articles = len(articles)
        row.notes = titre[:500]
        row.date_reception = datetime.utcnow()

        n_ok = 0
        n_dup = 0
        for a in articles:
            lpn = a.get("lpn") or None
            if lpn and session.query(Article).filter_by(lpn=lpn).first():
                n_dup += 1
                continue
            session.add(Article(
                lot_id=lot_id,
                lpn=lpn,
                asin=a.get("asin", ""),
                ean=a.get("ean", ""),
                description=a.get("description", ""),
                condition=a.get("condition", ""),
                categorie=a.get("categorie", ""),
                sous_categorie=a.get("sous_categorie", ""),
                retail_price=float(a.get("retail_price", 0) or 0),
                cout_reel=float(a.get("cout_reel", 0) or 0),
                cout_reconditionnnement=float(a.get("frais_remise", 0) or 0),
                prix_cible=float(a.get("prix_cible_marche") or a.get("prix_cible") or 0),
                prix_amazon=float(a.get("prix_marche_amazon", 0) or 0),
                prix_lbc=float(a.get("prix_marche_lbc", 0) or 0),
                prix_ebay=float(a.get("prix_marche_ebay", 0) or 0),
                marge_estimee=float(a.get("marge_estimee", 0) or 0),
                score_roi=int(a.get("score_roi", 0) or 0),
                canal_recommande=a.get("canal_recommande_marche") or a.get("canal_recommande", ""),
                statut="en_stock",
                date_reception=datetime.utcnow(),
            ))
            n_ok += 1
        session.commit()
        return n_ok, n_dup
    finally:
        session.close()


def _load_articles(lot_filter: str = "", statut_filter: str = "",
                   condition_filter: str = "", canal_filter: str = "",
                   stock_mort_only: bool = False) -> list[dict]:
    """Charge les articles depuis la base avec filtres optionnels."""
    session = get_session()
    try:
        q = session.query(Article)
        if lot_filter:
            q = q.filter(Article.lot_id == lot_filter)
        if statut_filter:
            q = q.filter(Article.statut == statut_filter)
        if condition_filter:
            q = q.filter(Article.condition.ilike(f"%{condition_filter}%"))
        if canal_filter:
            q = q.filter(Article.canal_recommande == canal_filter)

        rows = q.all()
        result = []
        now = datetime.utcnow()
        for r in rows:
            jours = (now - r.date_reception).days if r.date_reception else 0
            if stock_mort_only and (r.statut != "en_stock" or jours <= 30):
                continue
            result.append({
                "id": r.id,
                "lot_id": r.lot_id,
                "lpn": r.lpn or "",
                "asin": r.asin or "",
                "description": r.description or "",
                "condition": r.condition or "",
                "categorie": r.categorie or "",
                "retail_price": r.retail_price,
                "cout_reel": r.cout_reel,
                "frais_remise": r.cout_reconditionnnement,
                "prix_cible": r.prix_cible,
                "marge_estimee": r.marge_estimee,
                "score_roi": r.score_roi,
                "canal_recommande": r.canal_recommande or "",
                "statut": r.statut or "en_stock",
                "jours_en_stock": jours,
                "date_reception": r.date_reception,
            })
        return result
    finally:
        session.close()


def _calc_benefice_reel() -> float:
    """Somme des benefices reels des articles vendus (prix_vente - cout_reel)."""
    session = get_session()
    try:
        ventes = session.query(Vente).all()
        total = 0.0
        for v in ventes:
            art = session.query(Article).filter_by(id=v.article_id).first()
            if art:
                cout = (art.cout_reel or 0) + (art.cout_reconditionnnement or 0)
                total += (v.prix_vente or 0) - cout
        return round(total, 2)
    finally:
        session.close()


def _load_lots_ids() -> list[str]:
    session = get_session()
    try:
        return [r.lot_id for r in session.query(Lot.lot_id).all()]
    finally:
        session.close()


def _load_ventes_mois() -> list[dict]:
    session = get_session()
    try:
        debut_mois = datetime(datetime.utcnow().year, datetime.utcnow().month, 1)
        rows = session.query(Vente).filter(Vente.date_vente >= debut_mois).all()
        return [{"article_id": v.article_id, "canal": v.canal,
                 "prix_vente": v.prix_vente, "date_vente": v.date_vente} for v in rows]
    finally:
        session.close()


def _enregistrer_vente(article_id: int, canal: str, prix_vente: float,
                       date_v: date, frais_pct: float) -> str:
    """Enregistre une vente et met a jour le statut de l'article."""
    session = get_session()
    try:
        art = session.query(Article).filter_by(id=article_id).first()
        if art is None:
            return "Article introuvable."
        if art.statut == "vendu":
            return "Article deja marque comme vendu."
        prix_net = round(prix_vente * (1 - frais_pct / 100), 2)
        cout_total = (art.cout_reel or 0) + (art.cout_reconditionnnement or 0)
        benef = round(prix_net - cout_total, 2)
        marge = round(benef / cout_total * 100, 2) if cout_total > 0 else 0

        art.statut = "vendu"
        art.prix_cible = prix_vente
        art.marge_reelle = marge

        session.add(Vente(
            article_id=article_id,
            canal=canal,
            prix_vente=prix_vente,
            date_vente=datetime(date_v.year, date_v.month, date_v.day),
        ))
        session.commit()
        return f"Vente enregistree : {prix_net:.2f} EUR net, benefice {benef:.2f} EUR ({marge:.1f}%)"
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Check doublons (articles vendus avec annonces encore actives)
# ---------------------------------------------------------------------------
def _check_doublons() -> list[dict]:
    """Detecte les articles vendus dans les 24h qui ont des annonces actives."""
    session = get_session()
    try:
        hier = datetime.utcnow() - timedelta(hours=24)
        ventes_recentes = session.query(Vente).filter(Vente.date_vente >= hier).all()
        doublons = []
        for v in ventes_recentes:
            annonces = (
                session.query(Annonce)
                .filter(Annonce.article_id == v.article_id)
                .filter(Annonce.statut.in_(["generee", "publiee"]))
                .all()
            )
            if annonces:
                art = session.query(Article).filter_by(id=v.article_id).first()
                for a in annonces:
                    doublons.append({
                        "article_desc": (art.description or "")[:50] if art else "",
                        "canal": a.canal or "",
                        "lien": a.lien or "",
                        "annonce_id": a.id,
                    })
        return doublons
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Modifier un article
# ---------------------------------------------------------------------------
def _render_modifier_article(articles: list[dict]) -> None:
    """Section pour modifier le prix, canal, statut d'un article."""
    st.divider()
    st.markdown("**Modifier un article**")
    arts_modifiables = [a for a in articles if a["statut"] in ("en_stock", "annonce_publiee")]
    if not arts_modifiables:
        return
    labels = [f"#{a['id']} — {(a['description'] or '')[:40]}" for a in arts_modifiables]
    choix = st.selectbox("Article a modifier", ["-"] + labels, key="stk_mod_select")
    if choix == "-":
        return
    art = arts_modifiables[labels.index(choix)]
    st.caption(f"Prix actuel : {art['prix_cible']:,.0f} EUR | Etat : {art['condition']} | Canal : {art['canal_recommande']}")
    with st.form(f"stk_mod_form_{art['id']}"):
        mc1, mc2 = st.columns(2)
        new_prix = mc1.number_input("Prix cible (EUR)", 0.0, step=5.0, value=float(art["prix_cible"]))
        new_canal = mc2.selectbox("Canal", ["LBC", "Vinted", "eBay", "Autre"],
                                   index=["LBC", "Vinted", "eBay", "Autre"].index(art["canal_recommande"])
                                   if art["canal_recommande"] in ["LBC", "Vinted", "eBay", "Autre"] else 0)
        mc3, mc4 = st.columns(2)
        new_statut = mc3.selectbox("Statut", ["en_stock", "annonce_publiee", "vendu", "retire"],
                                    index=["en_stock", "annonce_publiee", "vendu", "retire"].index(art["statut"])
                                    if art["statut"] in ["en_stock", "annonce_publiee", "vendu", "retire"] else 0)
        new_notes = mc4.text_input("Notes", value="")
        if st.form_submit_button("Sauvegarder", use_container_width=True):
            session = get_session()
            try:
                row = session.query(Article).filter_by(id=art["id"]).first()
                if row:
                    row.prix_cible = new_prix
                    row.canal_recommande = new_canal
                    row.statut = new_statut
                    if new_notes:
                        row.notes = new_notes
                    session.commit()
                    st.success(f"Article #{art['id']} mis a jour.")
                    st.rerun()
            finally:
                session.close()


# =========================================================================
# ONGLET 1 — Vue stock
# =========================================================================
def _tab_vue_stock() -> None:
    # Alerte doublons (articles vendus avec annonces actives)
    doublons = _check_doublons()
    if doublons:
        for d in doublons:
            msg = f"Article vendu — retirez l'annonce {d['canal']} : {d['article_desc']}"
            if d.get("lien"):
                st.warning(f"{msg} [Voir annonce]({d['lien']})")
            else:
                st.warning(msg)

    # Metriques haut de page
    all_articles = _load_articles()
    en_stock = [a for a in all_articles if a["statut"] == "en_stock"]
    vendus_mois = _load_ventes_mois()
    ca_mois = sum(v["prix_vente"] for v in vendus_mois)
    stock_mort = [a for a in en_stock if a["jours_en_stock"] > 30]

    # Benefice reel = somme des (prix_vente - cout_reel) pour les vendus
    benef_reel = _calc_benefice_reel()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("En stock", len(en_stock))
    c2.metric("Vendus ce mois", len(vendus_mois))
    c3.metric("CA encaisse (mois)", f"{ca_mois:,.0f} EUR")
    c4.metric("Benefice reel", f"{benef_reel:,.0f} EUR")
    c5.metric("Stock mort (+30j)", len(stock_mort))

    # Filtres
    with st.expander("Filtres", expanded=False):
        fc1, fc2, fc3, fc4 = st.columns(4)
        lots = _load_lots_ids()
        lot_f = fc1.selectbox("Lot", ["Tous"] + lots, key="stk_lot_f")
        statut_f = fc2.selectbox("Statut", ["Tous", "en_stock", "annonce_publiee", "vendu", "retire"], key="stk_statut_f")
        cond_f = fc3.selectbox("Etat", ["Tous", "Warehouse Damage", "Customer Damage", "Carrier Damage", "Defective"], key="stk_cond_f")
        canal_f = fc4.selectbox("Canal", ["Tous", "LBC", "Vinted", "eBay"], key="stk_canal_f")
        stock_mort_toggle = st.toggle("Stock mort uniquement (+30 jours)", key="stk_mort_toggle")

    articles = _load_articles(
        lot_filter=lot_f if lot_f != "Tous" else "",
        statut_filter=statut_f if statut_f != "Tous" else "",
        condition_filter=cond_f if cond_f != "Tous" else "",
        canal_filter=canal_f if canal_f != "Tous" else "",
        stock_mort_only=stock_mort_toggle,
    )

    if not articles:
        st.info("Aucun article en base. Importez un lot depuis la Marketplace.")
        return

    # Tableau
    rows = []
    for a in articles:
        j = a["jours_en_stock"]
        if j > 30:
            jours_txt = f"{j}j !"
        elif j > 15:
            jours_txt = f"{j}j"
        else:
            jours_txt = f"{j}j"
        rows.append({
            "ID": a["id"],
            "Description": (a["description"] or "")[:55],
            "Etat": a["condition"],
            "Prix cible": round(a["prix_cible"], 0),
            "Cout reel": round(a["cout_reel"], 0),
            "Jours stock": a["jours_en_stock"],
            "Statut": a["statut"],
            "Canal": a["canal_recommande"],
            "Lot": a["lot_id"],
        })
    df = pd.DataFrame(rows)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "ID": st.column_config.NumberColumn(format="%d", width="small"),
            "Prix cible": st.column_config.NumberColumn(format="%.0f EUR"),
            "Cout reel": st.column_config.NumberColumn(format="%.0f EUR"),
            "Jours stock": st.column_config.ProgressColumn(
                "Jours stock", min_value=0, max_value=60, format="%d",
            ),
        },
    )
    st.caption(f"{len(articles)} articles affiches.")

    # Actions rapides
    st.divider()
    st.markdown("**Actions rapides**")
    arts_en_stock = [a for a in articles if a["statut"] == "en_stock"]
    if arts_en_stock:
        labels = [f"#{a['id']} — {(a['description'] or '')[:40]}" for a in arts_en_stock]
        choix = st.selectbox("Selectionner un article", ["-"] + labels, key="stk_art_select")
        if choix != "-":
            idx = labels.index(choix)
            art = arts_en_stock[idx]
            col_v, col_b = st.columns(2)
            with col_v:
                if st.button("Marquer vendu", use_container_width=True, type="primary", key="stk_marquer_vendu"):
                    st.session_state["stk_vente_article_id"] = art["id"]
                    st.info("Remplis le formulaire dans l'onglet 'Enregistrer une vente'.")
            with col_b:
                if art["jours_en_stock"] > 30:
                    if st.button("Baisser prix (-20%)", use_container_width=True, key="stk_baisser"):
                        session = get_session()
                        try:
                            row = session.query(Article).filter_by(id=art["id"]).first()
                            if row:
                                row.prix_cible = round(row.prix_cible * 0.8, 2)
                                session.commit()
                                st.success(f"Prix cible baisse a {row.prix_cible:.2f} EUR.")
                                st.rerun()
                        finally:
                            session.close()

    # Section modifier un article
    _render_modifier_article(articles)


# =========================================================================
# ONGLET 2 — Enregistrer une vente
# =========================================================================
def _tab_vente() -> None:
    st.markdown("**Enregistrer une vente**")

    # Charge les articles en stock
    articles = _load_articles(statut_filter="en_stock")
    if not articles:
        st.info("Aucun article en stock. Importez un lot d'abord.")
        return

    # Pre-selection si vient de l'onglet Vue stock
    pre_id = st.session_state.get("stk_vente_article_id")

    labels = [f"#{a['id']} — {(a['description'] or '')[:50]} ({a['condition']})" for a in articles]
    default_idx = 0
    if pre_id:
        for i, a in enumerate(articles):
            if a["id"] == pre_id:
                default_idx = i + 1
                break

    with st.form("form_vente", clear_on_submit=True):
        choix = st.selectbox("Article", ["-"] + labels, index=default_idx)

        col1, col2 = st.columns(2)
        canal = col1.selectbox("Canal de vente", ["LBC", "Vinted", "eBay", "Autre"])
        prix = col2.number_input("Prix encaisse (EUR)", min_value=0.0, step=5.0, value=0.0)

        col3, col4 = st.columns(2)
        date_v = col3.date_input("Date de vente", value=date.today())
        frais_map = {"LBC": 0.0, "Vinted": 5.0, "eBay": 10.0, "Autre": 0.0}
        frais_pct = col4.number_input(
            "Frais plateforme %",
            min_value=0.0, max_value=50.0, step=0.5,
            value=frais_map.get(canal, 0.0),
        )

        # Calcul du benefice en temps reel
        if choix != "-" and prix > 0:
            art = articles[labels.index(choix)]
            prix_net = round(prix * (1 - frais_pct / 100), 2)
            cout_t = art["cout_reel"] + art.get("frais_remise", 0)
            benef = round(prix_net - cout_t, 2)
            marge = round(benef / cout_t * 100, 1) if cout_t > 0 else 0
            st.info(
                f"Prix net (apres frais) : **{prix_net:.2f} EUR** | "
                f"Cout reel : **{cout_t:.2f} EUR** | "
                f"Benefice : **{benef:.2f} EUR** ({marge:.1f}%)"
            )

        submitted = st.form_submit_button("Confirmer la vente", use_container_width=True)

    if submitted:
        if choix == "-":
            st.warning("Selectionne un article.")
        elif prix <= 0:
            st.warning("Saisis le prix encaisse.")
        else:
            art = articles[labels.index(choix)]
            msg = _enregistrer_vente(art["id"], canal, prix, date_v, frais_pct)
            if "enregistree" in msg:
                st.success(msg)
                st.session_state.pop("stk_vente_article_id", None)
                st.rerun()
            else:
                st.error(msg)


# =========================================================================
# ONGLET 3 — Performance
# =========================================================================
def _tab_performance() -> None:
    session = get_session()
    try:
        ventes = session.query(Vente).all()
        articles_vendus = []
        for v in ventes:
            art = session.query(Article).filter_by(id=v.article_id).first()
            if art:
                cout_t = (art.cout_reel or 0) + (art.cout_reconditionnnement or 0)
                jours = 0
                if art.date_reception and v.date_vente:
                    jours = (v.date_vente - art.date_reception).days
                articles_vendus.append({
                    "description": art.description or "",
                    "canal": v.canal,
                    "prix_vente": v.prix_vente,
                    "cout_total": cout_t,
                    "benef": round(v.prix_vente - cout_t, 2),
                    "jours": max(0, jours),
                    "date_vente": v.date_vente,
                })
    finally:
        session.close()

    if not articles_vendus:
        st.info("Aucune vente enregistree. Utilisez l'onglet 'Enregistrer une vente'.")
        return

    ca = sum(a["prix_vente"] for a in articles_vendus)
    benef_total = sum(a["benef"] for a in articles_vendus)
    # Marge moyenne : filtre les articles avec cout aberrant (< 5 EUR)
    marges_valides = [
        min(round(a["benef"] / a["cout_total"] * 100, 1), 500.0)
        for a in articles_vendus
        if a["cout_total"] > 5
    ]
    marge_moy = round(sum(marges_valides) / len(marges_valides), 1) if marges_valides else 0
    jours_moy = round(sum(a["jours"] for a in articles_vendus) / len(articles_vendus), 1)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("CA total encaisse", f"{ca:,.0f} EUR")
    c2.metric("Benefice net total", f"{benef_total:,.0f} EUR")
    c3.metric("Marge moyenne", f"{marge_moy:.1f}%")
    c4.metric("Nb ventes", len(articles_vendus))
    c5.metric("Vitesse moy. (jours)", f"{jours_moy:.0f}j")

    # Meilleurs articles
    by_benef = sorted(articles_vendus, key=lambda a: a["benef"], reverse=True)
    st.markdown("**Meilleurs articles vendus**")
    for a in by_benef[:5]:
        st.caption(
            f"- {a['description'][:50]} — Benef: **{a['benef']:,.0f} EUR** "
            f"via {a['canal']} en {a['jours']}j"
        )

    # Plus lents
    by_jours = sorted(articles_vendus, key=lambda a: a["jours"], reverse=True)
    st.markdown("**Articles les plus lents a vendre**")
    for a in by_jours[:5]:
        st.caption(
            f"- {a['description'][:50]} — **{a['jours']}j** "
            f"pour {a['prix_vente']:,.0f} EUR via {a['canal']}"
        )


# =========================================================================
# Entree principale
# =========================================================================
def render() -> None:
    st.title("Stock & Articles")
    tab_stock, tab_vente, tab_perf = st.tabs(["Vue stock", "Enregistrer une vente", "Performance"])
    with tab_stock:
        _tab_vue_stock()
    with tab_vente:
        _tab_vente()
    with tab_perf:
        _tab_performance()
