# -*- coding: utf-8 -*-
"""
DeStock App - scrapers/prix_marche.py
Orchestrateur des sources de prix marche + cache SQLite.

Interroge Amazon.fr, Le Bon Coin et eBay pour un ASIN / description donnes,
calcule le prix cible selon la condition et un score de confiance 1-3.
Le resultat est cache en base (table prix_cache) avec TTL 24h.
"""

from __future__ import annotations

import json as _json
import time
from datetime import datetime, timedelta

from database import PrixCache, get_session
from scrapers import amazon, ebay, leboncoin

# ---------------------------------------------------------------------------
# Coefficients de revalorisation selon la condition B-Stock
# ---------------------------------------------------------------------------
# Pour chaque condition, on retient le MIN entre :
#   - amazon_neuf * coeff_amazon
#   - (lbc_median ou lbc_min selon `source`) * coeff_lbc
CONDITION_COEFFS_MARCHE = {
    "warehouse damage": {"amazon": 0.65, "lbc": 0.85, "source": "median"},
    "customer damage":  {"amazon": 0.45, "lbc": 0.65, "source": "median"},
    "carrier damage":   {"amazon": 0.45, "lbc": 0.65, "source": "median"},
    "defective":        {"amazon": 0.30, "lbc": 0.50, "source": "min"},
}
DEFAULT_COEFFS = {"amazon": 0.40, "lbc": 0.60, "source": "median"}

# TTL du cache en secondes (24h)
CACHE_TTL_SECONDS = 86400

# Delai entre requetes (anti-bot throttling)
DELAI_INTER_REQUETES = 1.5


# ---------------------------------------------------------------------------
# Cache SQLite
# ---------------------------------------------------------------------------
def _cache_get(cle: str) -> dict | None:
    """Retourne le dict en cache pour la cle si frais (<24h), sinon None."""
    if not cle:
        return None
    session = get_session()
    try:
        row = session.query(PrixCache).filter_by(asin=cle).first()
        if row is None or not row.data:
            return None
        age = (datetime.utcnow() - row.date).total_seconds()
        if age > CACHE_TTL_SECONDS:
            return None
        try:
            return _json.loads(row.data)
        except Exception:
            return None
    finally:
        session.close()


def _cache_set(cle: str, payload: dict) -> None:
    """Ecrit/met a jour une entree de cache."""
    if not cle:
        return
    session = get_session()
    try:
        row = session.query(PrixCache).filter_by(asin=cle).first()
        data_str = _json.dumps(payload, ensure_ascii=False, default=str)
        if row is None:
            row = PrixCache(asin=cle, date=datetime.utcnow(), data=data_str)
            session.add(row)
        else:
            row.date = datetime.utcnow()
            row.data = data_str
        session.commit()
    finally:
        session.close()


def vider_cache() -> int:
    """Supprime toutes les entrees du cache. Retourne le nombre d'entrees supprimees."""
    session = get_session()
    try:
        n = session.query(PrixCache).delete()
        session.commit()
        return n
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Logique metier
# ---------------------------------------------------------------------------
def _get_coeffs(condition: str) -> dict:
    cond = (condition or "").strip().lower()
    for key, val in CONDITION_COEFFS_MARCHE.items():
        if key in cond:
            return val
    return DEFAULT_COEFFS


def _cle_cache(asin: str, description: str) -> str:
    """Cle de cache : ASIN si present, sinon hash de la description."""
    if asin:
        return f"asin:{asin}"
    if description:
        # Tronque + normalise pour eviter les doublons
        key = description.strip().lower()[:100]
        return f"desc:{key}"
    return ""


def _build_query(description: str, asin: str) -> str:
    """Construit la query de recherche LBC/eBay a partir de la description."""
    if description:
        return description.strip()[:60]
    return asin or ""


