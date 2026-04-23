# -*- coding: utf-8 -*-
"""
DeStock App - modules/lots.py
Gestion simplifiee des lots : liste, creation, import articles.
"""

from __future__ import annotations

from datetime import datetime, date

import pandas as pd
import streamlit as st

from database import Article, Lot, Vente, get_session


# ---------------------------------------------------------------------------
# Coefficients prix cible selon l'etat
# ---------------------------------------------------------------------------
COEFFS_ETAT = {
    "warehouse damage": 0.65,
    "customer damage":  0.45,
    "carrier damage":   0.50,
    "defective":        0.30,
}
DEFAULT_COEFF = 0.45
PRIX_MIN = 5.0


def _coeff_for(condition: str) -> float:
    cond = (condition or "").strip().lower()
    for k, v in COEFFS_ETAT.items():
        if k in cond:
            return v
    return DEFAULT_COEFF


def _calc_prix_cible(retail: float, condition: str) -> float:
    return max(round(retail * _coeff_for(condition), 2), PRIX_MIN)


def _map_etat_emoji(val: str) -> str:
    """Mappe les libelles Excel (avec emojis) vers les conditions standard."""
    s = str(val or "").strip().lower()
    if "client" in s:
        return "Customer Damage"
    if "entrepot" in s or "entrepôt" in s or "warehouse" in s:
        return "Warehouse Damage"
    if "defect" in s or "défect" in s:
        return "Defective"
    if "transport" in s or "carrier" in s:
        return "Carrier Damage"
    return "Customer Damage"


# ---------------------------------------------------------------------------
# Helpers DB
# ---------------------------------------------------------------------------
def _load_lots_stats() -> list[dict]:
    session = get_session()
    try:
        lots = session.query(Lot).order_by(Lot.id.desc()).all()
        result = []
        for l in lots:
            arts = session.query(Article).filter_by(lot_id=l.lot_id).all()
            arts_ids = [a.id for a in arts]
            ventes = (
                session.query(Vente)
                .filter(Vente.article_id.in_(arts_ids))
                .all() if arts_ids else []
            )
            ca = sum(v.prix_vente or 0 for v in ventes)
            frais_total = l.cout_total or 0
            benef = ca - frais_total
            result.append({
                "lot_id": l.lot_id,
                "nom": l.notes or l.lot_id,
                "date_achat": l.date_enchere,
                "enchere": l.montant_enchere or 0,
                "frais_total": frais_total,
                "nb_articles": len(arts),
                "nb_vendus": sum(1 for a in arts if a.statut == "vendu"),
                "ca_encaisse": ca,
                "benefice": benef,
            })
        return result
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Section A — Liste des lots
# ---------------------------------------------------------------------------
def _section_liste() -> None:
    st.markdown("### Mes lots")
    lots = _load_lots_stats()
    if not lots:
        st.info("Aucun lot. Creez-en un ci-dessous.")
        return

    def _go_to_stock(lot_id: str) -> None:
        """Callback : change la page active et pre-selectionne le lot."""
        st.session_state["lot_selectionne"] = lot_id
        st.session_state["sidebar_nav"] = "Mon stock"

    for l in lots:
        with st.container():
            c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1, 1])
            date_str = l["date_achat"].strftime("%d/%m/%Y") if l["date_achat"] else "-"
            c1.markdown(f"**{l['nom']}**")
            c1.caption(f"Lot {l['lot_id']} | Achat {date_str}")
            c2.metric("Investi", f"{l['frais_total']:,.0f} EUR")
            c3.metric("Articles", f"{l['nb_vendus']}/{l['nb_articles']}")
            c4.metric("CA encaisse", f"{l['ca_encaisse']:,.0f} EUR")
            benef = l["benefice"]
            delta_color = "normal" if benef >= 0 else "inverse"
            c5.metric("Benefice", f"{benef:,.0f} EUR", delta_color=delta_color)
            st.button(
                "Voir le stock",
                key=f"lot_view_{l['lot_id']}",
                use_container_width=True,
                on_click=_go_to_stock,
                args=(l["lot_id"],),
            )
            st.divider()


