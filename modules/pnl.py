# -*- coding: utf-8 -*-
"""
DeStock App - modules/pnl.py
P&L, performance financiere, projections.

Trois onglets :
  1. Vue globale   : metriques, CA/semaine, canaux, fiscal
  2. Par lot       : tableau rentabilite par lot
  3. Projection    : vitesse de vente, objectif mensuel, CA potentiel
"""

from __future__ import annotations

from datetime import datetime, timedelta, date

import pandas as pd
import streamlit as st

from database import Article, Lot, Vente, get_session
from modules.parametres import get_param


# ---------------------------------------------------------------------------
# Chargement des donnees
# ---------------------------------------------------------------------------
def _load_all() -> tuple[list[dict], list[dict], list[dict]]:
    """Charge lots, articles et ventes depuis la base. Retourne 3 listes de dicts."""
    session = get_session()
    try:
        lots = [
            {
                "lot_id": r.lot_id,
                "cout_total": r.cout_total or 0,
                "statut": r.statut or "",
                "nb_articles": r.nb_articles or 0,
                "notes": r.notes or "",
            }
            for r in session.query(Lot).all()
        ]

        arts_rows = session.query(Article).all()
        articles = [
            {
                "id": r.id,
                "lot_id": r.lot_id,
                "description": r.description or "",
                "cout_reel": r.cout_reel or 0,
                "frais_remise": r.cout_reconditionnnement or 0,
                "prix_cible": r.prix_cible or 0,
                "statut": r.statut or "",
                "canal_recommande": r.canal_recommande or "",
                "categorie": r.categorie or "",
                "date_reception": r.date_reception,
            }
            for r in arts_rows
        ]

        ventes_rows = session.query(Vente).all()
        ventes = []
        for v in ventes_rows:
            art = session.query(Article).filter_by(id=v.article_id).first()
            ventes.append({
                "article_id": v.article_id,
                "lot_id": art.lot_id if art else "",
                "canal": v.canal or "",
                "prix_vente": v.prix_vente or 0,
                "cout_reel": (art.cout_reel or 0) if art else 0,
                "frais_remise": (art.cout_reconditionnnement or 0) if art else 0,
                "date_vente": v.date_vente,
            })
        return lots, articles, ventes
    finally:
        session.close()


