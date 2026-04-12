# -*- coding: utf-8 -*-
"""
DeStock App - scrapers/leboncoin.py
Scraping des prix Le Bon Coin via requests + BeautifulSoup.

Strategie :
  - GET https://www.leboncoin.fr/recherche?text={query}&sort=price&order=asc
  - LBC est une SPA Next.js : les donnees sont dans un <script id="__NEXT_DATA__">
    au format JSON. On les parse directement plutot que de scraper le DOM.
  - Fallback sur les cartes HTML si JSON introuvable.

Note : LBC est protege par DataDome. Les requetes simples peuvent etre
bloquees (403 ou page vide). Le code retourne alors un dict vide avec
`erreur` rempli.
"""

from __future__ import annotations

import json as _json
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
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Referer": "https://www.leboncoin.fr/",
}


_PRIX_RE = re.compile(r"([0-9][0-9\s\u202f\.]*[,]?\d*)")


def _parse_price(txt: str) -> float:
    if not txt:
        return 0.0
    clean = txt.replace("\u00a0", " ").replace("\u202f", " ")
    m = _PRIX_RE.search(clean)
    if not m:
        return 0.0
    raw = m.group(1).replace(" ", "")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _find_ads_in_json(data) -> list[dict]:
    """
    Cherche recursivement une liste d'annonces dans la structure JSON de LBC.
    La cle 'ads' (ou 'listAds') contient un tableau de dicts avec 'subject',
    'price', 'url', etc. Cette fonction est robuste aux changements de chemin.
    """
    if isinstance(data, dict):
        for key in ("ads", "listAds", "listings"):
            if key in data and isinstance(data[key], list):
                return data[key]
        for v in data.values():
            r = _find_ads_in_json(v)
            if r:
                return r
    elif isinstance(data, list):
        for item in data:
            r = _find_ads_in_json(item)
            if r:
                return r
    return []


def get_lbc_prices(query: str, nb_resultats: int = 5) -> dict:
    """
    Recupere les prix Le Bon Coin pour une recherche.
    Retourne un dict (jamais d'exception).
    """
    result = {
        "query": query,
        "prix_min": 0.0,
        "prix_max": 0.0,
        "prix_median": 0.0,
        "nb_annonces": 0,
        "annonces": [],
        "erreur": "",
    }
    if not query:
        result["erreur"] = "Query vide"
        return result

    url = (
        f"https://www.leboncoin.fr/recherche?text={quote_plus(query)}"
        f"&sort=price&order=asc"
    )
    try:
        r = requests.get(url, headers=_HEADERS, timeout=10)
    except requests.Timeout:
        result["erreur"] = "Timeout (10s)"
        return result
    except Exception as exc:
        result["erreur"] = f"{type(exc).__name__}: {exc}"
        return result

    if r.status_code == 403:
        result["erreur"] = "HTTP 403 (DataDome anti-bot)"
        return result
    if r.status_code != 200:
        result["erreur"] = f"HTTP {r.status_code}"
        return result

    soup = BeautifulSoup(r.text, "html.parser")

    # --- Chemin principal : parsing du __NEXT_DATA__ ---
    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        try:
            data = _json.loads(script.string)
            ads = _find_ads_in_json(data)
            prices: list[float] = []
            for ad in ads[:nb_resultats]:
                titre = ad.get("subject", "") or ad.get("title", "")
                # Prix LBC peut etre dans price (liste) ou price_cents ou direct
                prix_val: float = 0.0
                if isinstance(ad.get("price"), list) and ad["price"]:
                    prix_val = float(ad["price"][0])
                elif isinstance(ad.get("price"), (int, float)):
                    prix_val = float(ad["price"])
                elif isinstance(ad.get("price_cents"), (int, float)):
                    prix_val = float(ad["price_cents"]) / 100
                ad_url = ad.get("url", "")
                if ad_url and not ad_url.startswith("http"):
                    ad_url = f"https://www.leboncoin.fr{ad_url}"
                result["annonces"].append({
                    "titre": titre,
                    "prix": prix_val,
                    "url": ad_url,
                    "date": ad.get("first_publication_date", "") or ad.get("index_date", ""),
                })
                if prix_val > 0:
                    prices.append(prix_val)
            if prices:
                result["prix_min"] = min(prices)
                result["prix_max"] = max(prices)
                result["prix_median"] = float(statistics.median(prices))
                result["nb_annonces"] = len(prices)
                return result
            if ads:
                result["erreur"] = "JSON trouve mais aucun prix valide"
            else:
                result["erreur"] = "JSON trouve mais liste 'ads' introuvable"
        except Exception as exc:
            result["erreur"] = f"Parse JSON : {exc}"

    # --- Fallback : parsing HTML direct ---
    cards = soup.select("a[data-test-id='ad'], article[data-test-id='ad']")
    if cards:
        prices = []
        for card in cards[:nb_resultats]:
            title_el = card.select_one("[data-test-id='ad-title'], p")
            price_el = card.select_one("[data-test-id='price'], span[aria-label*='Prix']")
            if title_el and price_el:
                prix = _parse_price(price_el.get_text())
                href = card.get("href") or ""
                if href and not href.startswith("http"):
                    href = "https://www.leboncoin.fr" + href
                result["annonces"].append({
                    "titre": title_el.get_text(strip=True),
                    "prix": prix,
                    "url": href,
                    "date": "",
                })
                if prix > 0:
                    prices.append(prix)
        if prices:
            result["prix_min"] = min(prices)
            result["prix_max"] = max(prices)
            result["prix_median"] = float(statistics.median(prices))
            result["nb_annonces"] = len(prices)
            result["erreur"] = ""
            return result

    if not result["erreur"]:
        result["erreur"] = "Aucune annonce trouvee (DOM vide ou bloquee)"
    return result


if __name__ == "__main__":
    import json
    r = get_lbc_prices("Dreame X50 Ultra")
    print(json.dumps(r, indent=2, ensure_ascii=False))
