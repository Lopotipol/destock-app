# -*- coding: utf-8 -*-
"""
DeStock App - modules/annonces.py
Generation d'annonces via templates + suivi des annonces publiees.

Deux onglets :
  1. Generer une annonce  : selection article, templates par canal, checklist photos
  2. Mes annonces         : tableau des annonces publiees, actions
"""

from __future__ import annotations

import re as _re
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

from database import Annonce, Article, Template, get_session


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
CANAUX = ["LBC", "Vinted", "eBay"]

CONDITION_FR = {
    "warehouse damage": "Comme neuf",
    "customer damage":  "Bon etat",
    "carrier damage":   "Etat correct",
    "defective":        "Defectueux",
}

CHECKLIST_PHOTOS: dict[str, list[str]] = {
    "robot": [
        "Face avant du robot",
        "Base / station dock",
        "Brosse principale",
        "Bac a poussiere",
        "Accessoires inclus",
        "Defaut visible (obligatoire)",
        "Cable alimentation",
    ],
    "aspirateur_balai": [
        "Complet assemble",
        "Tete brosse",
        "Filtre",
        "Batterie / chargeur",
        "Defaut visible (obligatoire)",
    ],
    "cafetiere": [
        "Face avant",
        "Reservoir eau",
        "Groupe cafe / filtre",
        "Buse vapeur si dispo",
        "Defaut visible (obligatoire)",
        "Cable",
    ],
    "autre": [
        "Face avant",
        "Face arriere",
        "Defaut visible (obligatoire)",
        "Accessoires inclus",
        "Cable / chargeur",
    ],
}


# ---------------------------------------------------------------------------
# Bouton copier (JavaScript inline)
# ---------------------------------------------------------------------------
def _copy_button(element_id: str, text: str, label: str) -> None:
    """Affiche un bouton HTML qui copie le texte dans le presse-papier."""
    safe_text = text.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$").replace('"', "&quot;").replace("\n", "\\n")
    components.html(f"""
    <textarea id="{element_id}" style="position:absolute;left:-9999px">{text}</textarea>
    <button onclick="
      navigator.clipboard.writeText(`{safe_text}`).then(()=>{{
        this.innerText='Copie !';
        setTimeout(()=>this.innerText='{label}',2000)
      }}).catch(()=>{{
        var t=document.getElementById('{element_id}');
        t.select();document.execCommand('copy');
        this.innerText='Copie !';
        setTimeout(()=>this.innerText='{label}',2000)
      }})
    " style="
      background:#2563eb;color:white;
      border:none;padding:8px 16px;
      border-radius:6px;cursor:pointer;
      font-size:14px;margin:4px 0
    ">{label}</button>
    """, height=45)


# ---------------------------------------------------------------------------
# Helpers DB
# ---------------------------------------------------------------------------
def _load_articles_en_stock() -> list[dict]:
    session = get_session()
    try:
        rows = (
            session.query(Article)
            .filter(Article.statut.in_(["en_stock", "annonce_publiee"]))
            .all()
        )
        return [
            {
                "id": r.id,
                "lot_id": r.lot_id,
                "description": r.description or "",
                "condition": r.condition or "",
                "categorie": r.categorie or "",
                "sous_categorie": r.sous_categorie or "",
                "retail_price": r.retail_price or 0,
                "cout_reel": r.cout_reel or 0,
                "frais_remise": r.cout_reconditionnnement or 0,
                "prix_cible": r.prix_cible or 0,
                "asin": r.asin or "",
                "canal_recommande": r.canal_recommande or "",
                "statut": r.statut or "",
                "condition_reelle": r.condition_reelle or "",
                "commentaire_reception": r.commentaire_reception or "",
                "date_reception": r.date_reception,
            }
            for r in rows
        ]
    finally:
        session.close()


def _load_annonces() -> list[dict]:
    session = get_session()
    try:
        rows = session.query(Annonce).order_by(Annonce.date_publication.desc()).all()
        result = []
        for a in rows:
            art = session.query(Article).filter_by(id=a.article_id).first()
            result.append({
                "id": a.id,
                "article_id": a.article_id,
                "article_desc": (art.description or "")[:50] if art else "",
                "canal": a.canal or "",
                "titre": a.titre or "",
                "prix": a.prix,
                "lien": a.lien or "",
                "statut": a.statut or "",
                "date_publication": a.date_publication,
            })
        return result
    finally:
        session.close()