def analyser_article(asin: str, ean: str = "", description: str = "",
                     condition: str = "", use_cache: bool = True) -> dict:
    """
    Interroge Amazon + LBC + eBay pour un article et calcule le prix cible.
    Retourne un dict complet (jamais d'exception).

    - asin        : identifiant ASIN Amazon (prioritaire)
    - ean         : code EAN (non utilise dans cette version)
    - description : texte de l'article (utilise pour la recherche LBC/eBay)
    - condition   : etat B-Stock (Warehouse Damage / Customer Damage / ...)
    - use_cache   : si True, retourne le cache du jour si present
    """
    cle = _cle_cache(asin, description)

    # --- Cache hit ---
    if use_cache and cle:
        cached = _cache_get(cle)
        if cached is not None:
            cached["from_cache"] = True
            return cached

    query = _build_query(description, asin)

    # --- Appel des 3 sources avec delai anti-throttle ---
    res_amazon = amazon.get_amazon_price(asin) if asin else {
        "prix_neuf": 0.0, "prix_occasion_min": 0.0, "titre": "",
        "disponible": False, "erreur": "ASIN vide",
    }
    time.sleep(DELAI_INTER_REQUETES)

    res_lbc = leboncoin.get_lbc_prices(query, nb_resultats=10)
    time.sleep(DELAI_INTER_REQUETES)

    res_ebay = ebay.get_ebay_prices(query, asin=asin)

    # --- Extraction des chiffres cles ---
    prix_amazon       = float(res_amazon.get("prix_neuf") or 0)
    prix_amazon_occas = float(res_amazon.get("prix_occasion_min") or 0)
    prix_lbc_median   = float(res_lbc.get("prix_median") or 0)
    prix_lbc_min      = float(res_lbc.get("prix_min") or 0)
    prix_ebay_median  = float(res_ebay.get("prix_median") or 0)
    prix_ebay_vendus  = float(res_ebay.get("prix_vendus_median") or 0)

    # --- Score de confiance (nb de sources ayant retourne un prix) ---
    confiance = 0
    if prix_amazon > 0:
        confiance += 1
    if prix_lbc_median > 0:
        confiance += 1
    if prix_ebay_median > 0 or prix_ebay_vendus > 0:
        confiance += 1

    # --- Calcul du prix cible selon la condition ---
    coeffs = _get_coeffs(condition)
    candidats: list[float] = []

    if prix_amazon > 0:
        candidats.append(prix_amazon * coeffs["amazon"])

    if coeffs["source"] == "min" and prix_lbc_min > 0:
        candidats.append(prix_lbc_min * coeffs["lbc"])
    elif prix_lbc_median > 0:
        candidats.append(prix_lbc_median * coeffs["lbc"])

    # Si on a des donnees eBay "vendus" (le vrai prix marche), on les utilise
    # comme borne basse aussi (0.9x pour se caler en dessous du median vendus)
    if prix_ebay_vendus > 0:
        candidats.append(prix_ebay_vendus * 0.90)

    prix_cible = round(min(candidats), 2) if candidats else 0.0

    # --- Choix du canal recommande ---
    # Heuristique : eBay si on a des ventes reelles + marge > LBC ; sinon LBC si dispo ; sinon Amazon
    if prix_ebay_vendus > 0 and prix_ebay_vendus >= prix_lbc_median:
        canal = "eBay"
    elif prix_lbc_median > 0:
        canal = "LBC"
    elif prix_amazon > 0:
        canal = "Amazon"
    else:
        canal = "Inconnu"

    resultat = {
        "asin":                asin,
        "description":         description,
        "condition":           condition,
        "prix_amazon":         prix_amazon,
        "prix_amazon_occas":   prix_amazon_occas,
        "prix_lbc_median":     prix_lbc_median,
        "prix_lbc_min":        prix_lbc_min,
        "prix_ebay_median":    prix_ebay_median,
        "prix_ebay_vendus":    prix_ebay_vendus,
        "prix_cible_calcule":  prix_cible,
        "canal_recommande":    canal,
        "confiance":           confiance,
        "nb_lbc_annonces":     int(res_lbc.get("nb_annonces", 0) or 0),
        "nb_ebay_actifs":      int(res_ebay.get("nb_resultats", 0) or 0),
        "nb_ebay_vendus":      int(res_ebay.get("nb_vendus", 0) or 0),
        "erreurs": {
            "amazon": res_amazon.get("erreur", ""),
            "lbc":    res_lbc.get("erreur", ""),
            "ebay":   res_ebay.get("erreur", ""),
        },
        "from_cache": False,
        "date":       datetime.utcnow().isoformat(),
    }

    # --- Mise en cache ---
    if cle:
        _cache_set(cle, resultat)

    return resultat


if __name__ == "__main__":
    import json
    r = analyser_article(
        asin="B0DPB7RN2L",
        description="Dreame X50 Ultra Robot Aspirateur Laveur",
        condition="Customer Damage",
    )
    print(json.dumps(r, indent=2, ensure_ascii=False))