# =========================================================================
# ONGLET 1 — Vue globale
# =========================================================================
def _tab_vue_globale() -> None:
    lots, articles, ventes = _load_all()

    ca_total = sum(v["prix_vente"] for v in ventes)
    cout_investi = sum(l["cout_total"] for l in lots)
    frais_total = sum(v["frais_remise"] for v in ventes)
    benef_net = ca_total - cout_investi
    marge_nette = round(benef_net / cout_investi * 100, 1) if cout_investi > 0 else 0
    nb_vendus = len(ventes)
    nb_en_stock = sum(1 for a in articles if a["statut"] == "en_stock")
    lots_non_liq = [l for l in lots if l["statut"] != "liquide"]
    cash_engage = sum(l["cout_total"] for l in lots_non_liq)

    # --- Metriques principales ---
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("CA total encaisse", f"{ca_total:,.0f} EUR")
    m2.metric("Cout total investi", f"{cout_investi:,.0f} EUR")
    m3.metric("Benefice net", f"{benef_net:,.0f} EUR")
    m4.metric("Marge nette", f"{marge_nette:.1f}%")
    # Cash disponible estime = CA - couts - charges fiscales estimees
    statut_jur = get_param("profil_statut", "Aucun")
    charges_est = ca_total * 0.22 if statut_jur == "Auto-entrepreneur" else 0
    cash_dispo = round(ca_total - cout_investi - charges_est, 2)

    m5, m6, m7, m8 = st.columns(4)
    m5.metric("Articles vendus", nb_vendus)
    m6.metric("Articles en stock", nb_en_stock)
    m7.metric("Cash engage", f"{cash_engage:,.0f} EUR")
    color = "normal" if cash_dispo >= 500 else ("off" if cash_dispo >= 0 else "inverse")
    m8.metric("Cash disponible", f"{cash_dispo:,.0f} EUR", delta_color=color)

    # --- CA par semaine ---
    st.divider()
    st.markdown("**CA par semaine**")
    if ventes:
        df_v = pd.DataFrame(ventes)
        df_v["date_vente"] = pd.to_datetime(df_v["date_vente"])
        df_v["semaine"] = df_v["date_vente"].dt.to_period("W").astype(str)
        ca_semaine = df_v.groupby("semaine")["prix_vente"].sum().reset_index()
        ca_semaine.columns = ["Semaine", "CA"]
        st.bar_chart(ca_semaine, x="Semaine", y="CA")
    else:
        st.info("Aucune vente enregistree.")

    # --- Repartition par canal ---
    st.divider()
    st.markdown("**Repartition par canal**")
    if ventes:
        df_v = pd.DataFrame(ventes)
        canaux_stats = []
        for canal in sorted(df_v["canal"].unique()):
            sub = df_v[df_v["canal"] == canal]
            ca_c = sub["prix_vente"].sum()
            benef_c = (sub["prix_vente"] - sub["cout_reel"] - sub["frais_remise"]).sum()
            benef_moy = benef_c / len(sub) if len(sub) > 0 else 0
            canaux_stats.append({
                "Canal": canal,
                "Nb ventes": len(sub),
                "CA total": round(ca_c, 0),
                "Benefice moyen": round(benef_moy, 0),
            })
        st.dataframe(pd.DataFrame(canaux_stats), use_container_width=True, hide_index=True)
    else:
        st.info("Aucune vente.")

    # --- Fiscal ---
    st.divider()
    st.markdown("**Estimation fiscale**")
    statut_juridique = get_param("profil_statut", "Aucun")
    ca_annuel = float(get_param("profil_ca_annuel", "0") or 0) + ca_total

    if statut_juridique == "Auto-entrepreneur":
        plafond = 77700
        charges_pct = 0.22
        charges = round(ca_annuel * charges_pct, 2)
        net_apres_charges = round(ca_annuel - charges - cout_investi, 2)

        fc1, fc2, fc3 = st.columns(3)
        fc1.metric("CA annuel cumule", f"{ca_annuel:,.0f} EUR")
        fc2.metric("Charges sociales (22%)", f"{charges:,.0f} EUR")
        fc3.metric("Net apres charges", f"{net_apres_charges:,.0f} EUR")
        pct_plafond = round(ca_annuel / plafond * 100, 1)
        st.progress(min(pct_plafond / 100, 1.0), text=f"{pct_plafond:.1f}% du plafond AE ({plafond:,} EUR)")
        if pct_plafond > 80:
            st.warning(f"Attention : {pct_plafond:.1f}% du plafond auto-entrepreneur atteint.")

    elif statut_juridique in ("SAS", "SARL"):
        tva_marge = sum(
            (v["prix_vente"] - v["cout_reel"]) * 0.166
            for v in ventes if v["prix_vente"] > v["cout_reel"]
        )
        benef_imposable = benef_net - tva_marge
        is_estime = round(benef_imposable * 0.15, 2) if benef_imposable > 0 else 0
        fc1, fc2, fc3 = st.columns(3)
        fc1.metric("TVA sur marge", f"{tva_marge:,.0f} EUR")
        fc2.metric("IS estime (15%)", f"{is_estime:,.0f} EUR")
        fc3.metric("Net apres IS + TVA", f"{benef_net - tva_marge - is_estime:,.0f} EUR")
    else:
        st.caption("Pas de statut juridique configure. Allez dans Parametres > Profil juridique.")