def _detect_photo_category(article: dict) -> str:
    txt = (
        (article.get("description") or "")
        + " " + (article.get("sous_categorie") or "")
        + " " + (article.get("categorie") or "")
    ).lower()
    if any(k in txt for k in ("robot", "roborock", "dreame", "roomba", "ecovacs")):
        return "robot"
    if any(k in txt for k in ("balai", "stick", "dyson v", "handheld")):
        return "aspirateur_balai"
    if any(k in txt for k in ("cafe", "coffee", "espresso", "nespresso", "kaffeevoll")):
        return "cafetiere"
    return "autre"


def _condition_fr(condition: str) -> str:
    """Traduit la condition B-Stock en francais."""
    cond = (condition or "").strip().lower()
    for k, v in CONDITION_FR.items():
        if k in cond:
            return v
    return condition or ""


# ---------------------------------------------------------------------------
# Extraction marque / modele
# ---------------------------------------------------------------------------
_STOP_WORDS = {
    # Allemand — noms techniques produits
    "saugroboter", "wischfunktion", "kabelloser", "staubsauger",
    "kaffeevollautomat", "kaffeemaschine", "roller", "aquaroll",
    "wischer", "teppichschutz", "rollenabdeckung", "aspirapolvere",
    "kuchenmaschine",
    # Allemand — mots de liaison
    "mit", "und", "fur", "fuer", "von",
    # Francais
    "avec", "pour", "sans", "par", "con", "per",
    "de", "du", "le", "la", "les", "des", "et",
    "aspirateur", "laveur", "fil",
    # Anglais
    "robot", "vacuum", "cleaner", "cordless", "automatic", "with",
    "the", "and",
    # Generiques (souvent apres le vrai modele)
    "complete", "advanced", "automatic", "series", "serie",
    "generation", "pro", "ultra", "plus", "max", "gen",
    "master", "omni",
}


def _extract_marque_modele(description: str) -> tuple[str, str]:
    """
    Extrait la marque (1er mot) et le modele (max 2 mots significatifs).
    Filtre les mots > 12 caracteres et les stop words (y compris sous-mots
    dans les mots composes avec tiret).
    """
    if not description:
        return "", ""
    clean = _re.sub(r"^[^a-zA-Z0-9']+", "", description.strip())
    words = _re.split(r"[\s,]+", clean)
    if not words:
        return "", ""
    marque = words[0]
    modele_parts: list[str] = []
    for w in words[1:8]:
        w_clean = w.strip("-").strip()
        if not w_clean or len(w_clean) <= 2:
            continue
        if len(w_clean) > 12:
            continue
        # Mot purement numerique (ex: "18000") -> skip
        if w_clean.isdigit():
            continue
        # Mot alphanumerique tres long (ex: "ECAM37295TB") -> garder si < 10 chars
        if _re.search(r"\d", w_clean) and _re.search(r"[a-zA-Z]", w_clean) and len(w_clean) >= 10:
            continue
        sub_parts = w_clean.split("-")
        if any(sp.strip().lower() in _STOP_WORDS for sp in sub_parts if sp.strip()):
            continue
        if w_clean.lower() in _STOP_WORDS:
            continue
        modele_parts.append(w_clean)
        if len(modele_parts) >= 2:
            break
    return marque, " ".join(modele_parts)


# ---------------------------------------------------------------------------
# Templates : chargement + rendu
# ---------------------------------------------------------------------------
def _get_template(canal: str, condition: str) -> dict | None:
    session = get_session()
    try:
        cond_key = (condition or "").strip().lower()
        rows = session.query(Template).filter_by(canal=canal).all()
        for r in rows:
            if r.condition.strip().lower() in cond_key or cond_key in r.condition.strip().lower():
                return {"id": r.id, "titre": r.template_titre, "description": r.template_description, "nom": r.nom}
        if rows:
            r = rows[0]
            return {"id": r.id, "titre": r.template_titre, "description": r.template_description, "nom": r.nom}
        return None
    finally:
        session.close()


def render_template(template_str: str, article: dict) -> str:
    """Remplace les variables {marque}, {modele}, etc. dans un template."""
    desc = article.get("description") or ""
    marque, modele = _extract_marque_modele(desc)
    commentaire = article.get("commentaire_reception") or "Aucun commentaire de reception"

    now = datetime.utcnow()
    date_rec = article.get("date_reception")
    jours = 0
    if date_rec:
        try:
            if isinstance(date_rec, str):
                date_rec = datetime.fromisoformat(date_rec)
            jours = (now - date_rec).days
        except Exception:
            pass

    replacements = {
        "{marque}": marque,
        "{modele}": modele,
        "{retail}": f"{article.get('retail_price', 0):,.0f}",
        "{prix_cible}": f"{article.get('prix_cible', 0):,.0f}",
        "{asin}": article.get("asin") or "",
        "{condition}": article.get("condition") or "",
        "{commentaire_reception}": commentaire,
        "{categorie}": article.get("categorie") or "",
        "{canal}": article.get("canal") or "",
        "{jours_stock}": str(jours),
    }
    result = template_str
    for key, val in replacements.items():
        result = result.replace(key, val)
    return result


