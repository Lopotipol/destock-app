# -*- coding: utf-8 -*-
"""
DeStock App - modules/parametres.py
Page de configuration complete de l'application.

Toutes les valeurs sont persistees dans la table `parametres` (cle/valeur).
Les helpers `get_param` / `set_param` assurent la lecture/ecriture.
"""

import streamlit as st

from auth import change_password, current_user_id, current_user_nom
from config import CATEGORIES_BSTOCK, STATUTS_JURIDIQUES
from database import Parametre, get_session


# ---------------------------------------------------------------------------
# Helpers generiques de lecture/ecriture des parametres
# ---------------------------------------------------------------------------
def get_param(cle: str, defaut: str = "") -> str:
    session = get_session()
    try:
        row = session.query(Parametre).filter_by(cle=cle).first()
        return row.valeur if row and row.valeur is not None else defaut
    finally:
        session.close()


def set_param(cle: str, valeur: str) -> None:
    session = get_session()
    try:
        row = session.query(Parametre).filter_by(cle=cle).first()
        if row is None:
            row = Parametre(cle=cle, valeur=valeur)
            session.add(row)
        else:
            row.valeur = valeur
        session.commit()
    finally:
        session.close()


def _as_float(texte: str, defaut: float = 0.0) -> float:
    try:
        return float(str(texte).replace(",", "."))
    except (ValueError, TypeError):
        return defaut


def _as_int(texte: str, defaut: int = 0) -> int:
    try:
        return int(float(str(texte).replace(",", ".")))
    except (ValueError, TypeError):
        return defaut


# ---------------------------------------------------------------------------
# Sections de l'UI
# ---------------------------------------------------------------------------
def _section_profil_juridique() -> None:
    st.subheader("Profil juridique")

    statut_actuel = get_param("profil_statut", "Auto-entrepreneur")
    if statut_actuel not in STATUTS_JURIDIQUES:
        statut_actuel = "Auto-entrepreneur"

    with st.form("form_profil", clear_on_submit=False):
        statut = st.selectbox(
            "Statut juridique",
            STATUTS_JURIDIQUES,
            index=STATUTS_JURIDIQUES.index(statut_actuel),
        )
        ca_annuel = st.number_input(
            "CA annuel cumule (auto-entrepreneur) - EUR",
            min_value=0.0,
            value=_as_float(get_param("profil_ca_annuel", "0")),
            step=100.0,
        )
        taux_tva = st.number_input(
            "Taux de TVA applicable (%)",
            min_value=0.0,
            max_value=100.0,
            value=_as_float(get_param("profil_taux_tva", "0")),
            step=0.5,
        )
        if st.form_submit_button("Enregistrer le profil", use_container_width=True):
            set_param("profil_statut", statut)
            set_param("profil_ca_annuel", str(ca_annuel))
            set_param("profil_taux_tva", str(taux_tva))
            st.success("Profil juridique mis a jour.")


def _section_parametres_business() -> None:
    st.subheader("Parametres business")

    canaux_actifs = [c for c in get_param("business_canaux_actifs", "LBC,Vinted,eBay").split(",") if c]
    categories_actuelles = [c for c in get_param("business_categories_surveillees", "").split(",") if c]

    with st.form("form_business", clear_on_submit=False):
        frais_bstock = st.number_input(
            "Frais B-Stock (%)",
            min_value=0.0,
            max_value=100.0,
            value=_as_float(get_param("business_frais_bstock_pct", "5")),
            step=0.5,
        )
        seuil_stock_mort = st.number_input(
            "Seuil d'alerte stock mort (en jours)",
            min_value=1,
            max_value=365,
            value=_as_int(get_param("business_seuil_stock_mort_jours", "30")),
            step=1,
        )
        marge_min = st.number_input(
            "Marge minimale cible (%)",
            min_value=0.0,
            max_value=500.0,
            value=_as_float(get_param("business_marge_min_pct", "20")),
            step=1.0,
        )

        st.markdown("**Canaux de revente actifs**")
        col_lbc, col_vinted, col_ebay = st.columns(3)
        canal_lbc = col_lbc.checkbox("Le Bon Coin", value="LBC" in canaux_actifs)
        canal_vinted = col_vinted.checkbox("Vinted", value="Vinted" in canaux_actifs)
        canal_ebay = col_ebay.checkbox("eBay", value="eBay" in canaux_actifs)

        categories = st.multiselect(
            "Categories B-Stock surveillees",
            options=CATEGORIES_BSTOCK,
            default=[c for c in categories_actuelles if c in CATEGORIES_BSTOCK],
        )

        if st.form_submit_button("Enregistrer les parametres business", use_container_width=True):
            canaux = []
            if canal_lbc:
                canaux.append("LBC")
            if canal_vinted:
                canaux.append("Vinted")
            if canal_ebay:
                canaux.append("eBay")

            set_param("business_frais_bstock_pct", str(frais_bstock))
            set_param("business_seuil_stock_mort_jours", str(seuil_stock_mort))
            set_param("business_marge_min_pct", str(marge_min))
            set_param("business_canaux_actifs", ",".join(canaux))
            set_param("business_categories_surveillees", ",".join(categories))
            st.success("Parametres business mis a jour.")


