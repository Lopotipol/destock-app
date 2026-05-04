# -*- coding: utf-8 -*-
"""
DeStock App - modules/ebay_manager.py

Integration eBay : OAuth (Sandbox + Production), Inventory API, Fulfillment API,
generation de descriptions via Anthropic.

Stockage : modeles SQLAlchemy adosses au Base partage de database.py
=> compatibles SQLite (dev) et PostgreSQL (prod Render) automatiquement.
"""

from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import requests
import streamlit as st
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text

from database import Base, engine, get_session
import config


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
EBAY_SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/sell.marketing",
]

# Marketplaces eBay supportes (id, label)
EBAY_SITES = {
    "EBAY_FR": "France (eBay.fr)",
    "EBAY_DE": "Allemagne (eBay.de)",
    "EBAY_BE_FR": "Belgique FR (eBay.be)",
    "EBAY_IT": "Italie (eBay.it)",
    "EBAY_ES": "Espagne (eBay.es)",
}

# Quelques categories utiles (id eBay -> label) ; peut etre etendu
EBAY_CATEGORIES = {
    "9355":   "Telephones mobiles",
    "175672": "Casques audio",
    "139973": "Maison & cuisine",
    "11700":  "Jouets",
    "11450":  "Vetements",
    "293":    "Electronique grand public",
    "625":    "Photo & video",
    "171485": "Sport & loisirs",
    "11233":  "Beaute",
}

# Endpoints eBay (Sandbox vs Production)
_ENDPOINTS = {
    "sandbox": {
        "auth":  "https://auth.sandbox.ebay.com/oauth2/authorize",
        "token": "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
        "api":   "https://api.sandbox.ebay.com",
    },
    "prod": {
        "auth":  "https://auth.ebay.com/oauth2/authorize",
        "token": "https://api.ebay.com/identity/v1/oauth2/token",
        "api":   "https://api.ebay.com",
    },
}


# ---------------------------------------------------------------------------
# Modeles SQLAlchemy
# ---------------------------------------------------------------------------
class EbayConfig(Base):
    """Configuration eBay (singleton id=1) : env, tokens OAuth, policies, site."""
    __tablename__ = "ebay_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    environnement = Column(String(20), default="sandbox")  # sandbox / prod
    site_id = Column(String(20), default="EBAY_FR")
    redirect_uri = Column(String(255), default="")         # RuName configure dans eBay
    access_token = Column(Text, default="")
    refresh_token = Column(Text, default="")
    token_expiry = Column(DateTime, nullable=True)         # date d'expiration access_token
    refresh_expiry = Column(DateTime, nullable=True)       # date d'expiration refresh_token
    fulfillment_policy_id = Column(String(60), default="")
    payment_policy_id = Column(String(60), default="")
    return_policy_id = Column(String(60), default="")
    merchant_location_key = Column(String(60), default="default-location")
    oauth_state = Column(String(120), default="")          # anti-CSRF
    date_modif = Column(DateTime, default=datetime.utcnow)


class EbayListing(Base):
    """Annonces eBay publiees (lien article <-> offer/listing eBay)."""
    __tablename__ = "ebay_listings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False, index=True)
    sku = Column(String(60), unique=True, nullable=False)  # SKU envoye a eBay
    offer_id = Column(String(60), default="")              # offerId eBay
    listing_id = Column(String(60), default="")           # listingId publie
    titre = Column(Text, default="")
    prix = Column(Float, default=0.0)
    devise = Column(String(10), default="EUR")
    statut = Column(String(30), default="brouillon")       # brouillon / publie / vendu / supprime
    url = Column(Text, default="")
    date_creation = Column(DateTime, default=datetime.utcnow)
    date_publication = Column(DateTime, nullable=True)


class EbayVente(Base):
    """Ventes eBay synchronisees via Fulfillment API."""
    __tablename__ = "ebay_ventes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(60), unique=True, nullable=False)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=True)
    sku = Column(String(60), default="")
    prix_vente = Column(Float, default=0.0)
    frais_ebay = Column(Float, default=0.0)
    devise = Column(String(10), default="EUR")
    acheteur = Column(String(120), default="")
    statut = Column(String(30), default="paid")
    date_vente = Column(DateTime, default=datetime.utcnow)
    raw = Column(Text, default="")  # JSON brut pour debug