def render_template_full(canal: str, article: dict) -> dict | None:
    tpl = _get_template(canal, article.get("condition", ""))
    if tpl is None:
        return None
    titre = render_template(tpl["titre"], article)
    desc = render_template(tpl["description"], article)
    return {"titre": titre, "description": desc, "prix_recommande": article.get("prix_cible", 0)}


# =========================================================================
# ONGLET 1 — Generer une annonce
# =========================================================================
def _tab_generer() -> None:
    st.markdown("**Selectionner un article**")
    articles = _load_articles_en_stock()
    if not articles:
        st.info("Aucun article en stock. Importez un lot depuis la Marketplace.")
        return

    labels = [
        f"#{a['id']} — {_extract_marque_modele(a['description'])[0]} "
        f"{_extract_marque_modele(a['description'])[1]} — {_condition_fr(a['condition'])}"
        for a in articles
    ]
    choix = st.selectbox("Article", ["-"] + labels, key="ann_art_select")
    if choix == "-":
        return

    art = articles[labels.index(choix)]
    marque, modele = _extract_marque_modele(art["description"])

    # Fiche article
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**{marque} {modele}**")
    c1.caption(f"Etat : {_condition_fr(art['condition'])} | Cat : {art['categorie']}")
    c2.metric("Prix cible", f"{art['prix_cible']:,.0f} EUR")
    c2.caption(f"Canal rec. : {art['canal_recommande']}")
    c3.metric("Cout reel", f"{art['cout_reel']:,.0f} EUR")
    benef = art["prix_cible"] - art["cout_reel"] - art.get("frais_remise", 0)
    c3.caption(f"Benefice est. : {benef:,.0f} EUR")

    st.divider()

    # --- Generation par template ---
    st.markdown("**Generer l'annonce**")
    col_lbc, col_vinted, col_ebay = st.columns(3)
    for canal in CANAUX:
        if st.button(f"Generer {canal}", use_container_width=True, key=f"ann_gen_{canal.lower()}"):
            r = render_template_full(canal, art)
            if r:
                st.session_state["ann_result"] = r
                st.session_state["ann_canal"] = canal
                st.session_state["ann_article_id"] = art["id"]
                # Sauvegarde auto en base (statut=generee)
                session = get_session()
                try:
                    session.add(Annonce(
                        article_id=art["id"], canal=canal,
                        titre=r["titre"], description=r["description"],
                        prix=float(r.get("prix_recommande") or art["prix_cible"]),
                        statut="generee", date_publication=datetime.utcnow(),
                    ))
                    session.commit()
                finally:
                    session.close()

    # --- Resultat genere ---
    result = st.session_state.get("ann_result")
    canal_gen = st.session_state.get("ann_canal", "")
    if result and not result.get("erreur"):
        st.divider()
        st.markdown(f"**Annonce {canal_gen}**")

        titre = st.text_input("Titre", value=result.get("titre", ""), key="ann_titre_edit")
        _copy_button("copy_titre", titre, "Copier le titre")
        desc = st.text_area("Description", value=result.get("description", ""), height=250, key="ann_desc_edit")
        _copy_button("copy_desc", desc, "Copier la description")
        prix_rec = result.get("prix_recommande", art["prix_cible"])
        if isinstance(prix_rec, str):
            try:
                prix_rec = float(str(prix_rec).replace(",", ".").replace(" ", "").replace("EUR", "").replace("€", ""))
            except ValueError:
                prix_rec = art["prix_cible"]
        prix = st.number_input("Prix (EUR)", min_value=0.0, value=float(prix_rec), step=5.0, key="ann_prix_edit")
        _copy_button("copy_prix", f"{prix:.0f}", "Copier le prix")

        # Checklist photos
        st.divider()
        cat_photo = _detect_photo_category(art)
        photos = CHECKLIST_PHOTOS.get(cat_photo, CHECKLIST_PHOTOS["autre"])
        st.markdown("**Checklist photos a prendre**")
        for photo in photos:
            st.checkbox(photo, key=f"ann_photo_{photo}")

        # Publier
        st.divider()
        st.markdown("**Publier l'annonce**")
        lien = st.text_input("Lien de l'annonce publiee", placeholder="https://www.leboncoin.fr/...", key="ann_lien_publi")
        if st.button("Marquer annonce publiee", use_container_width=True, type="primary", key="ann_publier"):
            art_id = st.session_state.get("ann_article_id")
            if not art_id:
                st.error("Aucun article selectionne.")
            else:
                session = get_session()
                try:
                    # Met a jour l'annonce existante (generee -> publiee)
                    existing = (
                        session.query(Annonce)
                        .filter_by(article_id=art_id, canal=canal_gen, statut="generee")
                        .order_by(Annonce.id.desc())
                        .first()
                    )
                    if existing:
                        existing.titre = titre
                        existing.description = desc
                        existing.prix = prix
                        existing.lien = lien
                        existing.statut = "publiee"
                    else:
                        session.add(Annonce(
                            article_id=art_id, canal=canal_gen, titre=titre,
                            description=desc, prix=prix, lien=lien,
                            statut="publiee", date_publication=datetime.utcnow(),
                        ))
                    row = session.query(Article).filter_by(id=art_id).first()
                    if row:
                        row.statut = "annonce_publiee"
                    session.commit()
                    st.success(f"Annonce {canal_gen} publiee.")
                    st.session_state.pop("ann_result", None)
                    st.rerun()
                finally:
                    session.close()