def _section_connexions_api() -> None:
    st.subheader("Connexions & API")
    st.caption("Ces identifiants sont stockes dans la base locale. "
               "Le .env reste prioritaire au premier chargement.")

    with st.form("form_api", clear_on_submit=False):
        st.markdown("**B-Stock**")
        bstock_email = st.text_input("Email B-Stock", value=get_param("api_bstock_email", ""))
        bstock_password = st.text_input(
            "Mot de passe B-Stock",
            value=get_param("api_bstock_password", ""),
            type="password",
        )

        st.markdown("**Telegram**")
        telegram_token = st.text_input(
            "Token du bot Telegram",
            value=get_param("api_telegram_token", ""),
            type="password",
        )
        telegram_chat = st.text_input(
            "Chat ID Telegram",
            value=get_param("api_telegram_chat_id", ""),
        )

        st.markdown("**Anthropic (generation d'annonces)**")
        anthropic_key = st.text_input(
            "Cle API Anthropic",
            value=get_param("api_anthropic_key", ""),
            type="password",
        )

        st.markdown("**eBay**")
        ebay_key = st.text_input(
            "Cle API eBay",
            value=get_param("api_ebay_key", ""),
            type="password",
        )

        if st.form_submit_button("Enregistrer les connexions", use_container_width=True):
            set_param("api_bstock_email", bstock_email)
            set_param("api_bstock_password", bstock_password)
            set_param("api_telegram_token", telegram_token)
            set_param("api_telegram_chat_id", telegram_chat)
            set_param("api_anthropic_key", anthropic_key)
            set_param("api_ebay_key", ebay_key)
            st.success("Connexions mises a jour.")

    # Setup du profil Chrome B-Stock (connexion manuelle initiale)
    st.markdown("---")
    st.markdown("**Profil Chrome B-Stock**")

    # Import tardif pour eviter de charger Playwright au demarrage de l'app
    from scrapers import bstock as bstock_scraper

    if bstock_scraper.is_profile_configured():
        st.success("Profil sauvegarde - connexion automatique active.")
    else:
        st.warning("Profil non configure - le scraping B-Stock ne fonctionnera pas.")

    col_setup, col_reset = st.columns(2)
    with col_setup:
        if st.button(
            "Configurer connexion B-Stock (premiere fois)",
            use_container_width=True,
        ):
            st.info(
                "Un navigateur va s'ouvrir. Connectez-vous manuellement a B-Stock. "
                "Vous avez 120 secondes. Ne fermez pas le navigateur."
            )
            with st.spinner("Ouverture du navigateur... Connecte-toi dans la fenetre Chrome."):
                try:
                    ok, message = bstock_scraper.setup_profile(timeout_seconds=120)
                    if ok:
                        st.success(f"Profil sauvegarde - connexion automatique active. {message}")
                    else:
                        st.error(message)
                except Exception as e:
                    st.error(f"Erreur : {type(e).__name__}: {str(e)}")
                    import traceback
                    st.code(traceback.format_exc())

    with col_reset:
        if st.button(
            "Reinitialiser le profil",
            use_container_width=True,
            help="Supprime le profil Chrome B-Stock. Une nouvelle connexion manuelle sera necessaire.",
        ):
            ok, message = bstock_scraper.reset_profile()
            (st.success if ok else st.error)(message)
            if ok:
                st.rerun()