# ---------------------------------------------------------------------------
# Section B — Creer un lot
# ---------------------------------------------------------------------------
def _section_creer() -> None:
    st.markdown("### Ajouter un lot")

    with st.form("form_create_lot", clear_on_submit=False):
        nom = st.text_input(
            "Nom du lot",
            placeholder="BX2PL_073 - 4 palettes Floorcare",
        )
        col1, col2 = st.columns(2)
        date_achat = col1.date_input("Date d'achat", value=date.today())
        enchere = col2.number_input("Enchere payee (EUR)", min_value=0.0, step=50.0, value=0.0)

        col3, col4 = st.columns(2)
        frais_bstock_auto = round(enchere * 0.05, 2)
        col3.metric("Frais B-Stock (5%)", f"{frais_bstock_auto:,.2f} EUR")
        frais_bstock_manual = col4.number_input(
            "Frais B-Stock (ajustable)",
            min_value=0.0, step=10.0,
            value=float(frais_bstock_auto),
        )

        col5, col6 = st.columns(2)
        frais_supp = col5.number_input("Frais supplementaires (EUR)", min_value=0.0, step=50.0, value=0.0)
        livraison = col6.number_input("Frais livraison (EUR)", min_value=0.0, step=50.0, value=0.0)

        notes = st.text_area("Notes (optionnel)", value="")

        cout_total = round(enchere + frais_bstock_manual + frais_supp + livraison, 2)
        st.markdown(f"### TOTAL INVESTI : **{cout_total:,.2f} EUR**")

        submitted = st.form_submit_button("Creer le lot", use_container_width=True, type="primary")

    if submitted:
        if not nom:
            st.warning("Donnez un nom au lot.")
            return
        # Genere un lot_id depuis le nom (slug simple)
        import re as _re
        lot_id = _re.sub(r"[^A-Za-z0-9_-]+", "_", nom)[:100]
        session = get_session()
        try:
            if session.query(Lot).filter_by(lot_id=lot_id).first():
                lot_id = f"{lot_id}_{int(datetime.utcnow().timestamp())}"
            session.add(Lot(
                lot_id=lot_id,
                statut="recu",
                montant_enchere=enchere,
                frais_bstock_pct=5.0,
                frais_livraison=livraison,
                tva=frais_supp,
                cout_total=cout_total,
                notes=(notes or nom)[:500],
                date_enchere=datetime(date_achat.year, date_achat.month, date_achat.day),
                date_reception=datetime.utcnow(),
            ))
            session.commit()
            st.session_state["lot_cree_id"] = lot_id
            st.success(f"Lot '{nom}' cree avec un investissement de {cout_total:,.2f} EUR.")
            st.rerun()
        finally:
            session.close()


# ---------------------------------------------------------------------------
# Section C — Importer les articles
# ---------------------------------------------------------------------------
def _section_import() -> None:
    # Selectbox lot auquel rattacher les articles
    st.markdown("### Importer les articles")

    session = get_session()
    try:
        lots_rows = session.query(Lot).order_by(Lot.id.desc()).all()
        lots_list = [{"id": l.lot_id, "nom": l.notes or l.lot_id, "cout": l.cout_total or 0}
                     for l in lots_rows]
    finally:
        session.close()

    if not lots_list:
        st.info("Creez d'abord un lot ci-dessus.")
        return

    # Pre-selectionne le lot qu'on vient de creer
    pre_id = st.session_state.get("lot_cree_id", "")
    labels = [f"{l['nom']} ({l['id']})" for l in lots_list]
    default_idx = 0
    if pre_id:
        for i, l in enumerate(lots_list):
            if l["id"] == pre_id:
                default_idx = i
                break
    choix = st.selectbox("Lot cible", labels, index=default_idx, key="import_lot_select")
    lot_choisi = lots_list[labels.index(choix)]

    uploaded = st.file_uploader(
        "Fichier Excel ou CSV",
        type=["xlsx", "xls", "csv"],
        key="import_file",
    )
    if uploaded is None:
        return

    if st.button("Importer les articles", use_container_width=True, type="primary"):
        try:
            articles = _parse_uploaded(uploaded, lot_choisi)
            if not articles:
                st.error("Aucun article detecte dans le fichier.")
                return
            n_ok, n_dup = _insert_articles(lot_choisi["id"], articles, lot_choisi["cout"])
            st.success(
                f"{n_ok} articles importes pour {lot_choisi['nom']} "
                f"(cout unitaire : {lot_choisi['cout'] / max(n_ok, 1):.2f} EUR/article)."
                + (f" {n_dup} doublons ignores." if n_dup else "")
            )
            st.session_state.pop("lot_cree_id", None)
            st.rerun()
        except Exception as exc:
            st.error(f"Erreur import : {type(exc).__name__}: {exc}")


def _parse_uploaded(uploaded, lot: dict) -> list[dict]:
    """Parse un fichier Excel ou CSV. Retourne liste d'articles bruts."""
    name = uploaded.name.lower()
    if name.endswith(".csv"):
        return _parse_csv(uploaded, lot)
    return _parse_excel(uploaded)