# =========================================================================
# ONGLET 2 — Par lot
# =========================================================================
def _tab_par_lot() -> None:
    lots, articles, ventes = _load_all()
    if not lots:
        st.info("Aucun lot en base.")
        return

    rows = []
    best_marge = -999
    worst_marge = 999
    for lot in lots:
        lid = lot["lot_id"]
        arts_lot = [a for a in articles if a["lot_id"] == lid]
        ventes_lot = [v for v in ventes if v["lot_id"] == lid]
        ca_lot = sum(v["prix_vente"] for v in ventes_lot)
        cout_lot = lot["cout_total"]
        frais_lot = sum(a["frais_remise"] for a in arts_lot)
        benef_lot = ca_lot - cout_lot
        marge_lot = round(benef_lot / cout_lot * 100, 1) if cout_lot > 0 else 0
        vendus = sum(1 for a in arts_lot if a["statut"] == "vendu")
        total = len(arts_lot)
        taux_liq = round(vendus / total * 100, 1) if total > 0 else 0

        if marge_lot > best_marge:
            best_marge = marge_lot
        if marge_lot < worst_marge:
            worst_marge = marge_lot

        rows.append({
            "Lot": lid,
            "Titre": (lot["notes"] or "")[:35],
            "Cout total": round(cout_lot, 0),
            "CA encaisse": round(ca_lot, 0),
            "Benefice": round(benef_lot, 0),
            "Marge %": marge_lot,
            "Vendus": f"{vendus}/{total}",
            "Liquidation %": taux_liq,
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Cout total": st.column_config.NumberColumn(format="%.0f EUR"),
            "CA encaisse": st.column_config.NumberColumn(format="%.0f EUR"),
            "Benefice": st.column_config.NumberColumn(format="%.0f EUR"),
            "Marge %": st.column_config.NumberColumn(format="%.1f %%"),
            "Liquidation %": st.column_config.ProgressColumn(
                "Liquidation", min_value=0, max_value=100, format="%.0f%%",
            ),
        },
    )

    if rows:
        best = max(rows, key=lambda r: r["Marge %"])
        worst = min(rows, key=lambda r: r["Marge %"])
        c1, c2 = st.columns(2)
        c1.success(f"Meilleur lot : **{best['Lot']}** — marge {best['Marge %']:.1f}%")
        c2.error(f"Pire lot : **{worst['Lot']}** — marge {worst['Marge %']:.1f}%")


# =========================================================================
# ONGLET 3 — Projection
# =========================================================================
def _tab_projection() -> None:
    lots, articles, ventes = _load_all()

    en_stock = [a for a in articles if a["statut"] == "en_stock"]
    nb_stock = len(en_stock)

    # --- Vitesse de vente ---
    st.markdown("**Vitesse de vente**")
    now = datetime.utcnow()
    quatre_sem = now - timedelta(weeks=4)
    ventes_4sem = [v for v in ventes if v["date_vente"] and v["date_vente"] >= quatre_sem]
    ventes_par_sem = round(len(ventes_4sem) / 4, 1) if ventes_4sem else 0
    semaines_restantes = round(nb_stock / ventes_par_sem, 1) if ventes_par_sem > 0 else float("inf")

    vc1, vc2, vc3 = st.columns(3)
    vc1.metric("Ventes / semaine (moy. 4 sem)", f"{ventes_par_sem:.1f}")
    vc2.metric("Articles en stock", nb_stock)
    sem_txt = f"{semaines_restantes:.0f} sem" if semaines_restantes < 1000 else "-"
    vc3.metric("Semaines pour tout vendre", sem_txt)

    # --- Objectif mensuel ---
    st.divider()
    st.markdown("**Objectif mensuel**")
    objectif = st.number_input("Objectif CA mensuel (EUR)", 0.0, 100000.0, 3000.0, step=500.0, key="pnl_objectif")
    debut_mois = datetime(now.year, now.month, 1)
    ventes_mois = [v for v in ventes if v["date_vente"] and v["date_vente"] >= debut_mois]
    ca_mois = sum(v["prix_vente"] for v in ventes_mois)
    pct = round(ca_mois / objectif * 100, 1) if objectif > 0 else 0

    oc1, oc2 = st.columns(2)
    oc1.metric("CA ce mois", f"{ca_mois:,.0f} EUR")
    oc2.metric("Progression", f"{pct:.1f}%")
    st.progress(min(pct / 100, 1.0), text=f"{pct:.1f}% de l'objectif ({objectif:,.0f} EUR)")

    if ventes_par_sem > 0 and ca_mois < objectif:
        ca_par_sem = sum(v["prix_vente"] for v in ventes_4sem) / 4 if ventes_4sem else 0
        reste = objectif - ca_mois
        sem_pour_obj = round(reste / ca_par_sem, 1) if ca_par_sem > 0 else float("inf")
        if sem_pour_obj < 1000:
            st.caption(f"A ce rythme : objectif atteint dans environ **{sem_pour_obj:.0f} semaines**.")

    # --- CA potentiel restant ---
    st.divider()
    st.markdown("**CA potentiel restant (stock)**")
    ca_potentiel = sum(a["prix_cible"] for a in en_stock)
    cout_stock = sum(a["cout_reel"] + a["frais_remise"] for a in en_stock)
    benef_potentiel = ca_potentiel - cout_stock

    pc1, pc2, pc3 = st.columns(3)
    pc1.metric("CA potentiel", f"{ca_potentiel:,.0f} EUR")
    pc2.metric("Cout stock", f"{cout_stock:,.0f} EUR")
    pc3.metric("Benefice potentiel", f"{benef_potentiel:,.0f} EUR")

    # --- Tableau de bord aujourd'hui ---
    st.divider()
    st.markdown("**Aujourd'hui**")
    debut_jour = datetime(now.year, now.month, now.day)
    ventes_jour = [v for v in ventes if v["date_vente"] and v["date_vente"] >= debut_jour]
    ca_jour = sum(v["prix_vente"] for v in ventes_jour)

    jc1, jc2, jc3 = st.columns(3)
    jc1.metric("Ventes du jour", len(ventes_jour))
    jc2.metric("CA du jour", f"{ca_jour:,.0f} EUR")
    if ventes_jour:
        best = max(ventes_jour, key=lambda v: v["prix_vente"])
        jc3.metric("Meilleure vente", f"{best['prix_vente']:,.0f} EUR")
    else:
        jc3.metric("Meilleure vente", "-")