def _section_compte() -> None:
    st.subheader("Compte")
    st.info(f"Connecte en tant que **{current_user_nom()}**")

    with st.form("form_password", clear_on_submit=True):
        ancien = st.text_input("Ancien mot de passe", type="password")
        nouveau = st.text_input("Nouveau mot de passe", type="password")
        confirm = st.text_input("Confirmer le nouveau mot de passe", type="password")
        if st.form_submit_button("Changer le mot de passe", use_container_width=True):
            if nouveau != confirm:
                st.error("Les deux mots de passe ne correspondent pas.")
            else:
                ok, message = change_password(current_user_id(), ancien, nouveau)
                (st.success if ok else st.error)(message)


# ---------------------------------------------------------------------------
# Section : Scoring & Filtres lots
# ---------------------------------------------------------------------------
LOCALISATIONS_POSSIBLES = ["PL", "DE", "ES", "FR", "IT", "NL", "BE", "UK"]
LOCALISATIONS_DEFAUT = ["PL", "DE", "ES", "FR", "IT", "NL", "BE"]

CATEGORIES_SCORING = [
    "Kitchen", "Electronics", "Sports & Outdoors", "Toys Kids & Baby",
    "Health & Beauty", "Home & Garden", "Major Appliances",
    "Office Supplies", "Automotive", "Mixed Lots", "Apparel",
]

# Cles de parametres de scoring en base (prefixe "scoring_")
_SCORING_DEFAULTS = {
    "scoring_budget_max": "1000",
    "scoring_nb_articles_min": "50",
    "scoring_nb_articles_max": "1000",
    "scoring_ratio_max_pct": "35",
    "scoring_localisations": ",".join(LOCALISATIONS_DEFAUT),
    "scoring_exclure_uk": "1",
    "scoring_poids_ratio": "30",
    "scoring_poids_condition": "25",
    "scoring_poids_categorie": "20",
    "scoring_poids_cout_moyen": "15",
    "scoring_poids_localisation": "10",
    "scoring_ratio_excellent": "10",
    "scoring_ratio_bon": "20",
    "scoring_ratio_passable": "35",
    "scoring_cond_warehouse": "100",
    "scoring_cond_customer": "70",
    "scoring_cond_carrier": "75",
    "scoring_cond_defective": "30",
    "scoring_cout_ideal_min": "5",
    "scoring_cout_ideal_max": "50",
    "scoring_cout_acceptable_max": "150",
    "scoring_categories_preferees": "",
    "scoring_poids_volume_retail": "15",
    "scoring_volume_excellent": "50000",
    "scoring_volume_bon": "20000",
    "scoring_volume_passable": "5000",
    "scoring_toutes_categories": "0",
    "scoring_bonus_amazon": "1",
}


