# -*- coding: utf-8 -*-
"""
DeStock App - modules/marketplace.py
Exploration, scoring et analyse des lots B-Stock.

Deux onglets :
  1. Lots disponibles : scoring automatique + filtres disqualifiants + tri
  2. Analyser un lot  : scorecard API + upload CSV manifeste + enrichissement
"""

from datetime import datetime

import pandas as pd
import streamlit as st

from database import Article, Lot, get_session
from modules.parametres import get_param, get_scoring_params
from scrapers import bstock
from scrapers import prix_marche


# ---------------------------------------------------------------------------
# Helpers d'affichage
# ---------------------------------------------------------------------------
def _format_secondes(sec: int) -> str:
    if sec is None or sec < 0:
        return "-"
    if sec <= 0:
        return "Ferme"
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    if d:
        return f"{d}j {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _etoiles(score: int) -> str:
    try:
        n = int(score)
    except (TypeError, ValueError):
        n = 0
    return "*" * max(0, min(5, n)) + "." * max(0, 5 - n)


def _country_from_lot(lot: dict) -> str:
    """Extrait le code pays a 2 lettres d'un lot (GB -> UK pour coherence)."""
    c = (lot.get("seller_country") or "").upper().strip()
    if c == "GB":
        c = "UK"
    return c


# ---------------------------------------------------------------------------
# MOTEUR DE SCORING
# ---------------------------------------------------------------------------
def calculate_score(lot: dict, params: dict) -> dict:
    """
    Calcule un score /100 pour un lot selon les parametres de scoring.
    Applique d'abord les filtres disqualifiants, puis 6 criteres ponderes +
    bonus/malus. Les valeurs reelles sont exposees dans `details` pour que
    l'UI puisse montrer pourquoi un lot a tel score.
    """
    enchere = float(lot.get("enchere") or 0)
    nb_articles = int(lot.get("nb_articles") or 0)
    retail = float(lot.get("retail_total") or 0)
    ratio = (enchere / retail * 100) if retail > 0 else 100.0
    cout_moyen = (enchere / nb_articles) if nb_articles > 0 else 9999
    country = _country_from_lot(lot)
    vendeur = (lot.get("site_name") or lot.get("storefront_name") or "").lower()

    result = {
        "score_total": 0,
        "score_ratio": 0,
        "score_volume": 0,
        "score_categorie": 0,
        "score_cout_moyen": 0,
        "score_localisation": 0,
        "score_condition": 0,
        "bonus": 0,
        "disqualifie": False,
        "raison_disqualification": "",
        "details": {
            "ratio": f"{ratio:.1f}%",
            "retail": f"{retail:,.0f}",
            "cout_moyen": f"{cout_moyen:.2f}",
            "pays": country or "?",
            "categorie": lot.get("categorie") or ", ".join(lot.get("categories") or []),
            "vendeur": vendeur,
            "nb_articles": nb_articles,
        },
    }

    # --- FILTRES DISQUALIFIANTS ---
    if enchere > params["budget_max"] > 0:
        result.update(disqualifie=True, raison_disqualification="Hors budget")
        return result
    if nb_articles < params["nb_articles_min"]:
        result.update(disqualifie=True, raison_disqualification="Lot trop petit")
        return result
    if nb_articles > params["nb_articles_max"]:
        result.update(disqualifie=True, raison_disqualification="Lot trop grand")
        return result
    if ratio > params["ratio_max_pct"]:
        result.update(disqualifie=True, raison_disqualification="Trop cher")
        return result
    locs = [l.upper() for l in params.get("localisations") or []]
    if params.get("exclure_uk") and country == "UK":
        result.update(disqualifie=True, raison_disqualification="Hors zone (UK)")
        return result
    if country and locs and country not in locs:
        result.update(disqualifie=True, raison_disqualification=f"Hors zone ({country})")
        return result

    # --- 1. SCORE RATIO DECOTE ---
    p = params["poids_ratio"]
    if ratio < params["ratio_excellent"]:
        score_ratio = p
    elif ratio < params["ratio_bon"]:
        score_ratio = int(p * 0.75)
    elif ratio < params["ratio_passable"]:
        score_ratio = int(p * 0.40)
    else:
        score_ratio = 0

    # --- 2. SCORE VOLUME RETAIL ---
    p = params.get("poids_volume_retail", 0)
    if retail >= params.get("volume_excellent", 50000):
        score_vol = p
    elif retail >= params.get("volume_bon", 20000):
        score_vol = int(p * 0.75)
    elif retail >= params.get("volume_passable", 5000):
        score_vol = int(p * 0.50)
    else:
        score_vol = 0

    # --- 3. SCORE CATEGORIE ---
    p = params["poids_categorie"]
    if params.get("toutes_categories"):
        score_cat = p
    else:
        cats_lot = lot.get("categories") or []
        if isinstance(cats_lot, str):
            cats_lot = [c.strip() for c in cats_lot.split(",") if c.strip()]
        cats_pref = [c.strip().lower() for c in params.get("categories_preferees") or []]
        if not cats_pref or any(c.strip().lower() in cats_pref for c in cats_lot):
            score_cat = p
        else:
            score_cat = 0

    # --- 4. SCORE COUT MOYEN ---
    p = params["poids_cout_moyen"]
    if params["cout_ideal_min"] <= cout_moyen <= params["cout_ideal_max"]:
        score_cout = p
    elif cout_moyen < params["cout_ideal_min"]:
        # Trop cheap (ex: 0.20 EUR/article) -> toujours bon signe
        score_cout = p
    elif cout_moyen <= params["cout_acceptable_max"]:
        score_cout = int(p * 0.50)
    else:
        score_cout = 0

    # --- 5. SCORE LOCALISATION ---
    p = params["poids_localisation"]
    if country in ("PL", "DE"):
        score_loc = p
    elif country in ("ES", "FR", "IT", "NL", "BE"):
        score_loc = int(p * 0.75)
    elif country in ("UK", "GB"):
        score_loc = 0
    else:
        score_loc = int(p * 0.50)

    # --- 6. SCORE CONDITION (0 par defaut, recalcule quand CSV uploade) ---
    score_cond = 0

    # --- BONUS / MALUS ---
    bonus = 0
    if params.get("bonus_amazon") and "amazon" in vendeur:
        bonus += 10
    if country in ("UK", "GB"):
        bonus -= 20

    total = score_ratio + score_vol + score_cat + score_cout + score_loc + score_cond + bonus
    total = max(0, min(100, total))

    result.update({
        "score_total": total,
        "score_ratio": score_ratio,
        "score_volume": score_vol,
        "score_categorie": score_cat,
        "score_cout_moyen": score_cout,
        "score_localisation": score_loc,
        "score_condition": score_cond,
        "bonus": bonus,
    })
    return result