# =========================================================================
# ONGLET 4 — Par categorie
# =========================================================================
def _tab_par_categorie() -> None:
    lots, articles, ventes = _load_all()
    if not ventes:
        st.info("Aucune vente enregistree.")
        return

    # Enrichit chaque vente avec la categorie de l'article
    art_map = {a["id"]: a for a in articles}
    cat_data: dict[str, dict] = {}
    for v in ventes:
        art = art_map.get(v["article_id"])
        cat = (art.get("categorie") or "Autre") if art else "Autre"
        if cat not in cat_data:
            cat_data[cat] = {"nb": 0, "ca": 0.0, "benef": 0.0, "jours_total": 0}
        d = cat_data[cat]
        d["nb"] += 1
        d["ca"] += v["prix_vente"]
        d["benef"] += v["prix_vente"] - v["cout_reel"] - v["frais_remise"]
        if art and art.get("date_reception") and v["date_vente"]:
            try:
                dr = art["date_reception"] if isinstance(art["date_reception"], datetime) else datetime.fromisoformat(str(art["date_reception"]))
                d["jours_total"] += (v["date_vente"] - dr).days
            except Exception:
                pass

    rows = []
    for cat, d in cat_data.items():
        marge = round(d["benef"] / d["ca"] * 100, 1) if d["ca"] > 0 else 0
        delai = round(d["jours_total"] / d["nb"], 1) if d["nb"] > 0 else 0
        rows.append({
            "Categorie": cat,
            "Nb vendus": d["nb"],
            "CA total": round(d["ca"], 0),
            "Benefice": round(d["benef"], 0),
            "Marge %": marge,
            "Delai moyen (j)": delai,
        })
    rows.sort(key=lambda r: r["CA total"], reverse=True)
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True, column_config={
        "CA total": st.column_config.NumberColumn(format="%.0f EUR"),
        "Benefice": st.column_config.NumberColumn(format="%.0f EUR"),
        "Marge %": st.column_config.NumberColumn(format="%.1f %%"),
    })

    if rows:
        best_marge = max(rows, key=lambda r: r["Marge %"])
        best_speed = min(rows, key=lambda r: r["Delai moyen (j)"] if r["Delai moyen (j)"] > 0 else 999)
        st.success(
            f"Meilleure categorie : **{best_marge['Categorie']}** "
            f"({best_marge['Marge %']:.1f}% de marge, {best_marge['Delai moyen (j)']:.0f}j en moyenne)"
        )
        if best_speed["Delai moyen (j)"] > 0:
            st.info(
                f"Categorie la plus rapide : **{best_speed['Categorie']}** "
                f"({best_speed['Delai moyen (j)']:.0f}j pour vendre)"
            )


# =========================================================================
# Entree principale
# =========================================================================
def render() -> None:
    st.title("P&L / Finances")
    tab1, tab2, tab3, tab4 = st.tabs(["Vue globale", "Par lot", "Projection", "Par categorie"])
    with tab1:
        _tab_vue_globale()
    with tab2:
        _tab_par_lot()
    with tab3:
        _tab_projection()
    with tab4:
        _tab_par_categorie()