def get_scoring_params() -> dict:
    """
    Retourne un dict de TOUS les parametres de scoring, convertis dans le bon type.
    Utilise en interne par parametres.py et exporte pour marketplace.py (calculate_score).
    """
    raw = {}
    for cle, defaut in _SCORING_DEFAULTS.items():
        raw[cle] = get_param(cle, defaut)

    def _f(k):
        try:
            return float(raw.get(k, "0") or "0")
        except (ValueError, TypeError):
            return 0.0

    def _i(k):
        return int(_f(k))

    locs_str = raw.get("scoring_localisations", "")
    locs = [l.strip() for l in locs_str.split(",") if l.strip()]
    cats_str = raw.get("scoring_categories_preferees", "")
    cats = [c.strip() for c in cats_str.split(",") if c.strip()]

    return {
        "budget_max":            _f("scoring_budget_max"),
        "nb_articles_min":       _i("scoring_nb_articles_min"),
        "nb_articles_max":       _i("scoring_nb_articles_max"),
        "ratio_max_pct":         _f("scoring_ratio_max_pct"),
        "localisations":         locs,
        "exclure_uk":            raw.get("scoring_exclure_uk") == "1",
        "poids_ratio":           _i("scoring_poids_ratio"),
        "poids_condition":       _i("scoring_poids_condition"),
        "poids_categorie":       _i("scoring_poids_categorie"),
        "poids_cout_moyen":      _i("scoring_poids_cout_moyen"),
        "poids_localisation":    _i("scoring_poids_localisation"),
        "ratio_excellent":       _f("scoring_ratio_excellent"),
        "ratio_bon":             _f("scoring_ratio_bon"),
        "ratio_passable":        _f("scoring_ratio_passable"),
        "cond_warehouse":        _f("scoring_cond_warehouse"),
        "cond_customer":         _f("scoring_cond_customer"),
        "cond_carrier":          _f("scoring_cond_carrier"),
        "cond_defective":        _f("scoring_cond_defective"),
        "cout_ideal_min":        _f("scoring_cout_ideal_min"),
        "cout_ideal_max":        _f("scoring_cout_ideal_max"),
        "cout_acceptable_max":   _f("scoring_cout_acceptable_max"),
        "categories_preferees":  cats,
        "toutes_categories":     raw.get("scoring_toutes_categories") == "1",
        "poids_volume_retail":   _i("scoring_poids_volume_retail"),
        "volume_excellent":      _f("scoring_volume_excellent"),
        "volume_bon":            _f("scoring_volume_bon"),
        "volume_passable":       _f("scoring_volume_passable"),
        "bonus_amazon":          raw.get("scoring_bonus_amazon") == "1",
    }