# ---------------------------------------------------------------------------
# Init tables
# ---------------------------------------------------------------------------
def init_ebay_tables() -> None:
    """Cree les tables eBay si absentes (compatible SQLite + PostgreSQL)."""
    Base.metadata.create_all(
        engine,
        tables=[EbayConfig.__table__, EbayListing.__table__, EbayVente.__table__],
    )
    # Singleton EbayConfig (id=1)
    session = get_session()
    try:
        cfg = session.query(EbayConfig).filter_by(id=1).first()
        if not cfg:
            session.add(EbayConfig(id=1, environnement="sandbox", site_id="EBAY_FR"))
            session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Acces config
# ---------------------------------------------------------------------------
def get_ebay_config() -> EbayConfig:
    """Retourne l'instance EbayConfig singleton (la cree au besoin)."""
    session = get_session()
    try:
        cfg = session.query(EbayConfig).filter_by(id=1).first()
        if not cfg:
            cfg = EbayConfig(id=1, environnement="sandbox", site_id="EBAY_FR")
            session.add(cfg)
            session.commit()
            session.refresh(cfg)
        session.expunge(cfg)
        return cfg
    finally:
        session.close()


def save_ebay_config(**fields) -> None:
    """Met a jour les champs fournis sur la config singleton."""
    session = get_session()
    try:
        cfg = session.query(EbayConfig).filter_by(id=1).first()
        if not cfg:
            cfg = EbayConfig(id=1)
            session.add(cfg)
        for key, value in fields.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        cfg.date_modif = datetime.utcnow()
        session.commit()
    finally:
        session.close()


def save_tokens(access_token: str, refresh_token: str,
                expires_in: int, refresh_expires_in: Optional[int] = None) -> None:
    """Enregistre les tokens OAuth recus de eBay."""
    now = datetime.utcnow()
    fields = {
        "access_token": access_token,
        "token_expiry": now + timedelta(seconds=int(expires_in) - 60),
    }
    if refresh_token:
        fields["refresh_token"] = refresh_token
    if refresh_expires_in:
        fields["refresh_expiry"] = now + timedelta(seconds=int(refresh_expires_in))
    save_ebay_config(**fields)


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------
def _credentials(env: str) -> tuple[str, str]:
    """Retourne (client_id, client_secret) selon l'environnement."""
    if env == "prod":
        return config.EBAY_CLIENT_ID_PROD, config.EBAY_CLIENT_SECRET_PROD
    return config.EBAY_CLIENT_ID_SANDBOX, config.EBAY_CLIENT_SECRET_SANDBOX


def _basic_auth(env: str) -> str:
    cid, secret = _credentials(env)
    raw = f"{cid}:{secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("utf-8")


def get_auth_url(redirect_uri: str, env: Optional[str] = None) -> str:
    """
    Genere l'URL de consentement OAuth eBay.
    `redirect_uri` doit correspondre au RuName configure dans le dashboard eBay.
    """
    cfg = get_ebay_config()
    env = env or cfg.environnement or "sandbox"
    client_id, _ = _credentials(env)
    state = secrets.token_urlsafe(24)
    save_ebay_config(oauth_state=state, redirect_uri=redirect_uri, environnement=env)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "scope": " ".join(EBAY_SCOPES),
    }
    return f"{_ENDPOINTS[env]['auth']}?{urlencode(params)}"


