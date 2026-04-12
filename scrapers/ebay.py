# -*- coding: utf-8 -*-
"""
DeStock App - scrapers/ebay.py
Scraping des prix eBay via requests + BeautifulSoup.

Strategie :
  - GET https://www.ebay.fr/sch/i.html?_nkw={query}
  - Parsing des cartes .s-item
  - Deux passages : resultats actifs + resultats vendus (LH_Sold=1 + LH_Complete=1)
  - Fallback automatique sur ebay.com si fr renvoie moins de 3 resultats

eBay ne bloque generalement pas les scrapes legers (contrairement a Amazon/LBC).
"""

from __future__ import annotations

import re
import statistics
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
}

_PRIX_RE = re.compile(r"([0-9][0-9\s\u202f\.,]*)")


def _parse_price(txt: str) -> float:
    """Parse un prix eBay (peut contenir EUR, USD, etc.)."""
    if not txt:
        return 0.0
    clean = txt.replace("\u00a0", " ").replace("\u202f", " ")
    # eBay affiche parfois un range "EUR 10,00 a EUR 20,00" -> on prend le 1er
    m = _PRIX_RE.search(clean)
    if not m:
        return 0.0
    raw = m.group(1).replace(" ", "")
    # Heuristique : si ".00" en fin -> separateur decimal . ; sinon virgule = decimal
    if raw.count(",") == 1 and raw.count(".") == 0:
        raw = raw.replace(",", ".")
    elif raw.count(".") > 1 and raw.count(",") == 1:
        # Format FR "1.234,56"
        raw = raw.replace(".", "").replace(",", ".")
    elif raw.count(",") > 1 and raw.count(".") == 1:
        # Format US "1,234.56"
        raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _search_ebay(domain: str, query: str, sold_only: bool = False,
                 timeout: int = 20) -> list[dict]:
    """Recupere les annonces eBay d'un domaine donne."""
    if not query:
        return []
    url = f"https://www.ebay.{domain}/sch/i.html?_nkw={quote_plus(query)}"
    if sold_only:
        url += "&LH_Sold=1&LH_Complete=1"

    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        if r.status_code != 200:
            return []
    except Exception:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    items: list[dict] = []
    # eBay utilise `li.s-card` depuis fin 2024 (anciennement `li.s-item`).
    # On garde les deux selecteurs pour robustesse.
    cards = soup.select("li.s-card, li.s-item, div.s-card, div.s-item")
    for card in cards:
        # Nouveau : .s-card__title / .s-card__price
        # Ancien  : .s-item__title / .s-item__price
        title_el = card.select_one(".s-card__title, .s-item__title")
        price_el = card.select_one(".s-card__price, .s-item__price")
        link_el = card.find("a", href=True)
        if not (title_el and price_el):
            continue
        titre = title_el.get_text(strip=True)
        # Cartes promotionnelles en 1ere position -> on skip
        if titre in ("Shop on eBay", "Achetez sur eBay", ""):
            continue
        prix = _parse_price(price_el.get_text())
        if prix <= 0:
            continue
        items.append({
            "titre": titre,
            "prix": prix,
            "url": link_el.get("href") if link_el else "",
        })
    return items


def get_ebay_prices(query: str, asin: str | None = None) -> dict:
    """
    Recupere les prix eBay (fr puis fallback .com) pour une recherche.
    Inclut la mediane des articles VENDUS (vrai prix marche).
    """
    result = {
        "query": query,
        "prix_min": 0.0,
        "prix_max": 0.0,
        "prix_median": 0.0,
        "prix_vendus_median": 0.0,
        "nb_resultats": 0,
        "nb_vendus": 0,
        "devise": "EUR",
        "annonces": [],
        "erreur": "",
    }
    if not query:
        result["erreur"] = "Query vide"
        return result

    items_fr = _search_ebay("fr", query, sold_only=False)
    sold_fr = _search_ebay("fr", query, sold_only=True)

    items = items_fr
    sold = sold_fr

    # Fallback .com si moins de 3 resultats actifs sur .fr
    if len(items_fr) < 3:
        items_com = _search_ebay("com", query, sold_only=False)
        sold_com = _search_ebay("com", query, sold_only=True)
        items = items_fr + items_com
        sold = sold_fr + sold_com
        if items_com:
            result["devise"] = "EUR+USD"  # mix, a retraiter cote UI

    # Agregation des prix actifs
    prices = [i["prix"] for i in items if i["prix"] > 0]
    if prices:
        result["prix_min"] = min(prices)
        result["prix_max"] = max(prices)
        result["prix_median"] = float(statistics.median(prices))
        result["nb_resultats"] = len(prices)

    # Agregation des prix vendus
    sold_prices = [i["prix"] for i in sold if i["prix"] > 0]
    if sold_prices:
        result["prix_vendus_median"] = float(statistics.median(sold_prices))
        result["nb_vendus"] = len(sold_prices)

    # Garde les 10 premieres annonces pour affichage
    result["annonces"] = items[:10]

    if not prices and not sold_prices:
        result["erreur"] = "Aucun resultat eBay (fr + com)"

    return result


if __name__ == "__main__":
    import json
    r = get_ebay_prices("Dreame X50 Ultra")
    print(json.dumps(r, indent=2, ensure_ascii=False))