def _section_scoring() -> None:
    st.subheader("Scoring & Filtres lots")

    with st.form("form_scoring", clear_on_submit=False):

        # --- FILTRES DISQUALIFIANTS ---
        st.markdown("**Filtres disqualifiants**")
        st.caption(
            "Un lot qui depasse un de ces seuils est automatiquement exclu "
            "(badge rouge, affiche en bas du tableau)."
        )
        c1, c2 = st.columns(2)
        budget = c1.number_input(
            "Budget max par lot (EUR)",
            min_value=0.0, step=100.0,
            value=_as_float(get_param("scoring_budget_max", "1000"), 1000.0),
            help="Ex : 1000 EUR = on ne regarde pas les lots au-dela de 1000 EUR d'enchere",
        )
        ratio_max = c2.slider(
            "Ratio decote max acceptable (%)",
            min_value=1, max_value=80,
            value=_as_int(get_param("scoring_ratio_max_pct", "35"), 35),
            help="Ex : 35% = on exclut un lot si son cout depasse 35% du prix retail Amazon",
        )
        c3, c4 = st.columns(2)
        nb_min = c3.number_input("Nb articles minimum", 1, step=10,
                                  value=_as_int(get_param("scoring_nb_articles_min", "50"), 50))
        nb_max = c4.number_input("Nb articles maximum", 1, step=100,
                                  value=_as_int(get_param("scoring_nb_articles_max", "1000"), 1000))

        saved_locs = [l for l in get_param("scoring_localisations", ",".join(LOCALISATIONS_DEFAUT)).split(",") if l.strip()]
        locs = st.multiselect("Localisations acceptees", LOCALISATIONS_POSSIBLES,
                               default=[l for l in saved_locs if l in LOCALISATIONS_POSSIBLES])
        exclure_uk = st.toggle("Exclure UK", value=get_param("scoring_exclure_uk", "1") == "1")
        st.caption("UK exclu par defaut : droits de douane post-Brexit + TVA import + delais.")
        st.divider()

        # --- POIDS DES CRITERES ---
        st.markdown("**Poids des criteres**")
        st.caption(
            "Chaque critere a un poids (nombre de points sur 100). "
            "Plus le poids est eleve, plus ce critere compte dans la note finale. "
            "La somme doit faire 100."
        )
        p1, p2, p3 = st.columns(3)
        poids_ratio = p1.slider("Ratio decote", 0, 50,
                                 _as_int(get_param("scoring_poids_ratio", "30"), 30),
                                 help="Combien on paye vs prix Amazon. Le critere roi.")
        poids_categorie = p2.slider("Categories", 0, 30,
                                     _as_int(get_param("scoring_poids_categorie", "15"), 15),
                                     help="Bonus si le lot est dans une categorie qu'on revend bien.")
        poids_volume = p3.slider("Volume retail", 0, 30,
                                  _as_int(get_param("scoring_poids_volume_retail", "15"), 15),
                                  help="Plus le lot a une valeur retail elevee, plus il y a de potentiel.")
        p4, p5, p6 = st.columns(3)
        poids_cout = p4.slider("Cout moyen/article", 0, 20,
                                _as_int(get_param("scoring_poids_cout_moyen", "15"), 15),
                                help="Le cout par article. Trop cher = risque, trop cheap = pas de marge.")
        poids_loc = p5.slider("Localisation", 0, 20,
                               _as_int(get_param("scoring_poids_localisation", "10"), 10),
                               help="PL/DE = facile. UK = galere douane.")
        poids_condition = p6.slider("Condition articles", 0, 50,
                                     _as_int(get_param("scoring_poids_condition", "15"), 15),
                                     help="Applique quand le CSV est uploade (Warehouse > Customer > Defective).")
        somme = poids_ratio + poids_categorie + poids_volume + poids_cout + poids_loc + poids_condition
        if somme == 100:
            st.success(f"Somme des poids : {somme}/100")
        else:
            st.warning(f"Somme des poids : {somme}/100 (attendu 100)")
        st.divider()

        # --- SEUILS RATIO DECOTE ---
        st.markdown("**Seuils ratio decote**")
        st.caption(
            "Le ratio decote = ce que tu payes par rapport au prix retail Amazon. "
            "Ex : lot a 500 EUR pour 10 000 EUR de retail = ratio 5% = Excellent. "
            "Un lot a 3 000 EUR pour 10 000 EUR = ratio 30% = Passable."
        )
        s1, s2, s3 = st.columns(3)
        r_excellent = s1.number_input("Excellent si ratio < %", 1, 50, _as_int(get_param("scoring_ratio_excellent", "10"), 10))
        r_bon = s2.number_input("Bon si ratio < %", 1, 60, _as_int(get_param("scoring_ratio_bon", "20"), 20))
        r_passable = s3.number_input("Passable si ratio < %", 1, 80, _as_int(get_param("scoring_ratio_passable", "35"), 35))
        st.divider()

        # --- SEUILS VOLUME RETAIL ---
        st.markdown("**Seuils volume retail**")
        st.caption(
            "La valeur retail totale du lot = le potentiel de revente. "
            "Un lot a 80 000 EUR de retail = enorme potentiel. "
            "Un lot a 3 000 EUR = petit lot, marge limitee."
        )
        v1, v2, v3 = st.columns(3)
        vol_exc = v1.number_input("Excellent si retail > EUR", 0, 500000,
                                   _as_int(get_param("scoring_volume_excellent", "50000"), 50000), step=5000)
        vol_bon = v2.number_input("Bon si retail > EUR", 0, 200000,
                                   _as_int(get_param("scoring_volume_bon", "20000"), 20000), step=5000)
        vol_pas = v3.number_input("Passable si retail > EUR", 0, 100000,
                                   _as_int(get_param("scoring_volume_passable", "5000"), 5000), step=1000)
        st.divider()

        # --- SEUILS CONDITION ARTICLES ---
        st.markdown("**Score par condition article**")
        st.caption(
            "Quand le manifeste CSV est uploade, chaque article a un etat. "
            "Ce score definit le % de points accordes par etat :\n\n"
            "- **Warehouse Damage** = carton abime en entrepot, produit souvent comme neuf --> 100%\n"
            "- **Carrier Damage** = colis abime pendant le transport, souvent superficiel --> 75%\n"
            "- **Customer Damage** = retour client, etat variable (teste, ouvert, usage) --> 70%\n"
            "- **Defective** = article en panne, a reparer ou pieces detachees --> 30%"
        )
        d1, d2 = st.columns(2)
        cond_wh = d1.slider("Warehouse Damage %", 0, 100, _as_int(get_param("scoring_cond_warehouse", "100"), 100))
        cond_cu = d2.slider("Customer Damage %", 0, 100, _as_int(get_param("scoring_cond_customer", "70"), 70))
        d3, d4 = st.columns(2)
        cond_ca = d3.slider("Carrier Damage %", 0, 100, _as_int(get_param("scoring_cond_carrier", "75"), 75))
        cond_de = d4.slider("Defective %", 0, 100, _as_int(get_param("scoring_cond_defective", "30"), 30))
        st.divider()

        # --- SEUILS COUT MOYEN ---
        st.markdown("**Seuils cout moyen par article**")
        st.caption(
            "Le cout moyen = enchere / nb articles. "
            "Ex : lot a 100 EUR pour 500 articles = 0.20 EUR/article (tres bien). "
            "Lot a 1000 EUR pour 50 articles = 20 EUR/article (plus risque)."
        )
        m1, m2, m3 = st.columns(3)
        cout_id_min = m1.number_input("Ideal min (EUR)", 0.0, 500.0, _as_float(get_param("scoring_cout_ideal_min", "5"), 5.0), step=1.0)
        cout_id_max = m2.number_input("Ideal max (EUR)", 0.0, 1000.0, _as_float(get_param("scoring_cout_ideal_max", "50"), 50.0), step=5.0)
        cout_ac_max = m3.number_input("Acceptable max (EUR)", 0.0, 2000.0, _as_float(get_param("scoring_cout_acceptable_max", "150"), 150.0), step=10.0)
        st.divider()

        # --- CATEGORIES PREFEREES ---
        st.markdown("**Categories preferees**")
        st.caption(
            "Si le lot contient une de tes categories preferees, il gagne des points. "
            "Active 'Toutes' si tu ne veux pas filtrer par categorie."
        )
        toutes_cats = st.toggle("Toutes les categories (ignore ce critere)",
                                 value=get_param("scoring_toutes_categories", "0") == "1")
        saved_cats = [c for c in get_param("scoring_categories_preferees", "").split(",") if c.strip()]
        cats_pref = st.multiselect("Categories", CATEGORIES_SCORING,
                                    default=[c for c in saved_cats if c in CATEGORIES_SCORING],
                                    disabled=toutes_cats)
        st.divider()

        # --- BONUS / MALUS ---
        st.markdown("**Bonus / Malus**")
        bonus_amazon = st.toggle(
            "Bonus Amazon EU (+10 pts)",
            value=get_param("scoring_bonus_amazon", "1") == "1",
            help="Les lots Amazon EU sont bien documentes et fiables. +10 pts.",
        )
        st.caption("Malus UK : -20 pts automatique (douane + TVA import + delais).")

        # --- SAUVEGARDE ---
        if st.form_submit_button("Enregistrer le scoring", use_container_width=True):
            set_param("scoring_budget_max", str(budget))
            set_param("scoring_nb_articles_min", str(int(nb_min)))
            set_param("scoring_nb_articles_max", str(int(nb_max)))
            set_param("scoring_ratio_max_pct", str(ratio_max))
            set_param("scoring_localisations", ",".join(locs))
            set_param("scoring_exclure_uk", "1" if exclure_uk else "0")
            set_param("scoring_poids_ratio", str(poids_ratio))
            set_param("scoring_poids_condition", str(poids_condition))
            set_param("scoring_poids_categorie", str(poids_categorie))
            set_param("scoring_poids_cout_moyen", str(poids_cout))
            set_param("scoring_poids_localisation", str(poids_loc))
            set_param("scoring_poids_volume_retail", str(poids_volume))
            set_param("scoring_ratio_excellent", str(r_excellent))
            set_param("scoring_ratio_bon", str(r_bon))
            set_param("scoring_ratio_passable", str(r_passable))
            set_param("scoring_volume_excellent", str(vol_exc))
            set_param("scoring_volume_bon", str(vol_bon))
            set_param("scoring_volume_passable", str(vol_pas))
            set_param("scoring_cond_warehouse", str(cond_wh))
            set_param("scoring_cond_customer", str(cond_cu))
            set_param("scoring_cond_carrier", str(cond_ca))
            set_param("scoring_cond_defective", str(cond_de))
            set_param("scoring_cout_ideal_min", str(cout_id_min))
            set_param("scoring_cout_ideal_max", str(cout_id_max))
            set_param("scoring_cout_acceptable_max", str(cout_ac_max))
            set_param("scoring_categories_preferees", ",".join(cats_pref))
            set_param("scoring_toutes_categories", "1" if toutes_cats else "0")
            set_param("scoring_bonus_amazon", "1" if bonus_amazon else "0")
            st.success("Parametres de scoring mis a jour.")