def _parse_excel(uploaded) -> list[dict]:
    """Parse un Excel avec onglet DETAIL ARTICLES, header ligne 4 (index 3)."""
    try:
        xl = pd.ExcelFile(uploaded)
    except Exception as exc:
        raise RuntimeError(f"Impossible de lire l'Excel : {exc}")

    # Cherche l'onglet "DETAIL ARTICLES" (case-insensitive)
    sheet_name = None
    for s in xl.sheet_names:
        if "detail" in s.lower() and "article" in s.lower():
            sheet_name = s
            break
    if sheet_name is None:
        sheet_name = xl.sheet_names[0]  # fallback 1er onglet

    df = pd.read_excel(uploaded, sheet_name=sheet_name, header=3)
    articles = []
    for idx, row in df.iterrows():
        try:
            rang = int(row.iloc[0]) if pd.notna(row.iloc[0]) else idx + 1
            desc = str(row.iloc[2]).strip() if pd.notna(row.iloc[2]) else ""
            etat_raw = str(row.iloc[3]) if pd.notna(row.iloc[3]) else ""
            retail = float(row.iloc[4]) if pd.notna(row.iloc[4]) else 0.0
            if not desc or desc.lower() == "nan":
                continue
            import re as _re
            desc_clean = _re.sub(r"\*\*", "", desc).strip()
            articles.append({
                "rang": rang,
                "description": desc_clean[:500],
                "condition": _map_etat_emoji(etat_raw),
                "retail_price": retail,
                "asin": "",
                "ean": "",
                "categorie": "",
                "sous_categorie": "",
            })
        except Exception:
            continue
    return articles


def _parse_csv(uploaded, lot: dict) -> list[dict]:
    """Parse un CSV via le scraper bstock existant."""
    from scrapers.bstock import parse_manifest, DOWNLOADS_DIR
    lot_id = lot["id"]
    csv_path = DOWNLOADS_DIR / f"import_{lot_id}_{int(datetime.utcnow().timestamp())}.csv"
    csv_path.write_bytes(uploaded.getvalue())
    lot_dict = {"lot_id": lot_id, "cout_total": lot["cout"]}
    raw = parse_manifest(csv_path, lot_dict)
    # Normalise au format attendu par _insert_articles
    articles = []
    for i, a in enumerate(raw, 1):
        articles.append({
            "rang": i,
            "description": a.get("description", ""),
            "condition": a.get("condition", ""),
            "retail_price": a.get("retail_price", 0),
            "asin": a.get("asin", ""),
            "ean": a.get("ean", ""),
            "categorie": a.get("categorie", ""),
            "sous_categorie": a.get("sous_categorie", ""),
        })
    return articles


def _insert_articles(lot_id: str, articles: list[dict], cout_total: float) -> tuple[int, int]:
    """Insere les articles en base. Retourne (nb_ok, nb_doublons)."""
    n = len(articles) or 1
    cout_unitaire = round(cout_total / n, 2)
    session = get_session()
    n_ok = 0
    n_dup = 0
    try:
        for a in articles:
            lpn = f"{lot_id}_ART_{a['rang']:03d}"
            if session.query(Article).filter_by(lpn=lpn).first():
                n_dup += 1
                continue
            prix_cible = _calc_prix_cible(a["retail_price"], a["condition"])
            session.add(Article(
                lot_id=lot_id,
                lpn=lpn,
                asin=a.get("asin", ""),
                ean=a.get("ean", ""),
                description=a.get("description", ""),
                condition=a.get("condition", ""),
                categorie=a.get("categorie", ""),
                sous_categorie=a.get("sous_categorie", ""),
                retail_price=a.get("retail_price", 0),
                cout_reel=cout_unitaire,
                prix_cible=prix_cible,
                prix_affiche=prix_cible,
                teste_neuf=0,
                statut="en_stock",
                date_reception=datetime.utcnow(),
            ))
            n_ok += 1
        session.commit()
    finally:
        session.close()
    return n_ok, n_dup


# ---------------------------------------------------------------------------
# Entree principale
# ---------------------------------------------------------------------------
def render() -> None:
    st.markdown(
        """
        <div class='module-header'>
          <div class='module-title'>Mes lots</div>
          <div class='module-subtitle'>Suivi de tous vos achats B-Stock — creation, import, stats</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Stats globales
    lots_stats = _load_lots_stats()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Lots actifs", len(lots_stats))
    m2.metric("Total investi", f"{sum(l['frais_total'] for l in lots_stats):,.0f} EUR")
    m3.metric("CA encaisse", f"{sum(l['ca_encaisse'] for l in lots_stats):,.0f} EUR")
    benef_tot = sum(l["benefice"] for l in lots_stats)
    m4.metric("Benefice net", f"{benef_tot:,.0f} EUR",
               delta_color="normal" if benef_tot >= 0 else "inverse")
    st.divider()

    _section_liste()
    st.divider()
    _section_creer()
    st.divider()
    _section_import()
