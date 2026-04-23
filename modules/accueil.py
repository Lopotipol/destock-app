# -*- coding: utf-8 -*-
"""
DeStock App - modules/accueil.py
Tableau de bord : vue d'ensemble de l'activite.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import streamlit as st

from database import Article, Lot, Vente, get_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_snapshot() -> dict:
    """Charge toutes les stats agregees en une seule passe."""
    session = get_session()
    try:
        lots = session.query(Lot).all()
        articles = session.query(Article).all()
        ventes = session.query(Vente).all()
        art_map = {a.id: a for a in articles}

        # Stats lots
        lots_avec_stock = set()
        for a in articles:
            if a.statut != "vendu":
                lots_avec_stock.add(a.lot_id)

        # Pipeline par statut
        pipeline = {
            "en_stock":         0,  # A tester
            "en_attente_ligne": 0,  # Pret a publier
            "publie":           0,  # En ligne
            "vendu":            0,
        }
        for a in articles:
            s = a.statut or "en_stock"
            if s == "annonce_publiee":
                s = "publie"
            if s in pipeline:
                pipeline[s] += 1
            else:
                pipeline["en_stock"] += 1

        # Repartition par etat constate
        etats = {}
        for a in articles:
            if a.statut == "vendu":
                continue
            e = a.condition_reelle or a.condition or "Inconnu"
            etats[e] = etats.get(e, 0) + 1

        # Financier global
        total_investi = sum(l.cout_total or 0 for l in lots)
        ca_encaisse = sum(v.prix_vente or 0 for v in ventes)
        commissions_frais = sum(
            (v.commission_montant or 0) + (v.frais_supplementaires or 0)
            for v in ventes
        )
        benefice_net = ca_encaisse - commissions_frais - total_investi

        # CA potentiel restant
        en_stock = [a for a in articles if a.statut != "vendu"]
        ca_potentiel = sum(
            (a.prix_affiche if a.prix_affiche and a.prix_affiche > 0 else (a.prix_cible or 0))
            for a in en_stock
        )

        # Stock mort (>30 jours sans vente)
        now = datetime.utcnow()
        stock_mort = 0
        for a in en_stock:
            if a.date_reception and (now - a.date_reception).days > 30:
                stock_mort += 1

        # Ventes cette semaine + ce mois
        debut_semaine = now - timedelta(days=7)
        debut_mois = datetime(now.year, now.month, 1)
        ventes_semaine = [v for v in ventes if v.date_vente and v.date_vente >= debut_semaine]
        ventes_mois = [v for v in ventes if v.date_vente and v.date_vente >= debut_mois]
        ca_semaine = sum(v.prix_vente or 0 for v in ventes_semaine)
        ca_mois = sum(v.prix_vente or 0 for v in ventes_mois)

        # Top articles en stock (par prix_affiche desc)
        def _prix_aff(a):
            return a.prix_affiche if a.prix_affiche and a.prix_affiche > 0 else (a.prix_cible or 0)
        top_stock = sorted(en_stock, key=_prix_aff, reverse=True)[:5]

        # Activite recente (5 dernieres ventes)
        ventes_recentes = sorted(
            [v for v in ventes if v.date_vente],
            key=lambda v: v.date_vente, reverse=True,
        )[:5]
        activite = []
        for v in ventes_recentes:
            art = art_map.get(v.article_id)
            activite.append({
                "date": v.date_vente,
                "description": (art.description or "?")[:45] if art else "?",
                "prix": v.prix_vente or 0,
                "canal": v.canal or "",
                "benefice": v.benefice_net or 0,
            })

        return {
            "nb_lots_total": len(lots),
            "nb_lots_actifs": len(lots_avec_stock),
            "nb_articles_total": len(articles),
            "pipeline": pipeline,
            "etats": etats,
            "total_investi": total_investi,
            "ca_encaisse": ca_encaisse,
            "commissions_frais": commissions_frais,
            "benefice_net": benefice_net,
            "ca_potentiel": ca_potentiel,
            "stock_mort": stock_mort,
            "ca_semaine": ca_semaine,
            "ca_mois": ca_mois,
            "nb_ventes_semaine": len(ventes_semaine),
            "nb_ventes_mois": len(ventes_mois),
            "top_stock": [
                {
                    "description": (a.description or "?")[:50],
                    "prix": _prix_aff(a),
                    "statut": a.statut or "en_stock",
                    "lot_id": a.lot_id,
                }
                for a in top_stock
            ],
            "activite": activite,
        }
    finally:
        session.close()


def _metric_card(label: str, value: str, color: str = "#2563eb") -> str:
    """Card HTML colore pour une metrique pipeline."""
    return (
        f"<div style='background:white;border:1px solid #e2e8f0;"
        f"border-radius:10px;padding:14px 18px;border-top:3px solid {color};"
        f"box-shadow:0 1px 3px rgba(0,0,0,0.05);height:92px;'>"
        f"<div style='font-size:10.5px;font-weight:600;color:#94a3b8;"
        f"text-transform:uppercase;letter-spacing:0.06em;'>{label}</div>"
        f"<div style='font-size:26px;font-weight:700;color:#0f172a;"
        f"font-family:DM Mono,monospace;margin-top:6px;'>{value}</div>"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Entree principale
# ---------------------------------------------------------------------------
def render() -> None:
    st.markdown(
        """
        <div class='module-header'>
          <div class='module-title'>Tableau de bord</div>
          <div class='module-subtitle'>Vue d'ensemble de votre activite en temps reel</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    snap = _load_snapshot()

    # =====================================================================
    # LIGNE 1 : VOLUME GLOBAL
    # =====================================================================
    st.markdown("### Volume")
    l1, l2, l3, l4 = st.columns(4)
    l1.metric("Lots actifs", snap["nb_lots_actifs"], help=f"{snap['nb_lots_total']} au total")
    l2.metric("Articles total", snap["nb_articles_total"])
    l3.metric("CA ce mois", f"{snap['ca_mois']:,.0f} EUR",
               delta=f"{snap['nb_ventes_mois']} ventes")
    l4.metric("CA cette semaine", f"{snap['ca_semaine']:,.0f} EUR",
               delta=f"{snap['nb_ventes_semaine']} ventes")

    st.divider()

    # =====================================================================
    # LIGNE 2 : PIPELINE DES ARTICLES (cartes colorees)
    # =====================================================================
    st.markdown("### Pipeline des articles")
    p = snap["pipeline"]
    pc1, pc2, pc3, pc4 = st.columns(4)
    with pc1:
        st.markdown(
            _metric_card("A TESTER", str(p["en_stock"]), "#94a3b8"),
            unsafe_allow_html=True,
        )
    with pc2:
        st.markdown(
            _metric_card("PRETS A PUBLIER", str(p["en_attente_ligne"]), "#f59e0b"),
            unsafe_allow_html=True,
        )
    with pc3:
        st.markdown(
            _metric_card("EN LIGNE", str(p["publie"]), "#2563eb"),
            unsafe_allow_html=True,
        )
    with pc4:
        st.markdown(
            _metric_card("VENDUS", str(p["vendu"]), "#16a34a"),
            unsafe_allow_html=True,
        )

    total_non_vendu = p["en_stock"] + p["en_attente_ligne"] + p["publie"]
    if snap["nb_articles_total"] > 0:
        pct_vendu = p["vendu"] / snap["nb_articles_total"]
        st.progress(
            pct_vendu,
            text=f"{p['vendu']} articles vendus sur {snap['nb_articles_total']} "
                 f"({pct_vendu*100:.0f}%) — reste {total_non_vendu} a traiter",
        )

    st.divider()

    # =====================================================================
    # LIGNE 3 : FINANCES
    # =====================================================================
    st.markdown("### Finances")
    f1, f2, f3, f4 = st.columns(4)
    f1.metric("Total investi", f"{snap['total_investi']:,.0f} EUR")
    f2.metric("CA encaisse", f"{snap['ca_encaisse']:,.0f} EUR")
    f3.metric("Commissions + frais", f"{snap['commissions_frais']:,.0f} EUR")
    benef = snap["benefice_net"]
    f4.metric(
        "Benefice net",
        f"{benef:,.0f} EUR",
        delta_color="normal" if benef >= 0 else "inverse",
    )

    # CA potentiel restant
    ca_pot = snap["ca_potentiel"]
    reste_amortir = max(0, snap["total_investi"] - snap["ca_encaisse"])
    benef_pot = ca_pot + snap["ca_encaisse"] - snap["total_investi"] - snap["commissions_frais"]
    st.caption(
        f"Si tout le stock restant est vendu au prix affiche : "
        f"CA potentiel **{ca_pot:,.0f} EUR** | "
        f"Reste a amortir **{reste_amortir:,.0f} EUR** | "
        f"Benefice potentiel **{benef_pot:,.0f} EUR**"
    )

    st.divider()

    # =====================================================================
    # LIGNE 4 : REPARTITION PAR ETAT
    # =====================================================================
    if snap["etats"]:
        st.markdown("### Repartition par etat (stock restant)")
        etats_sorted = sorted(snap["etats"].items(), key=lambda x: -x[1])
        cols = st.columns(len(etats_sorted))
        total = sum(snap["etats"].values()) or 1
        for i, (etat, count) in enumerate(etats_sorted):
            with cols[i]:
                st.metric(etat, count)
                st.progress(count / total)
        st.divider()

    # =====================================================================
    # LIGNE 5 : ALERTES
    # =====================================================================
    alertes = []
    if snap["stock_mort"] > 0:
        alertes.append(
            f"**{snap['stock_mort']}** article(s) en stock depuis plus de 30 jours "
            "— pensez a baisser le prix."
        )
    if p["en_stock"] >= 20:
        alertes.append(
            f"**{p['en_stock']}** article(s) pas encore testes — priorite pour "
            "accelerer le pipeline."
        )
    if p["en_attente_ligne"] >= 10:
        alertes.append(
            f"**{p['en_attente_ligne']}** article(s) prets a publier — prenez "
            "les photos et mettez en ligne."
        )
    if alertes:
        st.markdown("### Alertes")
        for a in alertes:
            st.warning(a)
        st.divider()

    # =====================================================================
    # LIGNE 6 : TOP STOCK + ACTIVITE RECENTE (cote a cote)
    # =====================================================================
    col_top, col_act = st.columns(2)

    with col_top:
        st.markdown("### Top stock (plus gros potentiels)")
        if snap["top_stock"]:
            for a in snap["top_stock"]:
                st.markdown(
                    f"- **{a['description']}** — "
                    f"{a['prix']:,.0f} EUR _({a['statut']})_"
                )
        else:
            st.caption("Aucun article en stock.")

    with col_act:
        st.markdown("### Dernieres ventes")
        if snap["activite"]:
            for v in snap["activite"]:
                date_str = v["date"].strftime("%d/%m") if v["date"] else "-"
                st.markdown(
                    f"- {date_str} — **{v['description']}** — "
                    f"{v['prix']:,.0f} EUR via {v['canal']} "
                    f"(benef {v['benefice']:,.0f})"
                )
        else:
            st.caption("Aucune vente enregistree.")

    st.divider()

    # =====================================================================
    # LIGNE 7 : RACCOURCIS
    # =====================================================================
    st.markdown("### Raccourcis")

    def _go(page: str):
        st.session_state["sidebar_nav"] = page

    r1, r2, r3 = st.columns(3)
    with r1:
        st.button("Mes lots", on_click=_go, args=("Mes lots",),
                  use_container_width=True, key="dash_go_lots")
    with r2:
        st.button("Mon stock", on_click=_go, args=("Mon stock",),
                  use_container_width=True, key="dash_go_stock")
    with r3:
        st.button("Mes revenus", on_click=_go, args=("Mes revenus",),
                  use_container_width=True, key="dash_go_revenus")