# ---------------------------------------------------------------------------
# Section : Templates annonces
# ---------------------------------------------------------------------------
def _section_templates() -> None:
    from database import Template, get_session as _gs, seed_templates, _DEFAULT_TEMPLATES

    st.subheader("Templates annonces")
    st.caption(
        "Editez les templates utilises par la generation d'annonces. "
        "Variables disponibles : {marque} {modele} {retail} {prix_cible} "
        "{asin} {condition} {commentaire_reception} {categorie} {jours_stock}"
    )

    filtre_canal = st.selectbox("Filtrer par canal", ["Tous", "LBC", "eBay", "Vinted"], key="tpl_filtre")

    session = _gs()
    try:
        q = session.query(Template).order_by(Template.canal, Template.condition)
        if filtre_canal != "Tous":
            q = q.filter_by(canal=filtre_canal)
        templates = q.all()
        tpl_data = [
            {"id": t.id, "canal": t.canal, "condition": t.condition,
             "nom": t.nom, "titre": t.template_titre, "desc": t.template_description}
            for t in templates
        ]
    finally:
        session.close()

    if not tpl_data:
        st.info("Aucun template. Cliquez 'Reinitialiser' pour charger les templates par defaut.")

    for t in tpl_data:
        with st.expander(f"{t['nom']}", expanded=False):
            new_titre = st.text_input("Titre", value=t["titre"], key=f"tpl_t_{t['id']}")
            new_desc = st.text_area("Description", value=t["desc"], height=180, key=f"tpl_d_{t['id']}")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Sauvegarder", key=f"tpl_save_{t['id']}", use_container_width=True):
                    s = _gs()
                    try:
                        row = s.query(Template).filter_by(id=t["id"]).first()
                        if row:
                            row.template_titre = new_titre
                            row.template_description = new_desc
                            row.date_modif = __import__("datetime").datetime.utcnow()
                            s.commit()
                            st.success(f"Template '{t['nom']}' sauvegarde.")
                    finally:
                        s.close()
            with c2:
                if st.button("Remettre par defaut", key=f"tpl_reset_{t['id']}", use_container_width=True):
                    # Trouve le template par defaut correspondant
                    for canal, cond, nom, titre_d, desc_d in _DEFAULT_TEMPLATES:
                        if canal == t["canal"] and cond == t["condition"]:
                            s = _gs()
                            try:
                                row = s.query(Template).filter_by(id=t["id"]).first()
                                if row:
                                    row.template_titre = titre_d
                                    row.template_description = desc_d
                                    s.commit()
                                    st.success(f"Template '{t['nom']}' reinitialise.")
                                    st.rerun()
                            finally:
                                s.close()
                            break

    st.divider()
    if st.button("Reinitialiser TOUS les templates", use_container_width=True, key="tpl_reset_all"):
        s = _gs()
        try:
            s.query(Template).delete()
            s.commit()
        finally:
            s.close()
        seed_templates()
        st.success("Tous les templates reinitialises aux valeurs par defaut.")
        st.rerun()


# ---------------------------------------------------------------------------
# Entree principale du module
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("Parametres")

    tab_profil, tab_business, tab_api, tab_compte, tab_scoring, tab_tpl = st.tabs(
        ["Profil juridique", "Business", "Connexions & API", "Compte", "Scoring & Filtres", "Templates annonces"]
    )
    with tab_profil:
        _section_profil_juridique()
    with tab_business:
        _section_parametres_business()
    with tab_api:
        _section_connexions_api()
    with tab_compte:
        _section_compte()
    with tab_scoring:
        _section_scoring()
    with tab_tpl:
        _section_templates()