def exchange_code_for_token(code: str, redirect_uri: Optional[str] = None,
                             env: Optional[str] = None) -> dict:
    """Echange un code d'autorisation contre un access_token + refresh_token."""
    cfg = get_ebay_config()
    env = env or cfg.environnement or "sandbox"
    redirect_uri = redirect_uri or cfg.redirect_uri
    resp = requests.post(
        _ENDPOINTS[env]["token"],
        headers={
            "Authorization": _basic_auth(env),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    save_tokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_in=data.get("expires_in", 7200),
        refresh_expires_in=data.get("refresh_token_expires_in"),
    )
    return data


def refresh_access_token() -> Optional[str]:
    """
    Renouvelle l'access_token via le refresh_token stocke.
    Retourne le nouveau access_token ou None si echec.
    """
    cfg = get_ebay_config()
    if not cfg.refresh_token:
        return None
    env = cfg.environnement or "sandbox"
    resp = requests.post(
        _ENDPOINTS[env]["token"],
        headers={
            "Authorization": _basic_auth(env),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": cfg.refresh_token,
            "scope": " ".join(EBAY_SCOPES),
        },
        timeout=30,
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    save_tokens(
        access_token=data["access_token"],
        refresh_token=cfg.refresh_token,  # eBay renvoie pas toujours un nouveau
        expires_in=data.get("expires_in", 7200),
    )
    return data["access_token"]


def _valid_access_token() -> Optional[str]:
    """Retourne un access_token valide, en rafraichissant si necessaire."""
    cfg = get_ebay_config()
    if not cfg.access_token:
        return None
    if cfg.token_expiry and cfg.token_expiry > datetime.utcnow():
        return cfg.access_token
    return refresh_access_token()


def _api_headers(token: str, site_id: str = "EBAY_FR",
                  content_type: bool = True) -> dict:
    h = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": site_id,
        "Content-Language": "fr-FR",
    }
    if content_type:
        h["Content-Type"] = "application/json"
    return h


# ---------------------------------------------------------------------------
# Inventory API : publication
# ---------------------------------------------------------------------------
def create_ebay_listing(article, prix: float, categorie_id: str,
                         titre: Optional[str] = None,
                         description: Optional[str] = None,
                         images: Optional[list[str]] = None,
                         quantite: int = 1) -> dict:
    """
    Publie un article sur eBay via l'Inventory API.
    Etapes : createOrReplaceInventoryItem -> createOffer -> publishOffer.
    Retourne {"success", "listing_id", "offer_id", "url", "error"}.
    """
    token = _valid_access_token()
    if not token:
        return {"success": False, "error": "Token eBay manquant ou invalide."}
    cfg = get_ebay_config()
    env = cfg.environnement or "sandbox"
    base = _ENDPOINTS[env]["api"]
    site = cfg.site_id or "EBAY_FR"
    sku = f"DESTOCK-{article.id}-{int(datetime.utcnow().timestamp())}"
    titre = (titre or (article.description or ""))[:80]
    description = description or (article.description or "")
    images = images or []

    # 1) Inventory item
    inv_payload = {
        "availability": {"shipToLocationAvailability": {"quantity": quantite}},
        "condition": "USED_GOOD",
        "product": {
            "title": titre,
            "description": description,
            "imageUrls": images,
            "aspects": {},
        },
    }
    r = requests.put(
        f"{base}/sell/inventory/v1/inventory_item/{sku}",
        headers=_api_headers(token, site),
        json=inv_payload, timeout=30,
    )
    if r.status_code >= 300:
        return {"success": False, "error": f"inventory_item: {r.status_code} {r.text}"}

    # 2) Offer
    offer_payload = {
        "sku": sku,
        "marketplaceId": site,
        "format": "FIXED_PRICE",
        "availableQuantity": quantite,
        "categoryId": str(categorie_id),
        "listingDescription": description,
        "listingPolicies": {
            "fulfillmentPolicyId": cfg.fulfillment_policy_id,
            "paymentPolicyId": cfg.payment_policy_id,
            "returnPolicyId": cfg.return_policy_id,
        },
        "merchantLocationKey": cfg.merchant_location_key or "default-location",
        "pricingSummary": {"price": {"value": f"{prix:.2f}", "currency": "EUR"}},
    }
    r = requests.post(
        f"{base}/sell/inventory/v1/offer",
        headers=_api_headers(token, site),
        json=offer_payload, timeout=30,
    )
    if r.status_code >= 300:
        return {"success": False, "error": f"offer: {r.status_code} {r.text}"}
    offer_id = r.json().get("offerId", "")

    # 3) Publish
    r = requests.post(
        f"{base}/sell/inventory/v1/offer/{offer_id}/publish",
        headers=_api_headers(token, site), timeout=30,
    )
    if r.status_code >= 300:
        return {"success": False, "error": f"publish: {r.status_code} {r.text}"}
    listing_id = r.json().get("listingId", "")

    # Persistance
    session = get_session()
    try:
        session.add(EbayListing(
            article_id=article.id, sku=sku, offer_id=offer_id, listing_id=listing_id,
            titre=titre, prix=prix, statut="publie",
            url=f"https://www.ebay.fr/itm/{listing_id}" if env == "prod"
                else f"https://sandbox.ebay.com/itm/{listing_id}",
            date_publication=datetime.utcnow(),
        ))
        session.commit()
    finally:
        session.close()

    return {
        "success": True, "listing_id": listing_id, "offer_id": offer_id,
        "url": f"https://www.ebay.fr/itm/{listing_id}" if env == "prod"
               else f"https://sandbox.ebay.com/itm/{listing_id}",
    }


# ---------------------------------------------------------------------------
# Lectures API
# ---------------------------------------------------------------------------
def get_active_listings(limit: int = 50) -> list[dict]:
    """Liste les annonces actives (Inventory API getOffers)."""
    token = _valid_access_token()
    if not token:
        return []
    cfg = get_ebay_config()
    base = _ENDPOINTS[cfg.environnement or "sandbox"]["api"]
    r = requests.get(
        f"{base}/sell/inventory/v1/offer?limit={limit}",
        headers=_api_headers(token, cfg.site_id, content_type=False), timeout=30,
    )
    if r.status_code >= 300:
        return []
    return r.json().get("offers", [])


def get_recent_orders(days: int = 30) -> list[dict]:
    """Liste les commandes recentes via Fulfillment API."""
    token = _valid_access_token()
    if not token:
        return []
    cfg = get_ebay_config()
    base = _ENDPOINTS[cfg.environnement or "sandbox"]["api"]
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    flt = f"creationdate:[{since}..]"
    r = requests.get(
        f"{base}/sell/fulfillment/v1/order",
        params={"filter": flt, "limit": 100},
        headers=_api_headers(token, cfg.site_id, content_type=False), timeout=30,
    )
    if r.status_code >= 300:
        return []
    return r.json().get("orders", [])


# ---------------------------------------------------------------------------
# Merchant location (adresse d'expedition)
# ---------------------------------------------------------------------------
def create_merchant_location(
    location_key: str,
    nom: str,
    rue: str,
    ville: str,
    code_postal: str,
    pays: str = "FR",
    region: str = "",
    telephone: str = "",
) -> dict:
    """
    Cree (ou remplace) une location marchand sur eBay via l'Inventory API.
    `location_key` doit matcher le champ EbayConfig.merchant_location_key.
    Retourne {"success", "error"}.
    """
    token = _valid_access_token()
    if not token:
        return {"success": False, "error": "Token eBay manquant ou invalide."}
    cfg = get_ebay_config()
    base = _ENDPOINTS[cfg.environnement or "sandbox"]["api"]
    payload = {
        "location": {
            "address": {
                "addressLine1": rue,
                "city": ville,
                "stateOrProvince": region,
                "postalCode": code_postal,
                "country": pays,
            },
        },
        "locationInstructions": "Items ship from this location.",
        "name": nom,
        "phone": telephone,
        "merchantLocationStatus": "ENABLED",
        "locationTypes": ["WAREHOUSE"],
    }
    r = requests.post(
        f"{base}/sell/inventory/v1/location/{location_key}",
        headers=_api_headers(token, cfg.site_id),
        json=payload, timeout=30,
    )
    # eBay renvoie 204 si OK, 409 si la location existe deja
    if r.status_code in (200, 201, 204):
        save_ebay_config(merchant_location_key=location_key)
        return {"success": True}
    if r.status_code == 409:
        save_ebay_config(merchant_location_key=location_key)
        return {"success": True, "error": "Location deja existante (reutilisee)."}
    return {"success": False, "error": f"{r.status_code} {r.text}"}


# ---------------------------------------------------------------------------
# Test de connexion (validation policies + token + location)
# ---------------------------------------------------------------------------
def test_connection() -> dict:
    """
    Verifie : token valide, policies presentes, location accessible.
    Retourne {"ok": bool, "checks": [(label, ok, detail), ...]}.
    """
    checks = []
    cfg = get_ebay_config()
    base = _ENDPOINTS[cfg.environnement or "sandbox"]["api"]

    # 1) Cles API
    cid, secret = _credentials(cfg.environnement or "sandbox")
    checks.append(("Cles API (.env)", bool(cid and secret),
                    "OK" if (cid and secret) else "Manquantes dans .env"))

    # 2) Token
    token = _valid_access_token()
    checks.append(("Access token OAuth", bool(token),
                    "Valide" if token else "Absent ou expire — refaire l'autorisation"))
    if not token:
        return {"ok": False, "checks": checks}

    headers = _api_headers(token, cfg.site_id, content_type=False)

    # 3) Fulfillment policy
    if cfg.fulfillment_policy_id:
        r = requests.get(f"{base}/sell/account/v1/fulfillment_policy/"
                          f"{cfg.fulfillment_policy_id}",
                          headers=headers, timeout=20)
        checks.append(("Fulfillment policy", r.status_code == 200,
                        f"{r.status_code}"))
    else:
        checks.append(("Fulfillment policy", False, "Non configuree"))

    # 4) Payment policy
    if cfg.payment_policy_id:
        r = requests.get(f"{base}/sell/account/v1/payment_policy/"
                          f"{cfg.payment_policy_id}",
                          headers=headers, timeout=20)
        checks.append(("Payment policy", r.status_code == 200, f"{r.status_code}"))
    else:
        checks.append(("Payment policy", False, "Non configuree"))

    # 5) Return policy
    if cfg.return_policy_id:
        r = requests.get(f"{base}/sell/account/v1/return_policy/"
                          f"{cfg.return_policy_id}",
                          headers=headers, timeout=20)
        checks.append(("Return policy", r.status_code == 200, f"{r.status_code}"))
    else:
        checks.append(("Return policy", False, "Non configuree"))

    # 6) Merchant location
    loc_key = cfg.merchant_location_key or "default-location"
    r = requests.get(f"{base}/sell/inventory/v1/location/{loc_key}",
                      headers=headers, timeout=20)
    checks.append(("Merchant location", r.status_code == 200,
                    f"key='{loc_key}' — {r.status_code}"))

    ok = all(c[1] for c in checks)
    return {"ok": ok, "checks": checks}


# ---------------------------------------------------------------------------
# Generation de description (Anthropic)
# ---------------------------------------------------------------------------
def generate_ebay_description(article) -> str:
    """Genere une description eBay structuree via Claude."""
    api_key = config.ANTHROPIC_API_KEY
    if not api_key:
        return article.description or ""
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        prompt = (
            "Tu rediges une annonce eBay en francais pour un produit issu d'un lot "
            "de liquidation. Sois honnete sur l'etat, mets en avant les points forts, "
            "structure en HTML simple (titres + listes). Pas de texte avant/apres.\n\n"
            f"Description : {article.description}\n"
            f"Etat constate : {article.condition_reelle or article.condition or 'inconnu'}\n"
            f"Categorie : {article.categorie or '-'}\n"
            f"Commentaires test : {article.commentaire_test or '-'}\n"
        )
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        return f"{article.description or ''}\n\n[Generation IA indisponible: {exc}]"


# ---------------------------------------------------------------------------
# UI Streamlit
# ---------------------------------------------------------------------------
def render_ebay_settings() -> None:
    """Panneau de reglages eBay (a integrer dans modules/parametres.py)."""
    st.markdown("#### Connexion eBay")
    cfg = get_ebay_config()

    col1, col2 = st.columns(2)
    with col1:
        env = st.selectbox(
            "Environnement", ["sandbox", "prod"],
            index=0 if (cfg.environnement or "sandbox") == "sandbox" else 1,
            key="ebay_env_select",
        )
    with col2:
        site = st.selectbox(
            "Marketplace", list(EBAY_SITES.keys()),
            format_func=lambda k: EBAY_SITES[k],
            index=max(0, list(EBAY_SITES.keys()).index(cfg.site_id))
                  if cfg.site_id in EBAY_SITES else 0,
            key="ebay_site_select",
        )

    redirect_uri = st.text_input(
        "Redirect URI (RuName eBay)",
        value=cfg.redirect_uri or "",
        help="A configurer dans votre dashboard eBay Developer.",
    )

    p1, p2, p3 = st.columns(3)
    with p1:
        fp = st.text_input("Fulfillment Policy ID", value=cfg.fulfillment_policy_id or "")
    with p2:
        pp = st.text_input("Payment Policy ID", value=cfg.payment_policy_id or "")
    with p3:
        rp = st.text_input("Return Policy ID", value=cfg.return_policy_id or "")
    loc = st.text_input(
        "Merchant Location Key", value=cfg.merchant_location_key or "default-location",
    )

    if st.button("Enregistrer la configuration", key="ebay_save_cfg"):
        save_ebay_config(
            environnement=env, site_id=site, redirect_uri=redirect_uri,
            fulfillment_policy_id=fp, payment_policy_id=pp, return_policy_id=rp,
            merchant_location_key=loc,
        )
        st.success("Configuration eBay enregistree.")
        st.rerun()

    st.divider()
    st.markdown("#### Authentification OAuth")

    cid, secret = _credentials(env)
    if not cid or not secret:
        st.warning(f"Cles eBay manquantes pour l'environnement '{env}'. "
                   "Verifiez le fichier .env.")
        return

    token_ok = bool(cfg.access_token and cfg.token_expiry
                    and cfg.token_expiry > datetime.utcnow())
    if token_ok:
        st.success(f"Connecte ({env}) — token valide jusqu'au "
                   f"{cfg.token_expiry.strftime('%d/%m/%Y %H:%M')}")
    elif cfg.refresh_token:
        st.info("Token expire — utilisez 'Rafraichir' pour le renouveler.")
    else:
        st.warning("Aucun token : lancez l'autorisation OAuth.")

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Lancer l'autorisation", key="ebay_oauth_start"):
            if not redirect_uri:
                st.error("Definissez un Redirect URI avant de continuer.")
            else:
                url = get_auth_url(redirect_uri, env)
                st.markdown(f"[Cliquer ici pour autoriser sur eBay]({url})")
                st.caption("Apres autorisation, copiez le code de retour ci-dessous.")
    with c2:
        if st.button("Rafraichir le token", key="ebay_oauth_refresh"):
            tok = refresh_access_token()
            st.success("Token rafraichi.") if tok else st.error("Echec du refresh.")
            st.rerun()
    with c3:
        if st.button("Deconnecter", key="ebay_oauth_logout"):
            save_ebay_config(access_token="", refresh_token="",
                             token_expiry=None, refresh_expiry=None)
            st.success("Deconnecte.")
            st.rerun()

    code = st.text_input("Code d'autorisation eBay", key="ebay_oauth_code")
    if st.button("Echanger le code contre un token", key="ebay_oauth_exchange"):
        try:
            exchange_code_for_token(code, redirect_uri, env)
            st.success("Token obtenu et enregistre.")
            st.rerun()
        except Exception as exc:
            st.error(f"Echec : {exc}")

    st.divider()
    st.markdown("#### Adresse d'expedition (merchant location)")
    with st.form("ebay_location_form", clear_on_submit=False):
        lk1, lk2 = st.columns([1, 2])
        with lk1:
            location_key = st.text_input(
                "Location key", value=cfg.merchant_location_key or "default-location",
                help="Identifiant unique cote eBay. Reutilise si deja existant.",
            )
        with lk2:
            loc_nom = st.text_input("Nom de l'entrepot", value="DeStock Warehouse")
        loc_rue = st.text_input("Adresse (rue + numero)")
        lc1, lc2, lc3 = st.columns([2, 1, 1])
        with lc1:
            loc_ville = st.text_input("Ville")
        with lc2:
            loc_cp = st.text_input("Code postal")
        with lc3:
            loc_pays = st.text_input("Pays (ISO)", value="FR")
        loc_tel = st.text_input("Telephone (optionnel)")
        if st.form_submit_button("Creer / mettre a jour la location"):
            res = create_merchant_location(
                location_key=location_key, nom=loc_nom, rue=loc_rue,
                ville=loc_ville, code_postal=loc_cp, pays=loc_pays,
                telephone=loc_tel,
            )
            if res.get("success"):
                msg = "Location creee."
                if res.get("error"):
                    msg = res["error"]
                st.success(msg)
                st.rerun()
            else:
                st.error(f"Echec : {res.get('error')}")

    st.divider()
    st.markdown("#### Test de la connexion")
    if st.button("Tester la connexion eBay", key="ebay_test_conn"):
        result = test_connection()
        for label, ok, detail in result["checks"]:
            icon = "✅" if ok else "❌"
            st.write(f"{icon} **{label}** — {detail}")
        if result["ok"]:
            st.success("Tous les pre-requis sont OK : vous pouvez publier.")
        else:
            st.warning("Au moins un pre-requis manque — corrigez avant de publier.")


def render_ebay_publish(article) -> None:
    """Formulaire 'Publier sur eBay' pour un article donne."""
    st.markdown(f"### Publier sur eBay — {article.description[:50]}")
    cfg = get_ebay_config()
    if not _valid_access_token():
        st.error("Pas de token eBay valide. Configurez OAuth dans Reglages.")
        return

    titre = st.text_input("Titre (max 80 car.)",
                           value=(article.description or "")[:80],
                           max_chars=80, key=f"ebay_pub_title_{article.id}")
    prix_default = article.prix_affiche or article.prix_cible or 0.0
    prix = st.number_input("Prix EUR", min_value=0.0,
                            value=float(prix_default), step=0.5,
                            key=f"ebay_pub_price_{article.id}")
    categorie = st.selectbox(
        "Categorie eBay", list(EBAY_CATEGORIES.keys()),
        format_func=lambda k: f"{k} — {EBAY_CATEGORIES[k]}",
        key=f"ebay_pub_cat_{article.id}",
    )

    if "ebay_desc_" + str(article.id) not in st.session_state:
        st.session_state["ebay_desc_" + str(article.id)] = article.description or ""

    cdesc1, cdesc2 = st.columns([3, 1])
    with cdesc1:
        description = st.text_area(
            "Description", height=200,
            key=f"ebay_pub_desc_{article.id}",
            value=st.session_state["ebay_desc_" + str(article.id)],
        )
    with cdesc2:
        if st.button("Generer (IA)", key=f"ebay_pub_ai_{article.id}"):
            st.session_state["ebay_desc_" + str(article.id)] = \
                generate_ebay_description(article)
            st.rerun()

    images_raw = st.text_area(
        "URLs d'images (une par ligne, https obligatoire)",
        key=f"ebay_pub_imgs_{article.id}",
    )
    images = [u.strip() for u in (images_raw or "").splitlines() if u.strip()]

    if st.button("Publier maintenant", type="primary",
                  key=f"ebay_pub_go_{article.id}"):
        if not (cfg.fulfillment_policy_id and cfg.payment_policy_id
                and cfg.return_policy_id):
            st.error("Configurez les 3 policies eBay dans Reglages.")
            return
        if not images:
            st.error("eBay requiert au moins 1 image.")
            return
        with st.spinner("Publication en cours..."):
            res = create_ebay_listing(
                article=article, prix=prix, categorie_id=categorie,
                titre=titre, description=description, images=images,
            )
        if res.get("success"):
            st.success(f"Annonce publiee : {res['listing_id']}")
            st.markdown(f"[Voir sur eBay]({res['url']})")
        else:
            st.error(f"Echec : {res.get('error')}")


def render_ebay_dashboard() -> None:
    """Tableau de bord eBay : annonces actives + commandes recentes."""
    st.markdown("### Annonces actives sur eBay")
    listings = get_active_listings()
    if not listings:
        st.caption("Aucune annonce active (ou token invalide).")
    else:
        rows = []
        for o in listings:
            rows.append({
                "SKU": o.get("sku", ""),
                "Offer": o.get("offerId", ""),
                "Listing": o.get("listingId", ""),
                "Statut": o.get("status", ""),
                "Prix": (o.get("pricingSummary", {}).get("price", {}).get("value")
                         or "-"),
                "Format": o.get("format", ""),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("### Commandes recentes (30 derniers jours)")
    orders = get_recent_orders(days=30)
    if not orders:
        st.caption("Aucune commande recente.")
        return

    rows = []
    for o in orders:
        total = o.get("pricingSummary", {}).get("total", {})
        rows.append({
            "Date": o.get("creationDate", "")[:10],
            "Order": o.get("orderId", ""),
            "Acheteur": o.get("buyer", {}).get("username", ""),
            "Statut": o.get("orderFulfillmentStatus", ""),
            "Montant": f"{total.get('value', '-')} {total.get('currency', '')}",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)