# =========================================================================
# ONGLET 1 — Lots disponibles
# =========================================================================
def _tab_lots_disponibles() -> None:
    st.subheader("Lots disponibles - Europe")

    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        if st.button("Actualiser les lots", use_container_width=True):
            with st.spinner("Appel API B-Stock..."):
                try:
                    lots = bstock.get_lots_europe(max_lots=200, page_size=50)
                    st.session_state["mk_lots"] = lots
                    st.session_state["mk_lots_scraped_at"] = datetime.now()
                    st.success(f"{len(lots)} lots recuperes.")
                except Exception as exc:
                    st.error(f"Erreur API : {exc}")
    with col_info:
        ts = st.session_state.get("mk_lots_scraped_at")
        if ts:
            st.caption(f"Derniere actualisation : {ts.strftime('%d/%m/%Y %H:%M:%S')}")

    lots: list[dict] = st.session_state.get("mk_lots", [])
    if not lots:
        st.info("Clique sur 'Actualiser les lots' pour charger les lots B-Stock EU.")
        return

    # --- Calcul des scores ---
    params = get_scoring_params()
    scored: list[dict] = []
    for lot in lots:
        sc = calculate_score(lot, params)
        scored.append({**lot, **sc})

    # Tri : qualifies par score decroissant, puis disqualifies en bas
    scored.sort(key=lambda x: (not x["disqualifie"], x["score_total"]), reverse=True)

    # --- Construction du DataFrame ---
    rows = []
    for i, s in enumerate(scored):
        ratio = (s["enchere"] / s["retail_total"] * 100) if s.get("retail_total") else 0
        nb = s.get("nb_articles", 0)
        cout_moy = round(s["enchere"] / nb, 2) if nb > 0 else 0
        rows.append({
            "Score": s["score_total"],
            "Titre": (s.get("titre") or "")[:70],
            "Enchere": s.get("enchere", 0),
            "Articles": nb,
            "Retail": s.get("retail_total", 0),
            "Ratio %": round(ratio, 1),
            "EUR/art": cout_moy,
            "Lieu": (s.get("localisation") or "")[:30],
            "Ferme dans": _format_secondes(s.get("ferme_dans_secondes", -1)),
            "Statut": s.get("raison_disqualification", "") if s["disqualifie"] else "",
            "URL": s.get("url", ""),
        })
    df = pd.DataFrame(rows)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%d",
            ),
            "Enchere":  st.column_config.NumberColumn("Enchere EUR", format="%.0f"),
            "Retail":   st.column_config.NumberColumn("Retail EUR", format="%.0f"),
            "Ratio %":  st.column_config.NumberColumn(format="%.1f %%"),
            "EUR/art":  st.column_config.NumberColumn(format="%.2f"),
            "URL":      st.column_config.LinkColumn("B-Stock", display_text="Ouvrir"),
        },
    )

    # --- Actions par lot ---
    st.divider()
    st.markdown("**Selectionner un lot**")
    titres = [f"{i+1}. [{s['score_total']}pts] {(s.get('titre') or s.get('url',''))[:55]}"
              for i, s in enumerate(scored)]
    choix = st.selectbox("Lot", ["-"] + titres, key="mk_lot_select", label_visibility="collapsed")
    if choix == "-":
        return
    idx = titres.index(choix)
    s = scored[idx]

    # Detail du score
    with st.expander("Detail du score", expanded=True):
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Ratio", f"{s['score_ratio']}pts")
        c2.metric("Volume", f"{s['score_volume']}pts")
        c3.metric("Categorie", f"{s['score_categorie']}pts")
        c4.metric("Cout/art.", f"{s['score_cout_moyen']}pts")
        c5.metric("Localisation", f"{s['score_localisation']}pts")
        c6.metric("Bonus", f"{s['bonus']:+d}pts")
        d = s["details"]
        enchere_txt = f"{s.get('enchere', 0):,.0f}"
        st.caption(
            f"Enchere actuelle : **{enchere_txt} EUR** | "
            f"Ratio decote : **{d['ratio']}** | "
            f"Retail : **{d['retail']} EUR** | "
            f"Cout/article : **{d['cout_moyen']} EUR** | "
            f"Pays : **{d['pays']}** | "
            f"Categorie : **{d['categorie'] or '-'}** | "
            f"Vendeur : **{d['vendeur'] or '-'}** | "
            f"Articles : **{d.get('nb_articles', '-')}**"
        )
        if s["disqualifie"]:
            st.error(f"Disqualifie : {s['raison_disqualification']}")

    # Boutons d'action
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if s.get("url"):
            st.link_button("Ouvrir sur B-Stock", s["url"], use_container_width=True)
    with col_b:
        if st.button("Analyser ce lot", use_container_width=True, type="primary", key="mk_goto_analyze"):
            st.session_state["mk_url_lot"] = s.get("url", "")
            st.session_state.pop("mk_detail", None)
            st.success("URL pre-remplie — ouvrez l'onglet 'Analyser un lot'.")
    with col_c:
        if st.button("Surveiller", use_container_width=True, key="mk_surveiller_lot"):
            try:
                _persist_lot(s, [], "surveillance", False)
                st.success("Lot ajoute en surveillance.")
            except Exception as exc:
                st.error(str(exc))