# =========================================================================
# ONGLET 2 — Mes annonces
# =========================================================================
def _tab_mes_annonces() -> None:
    annonces = _load_annonces()
    if not annonces:
        st.info("Aucune annonce enregistree.")
        return

    # Filtre par statut
    statut_f = st.radio("Statut", ["Toutes", "generee", "publiee", "vendue"], horizontal=True, key="ann_stat_f")
    if statut_f != "Toutes":
        annonces = [a for a in annonces if a["statut"] == statut_f]

    st.markdown(f"**{len(annonces)} annonces**")
    for a in annonces:
        with st.container():
            c1, c2, c3, c4 = st.columns([3, 1, 1, 2])
            c1.markdown(f"**{a['article_desc']}** — {a['canal']}")
            c2.markdown(f"**{a['prix']:,.0f} EUR**")
            badge = {"generee": "orange", "publiee": "green", "vendue": "gray"}.get(a["statut"], "blue")
            c3.markdown(f":{badge}[{a['statut']}]")
            if a.get("date_publication"):
                c4.caption(a["date_publication"].strftime("%d/%m/%Y"))
            actions = st.columns(4)
            with actions[0]:
                if a.get("lien"):
                    st.link_button("Voir", a["lien"], use_container_width=True)
            with actions[1]:
                # Bouton Publier (pour les generees)
                if a["statut"] == "generee":
                    if st.button("Publier", key=f"ann_pub_{a['id']}", use_container_width=True):
                        st.session_state[f"ann_pub_form_{a['id']}"] = True
            with actions[2]:
                if a["statut"] == "publiee":
                    if st.button("Marquer vendu", key=f"ann_vendu_{a['id']}", use_container_width=True):
                        session = get_session()
                        try:
                            row = session.query(Annonce).filter_by(id=a["id"]).first()
                            if row:
                                row.statut = "vendue"
                            session.commit()
                            st.rerun()
                        finally:
                            session.close()
            with actions[3]:
                if st.button("Supprimer", key=f"ann_del_{a['id']}", use_container_width=True):
                    session = get_session()
                    try:
                        session.query(Annonce).filter_by(id=a["id"]).delete()
                        session.commit()
                        st.rerun()
                    finally:
                        session.close()
            # Formulaire inline pour publier une annonce generee
            if st.session_state.get(f"ann_pub_form_{a['id']}"):
                lien_pub = st.text_input("Lien de l'annonce", key=f"ann_pub_lien_{a['id']}")
                if st.button("Confirmer publication", key=f"ann_pub_ok_{a['id']}"):
                    session = get_session()
                    try:
                        row = session.query(Annonce).filter_by(id=a["id"]).first()
                        if row:
                            row.statut = "publiee"
                            row.lien = lien_pub
                        art_row = session.query(Article).filter_by(id=a["article_id"]).first()
                        if art_row:
                            art_row.statut = "annonce_publiee"
                        session.commit()
                        st.session_state.pop(f"ann_pub_form_{a['id']}", None)
                        st.rerun()
                    finally:
                        session.close()
            st.divider()


# =========================================================================
# Entree principale
# =========================================================================
def render() -> None:
    st.title("Annonces")
    tab_gen, tab_list = st.tabs(["Generer une annonce", "Mes annonces"])
    with tab_gen:
        _tab_generer()
    with tab_list:
        _tab_mes_annonces()