# =========================================================================
# ONGLET 2 — Analyser un lot
# =========================================================================
def _tab_analyser_lot() -> None:
    st.subheader("Analyser un lot")

    # Recuperation URL et infos lot via API
    url_default = st.session_state.get("mk_url_lot", "")
    url = st.text_input(
        "URL du lot B-Stock",
        value=url_default,
        placeholder="https://bstock.com/amazoneu/auction/auction/view/id/48982/",
    )
    col_fetch, col_open = st.columns(2)
    with col_fetch:
        fetch_clicked = st.button("Recuperer infos", use_container_width=True, type="primary")
    with col_open:
        if url:
            st.link_button("Ouvrir sur B-Stock", url, use_container_width=True)

    if fetch_clicked and url:
        try:
            frais_pct = float(get_param("business_frais_bstock_pct", "5") or 5)
        except ValueError:
            frais_pct = 5.0
        with st.spinner("Recuperation via l'API..."):
            try:
                lot_data = bstock.get_lot_detail(url, frais_bstock_pct=frais_pct)
                st.session_state["mk_detail"] = {
                    "lot": lot_data, "articles": [], "csv_path": "",
                }
            except Exception as exc:
                st.error(f"Erreur API : {exc}")

    detail = st.session_state.get("mk_detail")
    if not detail or not detail.get("lot"):
        st.info("Colle l'URL d'un lot B-Stock puis clique 'Recuperer infos'.")
        return

    lot = detail["lot"]

    # =====================================================================
    # PARTIE 1 — DONNEES B-STOCK + BUDGET
    # =====================================================================
    st.divider()
    st.markdown("### 1. Donnees B-Stock")
    st.caption(
        "B-Stock gonfle les prix retail. "
        "Ces donnees servent uniquement a estimer votre budget max."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Enchere actuelle", f"{lot.get('enchere', 0):,.0f} EUR")
    c2.metric("Frais B-Stock", f"{lot.get('frais_bstock', 0):,.0f} EUR")
    c3.metric("Retail B-Stock", f"{lot.get('retail_total', 0):,.0f} EUR")
    c4.metric("Nb articles", f"{lot.get('nb_articles', 0)}")

    ferme_sec = lot.get("ferme_dans_secondes", -1)
    ferme_txt = _format_secondes(ferme_sec) if ferme_sec and ferme_sec > 0 else lot.get("date_cloture", "-")
    st.caption(f"Ferme dans : **{ferme_txt}** | {lot.get('titre', '')[:80]}")

    # --- Saisie manuelle du budget ---
    st.markdown("**Calculer votre budget max**")
    budget_cols = st.columns(4)
    with budget_cols[0]:
        enchere_user = st.number_input(
            "Votre enchere max (EUR)",
            min_value=0.0, step=50.0,
            value=float(st.session_state.get("mk_enchere_user", lot.get("enchere", 0))),
            key="mk_enchere_input",
        )
    with budget_cols[1]:
        frais_bstock_pct = float(get_param("business_frais_bstock_pct", "5") or 5)
        frais_bstock_calc = round(enchere_user * frais_bstock_pct / 100, 2)
        st.metric("Frais B-Stock calcules", f"{frais_bstock_calc:,.0f} EUR")
    with budget_cols[2]:
        frais_supp_user = st.number_input(
            "Frais supplementaires B-Stock (EUR)",
            min_value=0.0, step=50.0,
            value=float(st.session_state.get("mk_frais_supp_user", 0)),
            key="mk_frais_supp_input",
            help="Frais affiches au moment d'encherir (ex: 794 EUR pour ce lot)",
        )
    with budget_cols[3]:
        livraison_user = st.number_input(
            "Frais livraison (EUR)",
            min_value=0.0, step=50.0,
            value=float(st.session_state.get("mk_livraison_user", lot.get("frais_livraison", 0))),
            key="mk_livraison_input",
        )

    # Sauvegarde dans session pour Partie 2
    st.session_state["mk_enchere_user"] = enchere_user
    st.session_state["mk_frais_supp_user"] = frais_supp_user
    st.session_state["mk_livraison_user"] = livraison_user

    cout_total_user = round(enchere_user + frais_bstock_calc + frais_supp_user + livraison_user, 2)
    nb_articles = int(lot.get("nb_articles") or 1)
    cout_par_article = round(cout_total_user / nb_articles, 2) if nb_articles > 0 else 0

    # Metriques recapitulatives
    rc1, rc2, rc3, rc4, rc5 = st.columns(5)
    rc1.metric("Enchere", f"{enchere_user:,.0f} EUR")
    rc2.metric("Frais B-Stock", f"{frais_bstock_calc:,.0f} EUR")
    rc3.metric("Frais supp.", f"{frais_supp_user:,.0f} EUR")
    rc4.metric("Livraison", f"{livraison_user:,.0f} EUR")
    rc5.metric("COUT TOTAL", f"{cout_total_user:,.0f} EUR")

    st.metric("COUT PAR ARTICLE", f"{cout_par_article:,.2f} EUR")

    if cout_par_article < 5:
        st.success(f"Excellent — {cout_par_article:.2f} EUR/article, fort potentiel de revente.")
    elif cout_par_article <= 20:
        st.warning(f"Correct — {cout_par_article:.2f} EUR/article, analysez le manifeste pour valider.")
    else:
        st.error(f"Risque — {cout_par_article:.2f} EUR/article, verifiez bien les articles avant d'encherir.")

    # Score du lot recalcule en temps reel avec les vrais couts saisis
    params = get_scoring_params()
    lot_pour_score = {**lot, "enchere": enchere_user, "cout_total": cout_total_user}
    sc = calculate_score(lot_pour_score, params)
    col_sc, col_det = st.columns([1, 4])
    with col_sc:
        color = "green" if sc["score_total"] >= 70 else ("orange" if sc["score_total"] >= 40 else "red")
        st.markdown(f"**Score : :{color}[{sc['score_total']}/100]**")
        if sc["disqualifie"]:
            st.error(sc["raison_disqualification"])
    with col_det:
        st.caption(
            f"Enchere : **{enchere_user:,.0f} EUR** | "
            f"Frais supp : **{frais_supp_user:,.0f} EUR** | "
            f"Livraison : **{livraison_user:,.0f} EUR** | "
            f"TOTAL : **{cout_total_user:,.0f} EUR** | "
            f"**{cout_par_article:.2f} EUR/article**"
        )
        d = sc["details"]
        st.caption(
            f"Ratio {sc['score_ratio']}pts ({d['ratio']}) | "
            f"Volume {sc['score_volume']}pts ({d['retail']} EUR retail) | "
            f"Cat {sc['score_categorie']}pts | "
            f"Cout {sc['score_cout_moyen']}pts ({d['cout_moyen']} EUR/art) | "
            f"Loc {sc['score_localisation']}pts ({d['pays']}) | "
            f"Bonus {sc['bonus']:+d}pts"
        )

    # =====================================================================
    # PARTIE 2 — IMPORT MANIFESTE
    # =====================================================================
    st.divider()
    st.markdown("### 2. Importez le manifeste CSV")
    st.markdown(
        "**1.** Cliquez 'Ouvrir sur B-Stock' ci-dessus et connectez-vous  \n"
        "**2.** Telechargez le manifeste CSV depuis la page du lot  \n"
        "**3.** Deposez le fichier ici :"
    )
    uploaded_csv = st.file_uploader(
        "Fichier CSV du manifeste",
        type=["csv"],
        accept_multiple_files=False,
        key="mk_manifest_upload",
    )
    if uploaded_csv is not None and not detail.get("articles"):
        try:
            lot_id = lot.get("lot_id") or bstock._extract_lot_id(url)
            csv_path = bstock.DOWNLOADS_DIR / f"manifest_{lot_id}_upload.csv"
            csv_path.write_bytes(uploaded_csv.getvalue())
            # cout_total = enchere + frais_bstock + frais_supp + livraison (saisis Partie 1)
            lot_for_parse = {**lot, "cout_total": cout_total_user, "lot_id": lot_id}
            articles = bstock.parse_manifest(csv_path, lot_for_parse)
            detail["articles"] = articles
            detail["csv_path"] = str(csv_path)
            st.session_state["mk_detail"] = detail
            st.success(f"{len(articles)} articles parses avec un cout total de {cout_total_user:,.0f} EUR.")
            st.rerun()
        except Exception as exc:
            st.error(f"Erreur parsing CSV : {type(exc).__name__}: {exc}")

    articles = detail.get("articles") or []
    if not articles:
        st.info("Deposez le manifeste CSV pour debloquer l'analyse articles.")
        return

    # =====================================================================
    # PARTIE 3 — RESULTATS REELS
    # =====================================================================
    st.divider()
    st.markdown("### 3. Resultats bases sur vos couts reels")

    # --- Synthese lot (4 metriques grandes) ---
    ca_pot = sum(a.get("prix_cible", 0) for a in articles)
    benef_total = sum(a.get("benef_net", 0) for a in articles)
    cout_total_art = sum(a.get("cout_reel", 0) + a.get("frais_remise", 0) for a in articles)
    marge_nette = round(benef_total / cout_total_art * 100, 1) if cout_total_art > 0 else 0
    nb_risque = sum(1 for a in articles if a.get("benef_net", 0) < 0)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("CA potentiel", f"{ca_pot:,.0f} EUR")
    m2.metric("Benefice net estime", f"{benef_total:,.0f} EUR")
    m3.metric("Marge nette", f"{marge_nette:.1f}%")
    m4.metric("Articles a risque", f"{nb_risque}")

    # --- Repartition par etat ---
    conditions: dict[str, int] = {}
    for a in articles:
        c = a.get("condition", "Autre")
        conditions[c] = conditions.get(c, 0) + 1
    cols_cond = st.columns(len(conditions) or 1)
    for i, (cond_name, cond_count) in enumerate(sorted(conditions.items(), key=lambda x: -x[1])):
        cols_cond[i % len(cols_cond)].metric(cond_name, cond_count)

    # --- Enrichissement prix marche ---
    st.divider()
    _render_enrichment_section(articles)

    # --- Tableau articles ---
    st.divider()
    _render_articles_section(articles)

    # --- Decision ---
    st.divider()
    st.markdown("**Decision**")
    _render_actions(lot, articles)


# ---------------------------------------------------------------------------
# Enrichissement prix marche
# ---------------------------------------------------------------------------
def _render_enrichment_section(articles: list[dict]) -> None:
    if not articles:
        return
    already = sum(1 for a in articles if a.get("enrichi"))
    st.markdown("**Enrichissement prix marche (LBC / eBay)**")
    col_info, col_limit = st.columns([2, 1])
    with col_info:
        if already:
            st.caption(f"{already}/{len(articles)} articles enrichis.")
        else:
            st.caption("Interroge LBC et eBay pour les vrais prix marche. Cache 24h par ASIN.")
    with col_limit:
        max_a = st.number_input(
            "Nb max articles", 1, len(articles),
            min(20, len(articles)), step=5, key="mk_enrich_limit",
        )

    if st.button("Enrichir prix marche", use_container_width=True, key="mk_enrich_btn"):
        indices = sorted(
            range(len(articles)),
            key=lambda i: articles[i].get("retail_price", 0) or 0,
            reverse=True,
        )[:int(max_a)]
        progress = st.progress(0.0, text="Enrichissement...")
        n_ok = 0
        for step, idx in enumerate(indices, 1):
            art = articles[idx]
            try:
                r = prix_marche.analyser_article(
                    asin=art.get("asin", ""), ean=art.get("ean", ""),
                    description=art.get("description", ""), condition=art.get("condition", ""),
                )
            except Exception:
                progress.progress(step / len(indices))
                continue
            prix_cible_m = float(r.get("prix_cible_calcule") or 0)
            cout_reel = float(art.get("cout_reel") or 0)
            marge_r = round((prix_cible_m - cout_reel) / cout_reel * 100, 2) if cout_reel > 0 else 0.0
            art.update({
                "enrichi": True,
                "prix_marche_amazon": float(r.get("prix_amazon") or 0),
                "prix_marche_lbc": float(r.get("prix_lbc_median") or 0),
                "prix_marche_ebay": float(r.get("prix_ebay_median") or 0),
                "prix_cible_marche": prix_cible_m,
                "marge_reelle": marge_r,
                "canal_recommande_marche": r.get("canal_recommande", ""),
                "confiance_marche": int(r.get("confiance") or 0),
            })
            n_ok += 1
            progress.progress(step / len(indices))
        progress.empty()
        st.session_state["mk_detail"]["articles"] = articles
        st.success(f"{n_ok}/{len(indices)} articles enrichis.")
        st.rerun()


# ---------------------------------------------------------------------------
# Table articles editable (frais remise) + detail article + resume lot
# ---------------------------------------------------------------------------
def _render_articles_section(articles: list[dict]) -> None:
    """Rendu complet de la section articles : table + detail + resume."""
    if not articles:
        st.info("Aucun article — uploade le manifeste CSV.")
        return

    st.markdown("**Tableau articles**")

    # --- Filtres ---
    with st.expander("Filtres", expanded=False):
        c1, c2, c3 = st.columns(3)
        etats = sorted({a.get("condition", "") for a in articles if a.get("condition")})
        canaux = sorted({a.get("canal_recommande", "") for a in articles if a.get("canal_recommande")})
        f_etat = c1.multiselect("Etat", etats, default=etats, key="mk_f_etat")
        f_canal = c2.multiselect("Canal", canaux, default=canaux, key="mk_f_canal")
        f_score = c3.slider("Score ROI min", 1, 5, 1, key="mk_f_score")

    # --- Construction du DataFrame editable ---
    rows = []
    for i, a in enumerate(articles):
        if f_etat and a.get("condition", "") not in f_etat:
            continue
        if f_canal and a.get("canal_recommande", "") not in f_canal:
            continue
        if a.get("score_roi", 0) < f_score:
            continue
        rows.append({
            "_idx": i,
            "Description": (a.get("description") or "")[:60],
            "Etat": a.get("condition", ""),
            "Categorie": a.get("categorie", ""),
            "Retail (EUR)": round(a.get("retail_price", 0), 2),
            "Cout reel (EUR)": round(a.get("cout_reel", 0), 2),
            "Frais remise (EUR)": round(a.get("frais_remise", 0), 2),
            "Prix cible (EUR)": round(a.get("prix_cible", 0), 2),
            "Benef net (EUR)": round(a.get("benef_net", 0), 2),
            "Marge %": round(a.get("marge_estimee", 0), 1),
            "Score": a.get("score_roi", 0),
            "Canal": a.get("canal_recommande", ""),
        })
    if not rows:
        st.warning("Aucun article ne correspond aux filtres.")
        return

    df = pd.DataFrame(rows)

    edited = st.data_editor(
        df.drop(columns=["_idx"]),
        use_container_width=True,
        hide_index=True,
        disabled=[c for c in df.columns if c not in ("Frais remise (EUR)",)],
        column_config={
            "Retail (EUR)":       st.column_config.NumberColumn(format="%.0f"),
            "Cout reel (EUR)":    st.column_config.NumberColumn(format="%.0f"),
            "Frais remise (EUR)": st.column_config.NumberColumn(format="%.0f", min_value=0, max_value=500),
            "Prix cible (EUR)":   st.column_config.NumberColumn(format="%.0f"),
            "Benef net (EUR)":    st.column_config.NumberColumn(format="%.0f"),
            "Marge %":            st.column_config.NumberColumn(format="%.1f"),
            "Score":              st.column_config.NumberColumn(format="%d"),
        },
        key="mk_articles_editor",
    )

    # Recalcul apres edition des frais de remise
    if edited is not None:
        for row_idx in range(len(edited)):
            orig_idx = df.iloc[row_idx]["_idx"]
            new_frais = float(edited.iloc[row_idx]["Frais remise (EUR)"])
            art = articles[orig_idx]
            if abs(new_frais - art.get("frais_remise", 0)) > 0.01:
                art["frais_remise"] = new_frais
                cout_t = art["cout_reel"] + new_frais
                art["benef_net"] = round(art["prix_cible"] - cout_t, 2)
                art["marge_estimee"] = round(art["benef_net"] / cout_t * 100, 2) if cout_t > 0 else 0

    st.caption(f"{len(rows)}/{len(articles)} articles affiches.")
    st.divider()

    # --- Detail article ---
    _render_article_detail(articles, df)
    st.divider()

    # --- Resume lot ---
    _render_lot_summary(articles)


def _render_article_detail(articles: list[dict], df_visible: pd.DataFrame) -> None:
    """Panneau detail d'un article selectionne + recherche prix marche."""
    st.markdown("**Detail article**")
    labels = [f"{int(r['_idx']+1)}. {r['Description']}" for _, r in df_visible.iterrows()]
    choix = st.selectbox("Selectionner un article", ["-"] + labels, key="mk_art_select")
    if choix == "-":
        return

    idx = int(df_visible.iloc[labels.index(choix)]["_idx"])
    a = articles[idx]

    # A. Infos article
    st.markdown(f"**{a.get('description', '')}**")
    c1, c2 = st.columns(2)
    with c1:
        asin = a.get("asin", "")
        st.markdown(f"ASIN : **{asin}**" + (f"  [Voir sur Amazon.fr](https://www.amazon.fr/dp/{asin})" if asin else ""))
        cond = a.get("condition", "")
        expl = {
            "Warehouse Damage": "Carton abime en entrepot, produit souvent comme neuf",
            "Customer Damage": "Retour client, etat variable (teste/ouvert/usage)",
            "Carrier Damage": "Dommage transport, souvent superficiel",
            "Defective": "En panne ou incomplet, reparation ou pieces detachees",
        }
        txt_expl = ""
        for k, v in expl.items():
            if k.lower() in cond.lower():
                txt_expl = v
                break
        st.markdown(f"Etat : **{cond}** — _{txt_expl}_" if txt_expl else f"Etat : **{cond}**")
        st.markdown(f"Categorie : **{a.get('categorie', '')}** / {a.get('sous_categorie', '')}")
    with c2:
        st.metric("Retail B-Stock", f"{a.get('retail_price', 0):,.2f} EUR")
        st.metric("Cout reel", f"{a.get('cout_reel', 0):,.2f} EUR")
        st.metric("Frais remise en etat", f"{a.get('frais_remise', 0):,.0f} EUR")
        st.metric("Prix cible", f"{a.get('prix_cible', 0):,.2f} EUR")
        benef = a.get("benef_net", 0)
        color = "normal" if benef >= 0 else "inverse"
        st.metric("Benefice net estime", f"{benef:,.2f} EUR", delta=f"{a.get('marge_estimee',0):.1f}%", delta_color=color)

    # B. Prix marche en temps reel
    st.markdown("---")
    if st.button("Rechercher prix marche", key=f"mk_art_prix_{idx}", use_container_width=True):
        with st.spinner("Interrogation Amazon / LBC / eBay..."):
            try:
                r = prix_marche.analyser_article(
                    asin=a.get("asin", ""),
                    ean=a.get("ean", ""),
                    description=a.get("description", ""),
                    condition=a.get("condition", ""),
                )
            except Exception as exc:
                st.error(f"Erreur : {exc}")
                return
            st.session_state[f"mk_art_marche_{idx}"] = r

    marche = st.session_state.get(f"mk_art_marche_{idx}")
    if marche:
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("Amazon.fr", f"{marche.get('prix_amazon', 0):,.2f} EUR",
                    help=marche.get("erreurs", {}).get("amazon", ""))
        mc2.metric("LBC median", f"{marche.get('prix_lbc_median', 0):,.2f} EUR",
                    help=f"{marche.get('nb_lbc_annonces', 0)} annonces")
        mc3.metric("eBay vendus median", f"{marche.get('prix_ebay_vendus', 0):,.2f} EUR",
                    help=f"{marche.get('nb_ebay_vendus', 0)} ventes")

        prix_c = marche.get("prix_cible_calcule", 0)
        if prix_c > 0:
            cout_t = a.get("cout_reel", 0) + a.get("frais_remise", 0)
            benef_m = round(prix_c - cout_t, 2)
            st.info(
                f"Prix cible recalcule (marche) : **{prix_c:,.2f} EUR** | "
                f"Benefice : **{benef_m:,.2f} EUR** | "
                f"Canal recommande : **{marche.get('canal_recommande', '')}** | "
                f"Confiance : **{marche.get('confiance', 0)}/3**"
            )

        # C. Annonces similaires (LBC + eBay)
        # On relance les scrapers individuels pour avoir les annonces
        from scrapers import ebay as ebay_scraper, leboncoin as lbc_scraper

        query = (a.get("description") or a.get("asin", ""))[:60]
        if query:
            with st.expander("Annonces similaires LBC / eBay", expanded=False):
                tab_lbc, tab_ebay = st.tabs(["Le Bon Coin", "eBay"])
                with tab_lbc:
                    lbc_data = lbc_scraper.get_lbc_prices(query, nb_resultats=5)
                    if lbc_data.get("annonces"):
                        for ann in lbc_data["annonces"][:5]:
                            c_t, c_p, c_l = st.columns([3, 1, 1])
                            c_t.markdown(f"**{ann.get('titre', '')[:60]}**")
                            c_p.markdown(f"**{ann.get('prix', 0):,.0f} EUR**")
                            url_ann = ann.get("url", "")
                            if url_ann:
                                c_l.link_button("Voir", url_ann)
                    else:
                        st.caption(f"Aucune annonce trouvee. {lbc_data.get('erreur', '')}")
                with tab_ebay:
                    ebay_data = ebay_scraper.get_ebay_prices(query)
                    if ebay_data.get("annonces"):
                        for ann in ebay_data["annonces"][:5]:
                            c_t, c_p, c_l = st.columns([3, 1, 1])
                            c_t.markdown(f"**{ann.get('titre', '')[:60]}**")
                            c_p.markdown(f"**{ann.get('prix', 0):,.0f} EUR**")
                            url_ann = ann.get("url", "")
                            if url_ann:
                                c_l.link_button("Voir", url_ann)
                    else:
                        st.caption(f"Aucune annonce trouvee. {ebay_data.get('erreur', '')}")


def _render_lot_summary(articles: list[dict]) -> None:
    """Recapitulatif financier du lot en bas de la section articles."""
    st.markdown("**Recapitulatif du lot**")

    # Stats par condition
    conditions: dict[str, int] = {}
    for a in articles:
        c = a.get("condition", "Inconnu")
        conditions[c] = conditions.get(c, 0) + 1
    parts = " | ".join(f"{k}: {v}" for k, v in sorted(conditions.items(), key=lambda x: -x[1]))
    st.caption(f"**{len(articles)} articles** — {parts}")

    # Finances
    cout_total = sum(a.get("cout_reel", 0) + a.get("frais_remise", 0) for a in articles)
    ca_potentiel = sum(a.get("prix_cible", 0) for a in articles)
    benef_total = sum(a.get("benef_net", 0) for a in articles)
    marge_totale = round(benef_total / cout_total * 100, 2) if cout_total > 0 else 0.0
    a_risque = sum(1 for a in articles if a.get("benef_net", 0) < 0)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Cout total (+ remise)", f"{cout_total:,.0f} EUR")
    c2.metric("CA potentiel", f"{ca_potentiel:,.0f} EUR")
    c3.metric("Benefice net total", f"{benef_total:,.0f} EUR")
    c4.metric("Marge nette", f"{marge_totale:.1f}%")
    c5.metric("Articles a risque", f"{a_risque}")

    # Top 5 articles par benefice
    top5 = sorted(articles, key=lambda a: a.get("benef_net", 0), reverse=True)[:5]
    if top5:
        st.markdown("**Top 5 articles par benefice estime**")
        for i, a in enumerate(top5, 1):
            st.caption(
                f"{i}. {(a.get('description') or '')[:50]} — "
                f"Benef: **{a.get('benef_net',0):,.0f} EUR** "
                f"(marge {a.get('marge_estimee',0):.0f}%) — {a.get('condition','')}"
            )

    # Articles a risque (marge < 0)
    if a_risque:
        risques = [a for a in articles if a.get("benef_net", 0) < 0][:5]
        st.markdown("**Articles a risque (benefice negatif)**")
        for a in risques:
            st.caption(
                f"- {(a.get('description') or '')[:50]} — "
                f"Perte: **{a.get('benef_net',0):,.0f} EUR** — "
                f"{a.get('condition','')}"
            )


# ---------------------------------------------------------------------------
# Actions lot
# ---------------------------------------------------------------------------
def _render_actions(lot: dict, articles: list[dict]) -> None:
    st.markdown("**Decision**")
    c1, c2, c3 = st.columns(3)
    with c1:
        url_lot = lot.get("url", "")
        if url_lot:
            st.link_button("Encherir sur B-Stock", url_lot, use_container_width=True)
    with c2:
        if st.button("Surveiller", use_container_width=True):
            try:
                _persist_lot(lot, articles, "surveillance", False)
                st.success("Lot en surveillance.")
            except Exception as exc:
                st.error(str(exc))
    with c3:
        if st.button("Importer en stock", use_container_width=True, type="primary"):
            st.session_state["mk_show_import_form"] = True

    # Formulaire d'import avec saisie des vrais couts
    if st.session_state.get("mk_show_import_form"):
        _render_import_form(lot, articles)


def _render_import_form(lot: dict, articles: list[dict]) -> None:
    """
    Confirmation d'import en stock.
    Les couts viennent de la Partie 1 (session_state) et sont affiches
    en lecture seule. L'import est bloque si l'enchere n'a pas ete saisie.
    """
    from modules.stock import importer_lot_en_stock

    lot_id = lot.get("lot_id") or bstock._extract_lot_id(lot.get("url", ""))
    st.markdown("---")
    st.markdown("**Importer ce lot en stock**")

    # Recupere les vrais couts saisis dans la Partie 1
    enchere = float(st.session_state.get("mk_enchere_user", 0))
    frais_supp = float(st.session_state.get("mk_frais_supp_user", 0))
    livraison = float(st.session_state.get("mk_livraison_user", 0))
    frais_bstock_pct = float(get_param("business_frais_bstock_pct", "5") or 5)
    frais_bstock = round(enchere * frais_bstock_pct / 100, 2)
    cout_total = round(enchere + frais_bstock + frais_supp + livraison, 2)

    # Blocage si Partie 1 pas remplie
    if enchere <= 0:
        st.warning(
            "Saisissez d'abord votre enchere reelle dans la Partie 1 "
            "(champ 'Votre enchere max') avant d'importer."
        )
        return

    # Affichage en lecture seule des couts confirmes
    st.info(
        f"Enchere payee : **{enchere:,.2f} EUR** | "
        f"Frais B-Stock : **{frais_bstock:,.2f} EUR** | "
        f"Frais supp : **{frais_supp:,.2f} EUR** | "
        f"Livraison : **{livraison:,.2f} EUR** | "
        f"**TOTAL : {cout_total:,.2f} EUR**"
    )

    nom_lot = st.text_input(
        "Nom du lot",
        value=lot.get("titre", "") or lot_id,
        key="mk_import_nom_lot",
    )

    if st.button("Confirmer l'import en stock", use_container_width=True, type="primary", key="mk_confirm_import"):
        if not lot_id:
            st.error("Impossible de determiner l'ID du lot.")
            return

        # Recalcule le cout_reel par article avec le vrai cout total
        retail_total = sum(a.get("retail_price", 0) for a in articles) or 1
        for a in articles:
            ratio = a.get("retail_price", 0) / retail_total
            a["cout_reel"] = round(ratio * cout_total, 2)

        try:
            n_ok, n_dup = importer_lot_en_stock(
                lot_id=lot_id,
                articles=articles,
                url=lot.get("url", ""),
                enchere=enchere,
                frais_bstock=frais_bstock,
                frais_supp=frais_supp,
                livraison=livraison,
                retail_total=float(lot.get("retail_total", 0) or 0),
                titre=nom_lot,
            )
            st.session_state.pop("mk_show_import_form", None)
            st.success(
                f"Lot importe en stock : {n_ok} articles inseres"
                + (f", {n_dup} doublons ignores" if n_dup else "")
                + f". Cout total : {cout_total:,.2f} EUR."
                + " Allez dans Stock & Articles pour gerer."
            )
        except Exception as exc:
            st.error(f"Erreur import : {type(exc).__name__}: {exc}")


def _persist_lot(lot: dict, articles: list[dict], statut: str, with_articles: bool) -> int:
    lot_id = lot.get("lot_id") or bstock._extract_lot_id(lot.get("url", ""))
    if not lot_id:
        raise ValueError("lot_id introuvable")
    frais_pct = float(get_param("business_frais_bstock_pct", "5") or 5)
    session = get_session()
    try:
        row = session.query(Lot).filter_by(lot_id=lot_id).first()
        if row is None:
            row = Lot(lot_id=lot_id)
            session.add(row)
        row.url_bstock = lot.get("url", "")
        row.statut = statut
        row.montant_enchere = float(lot.get("enchere", 0) or 0)
        row.frais_bstock_pct = frais_pct
        row.frais_livraison = float(lot.get("frais_livraison", 0) or 0)
        row.cout_total = float(lot.get("cout_total", 0) or 0)
        row.retail_total = float(lot.get("retail_total", 0) or 0)
        row.nb_articles = int(lot.get("nb_articles") or len(articles))
        row.notes = (lot.get("titre") or "")[:500]
        n_art = 0
        if with_articles:
            for a in articles:
                lpn = a.get("lpn") or None
                if lpn and session.query(Article).filter_by(lpn=lpn).first():
                    continue
                session.add(Article(
                    lot_id=lot_id, lpn=lpn,
                    asin=a.get("asin", ""), ean=a.get("ean", ""),
                    description=a.get("description", ""),
                    condition=a.get("condition", ""),
                    categorie=a.get("categorie", ""),
                    sous_categorie=a.get("sous_categorie", ""),
                    retail_price=float(a.get("retail_price", 0) or 0),
                    cout_reel=float(a.get("cout_reel", 0) or 0),
                    prix_amazon=float(a.get("prix_marche_amazon", 0) or 0),
                    prix_lbc=float(a.get("prix_marche_lbc", 0) or 0),
                    prix_ebay=float(a.get("prix_marche_ebay", 0) or 0),
                    prix_cible=float(a.get("prix_cible_marche") or a.get("prix_cible") or 0),
                    marge_estimee=float(a.get("marge_estimee", 0) or 0),
                    marge_reelle=float(a.get("marge_reelle") or a.get("marge_estimee") or 0),
                    score_roi=int(a.get("score_roi", 0) or 0),
                    canal_recommande=a.get("canal_recommande_marche") or a.get("canal_recommande", ""),
                    statut="en_stock",
                ))
                n_art += 1
        session.commit()
        return n_art
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Entree principale
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("Marketplace B-Stock")
    tab_lots, tab_detail = st.tabs(["Lots disponibles", "Analyser un lot"])
    with tab_lots:
        _tab_lots_disponibles()
    with tab_detail:
        _tab_analyser_lot()
