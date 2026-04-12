# -*- coding: utf-8 -*-
"""
DeStock App - scrapers/amazon.py
Recuperation des prix Amazon.fr via scraping HTTP direct (pas de browser).

Strategie :
  - GET https://www.amazon.fr/dp/{ASIN}
  - User-Agent Chrome realiste
  - Parsing BeautifulSoup
  - Timeout 10s + 1 retry en cas de timeout / 503

Note : Amazon a un anti-bot agressif. Les requetes simples peuvent etre
bloquees par un 503 "Robot check". Le code retourne alors un dict avec
`erreur` rempli pour que l'appelant gere le cas.
"""

from __future__ import annotations

import re
import time

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
    "Sec-Fetch-User": "?1",
}


_PRIX_RE = re.compile(r"([0-9][0-9\s\u202f\.]*[,]?\d*)")


def _parse_price(txt: str) -> float:
    """Parse un prix texte francais (ex: '1 234,56 EUR') en float."""
    if not txt:
        return 0.0
    clean = txt.replace("\u00a0", " ").replace("\u202f", " ")
    m = _PRIX_RE.search(clean)
    if not m:
        return 0.0
    raw = m.group(1).replace(" ", "")
    # Format europeen : virgule decimale, point = milliers
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def get_amazon_price(asin: str, retry: int = 1) -> dict:
    """
    Recupere le prix Amazon.fr pour un ASIN.
    Retourne un dict complet meme en cas d'erreur (jamais de lever d'exception).
    """
    result = {
        "asin": asin,
        "prix_neuf": 0.0,
        "prix_occasion_min": 0.0,
        "titre": "",
        "disponible": False,
        "url": f"https://www.amazon.fr/dp/{asin}" if asin else "",
        "erreur": "",
    }
    if not asin:
        result["erreur"] = "ASIN vide"
        return result

    attempts = max(1, retry + 1)
    for attempt in range(attempts):
        try:
            r = requests.get(result["url"], headers=_HEADERS, timeout=10)

            # Amazon repond 503 sur ses pages de robot check
            if r.status_code == 503:
                result["erreur"] = "HTTP 503 (Amazon anti-bot)"
                if attempt < attempts - 1:
                    time.sleep(2)
                    continue
                return result

            if r.status_code != 200:
                result["erreur"] = f"HTTP {r.status_code}"
                if attempt < attempts - 1:
                    time.sleep(1)
                    continue
                return result

            soup = BeautifulSoup(r.text, "html.parser")

            # Detection explicite de la page "Robot Check" / CAPTCHA
            title_tag = soup.find("title")
            title_txt = title_tag.get_text(strip=True) if title_tag else ""
            if "Robot" in title_txt or "captcha" in r.text.lower()[:2000]:
                result["erreur"] = "Amazon robot check (CAPTCHA)"
                if attempt < attempts - 1:
                    time.sleep(2)
                    continue
                return result

            # --- Titre produit ---
            title_el = soup.find("span", id="productTitle")
            if title_el:
                result["titre"] = title_el.get_text(strip=True)

            # --- Prix neuf : plusieurs selecteurs candidats ---
            price_selectors = [
                "#corePriceDisplay_desktop_feature_div span.a-price > .a-offscreen",
                "#corePrice_feature_div span.a-price > .a-offscreen",
                ".priceToPay span.a-offscreen",
                "#priceblock_ourprice",
                "#priceblock_dealprice",
                "#priceblock_saleprice",
                "span.a-price > span.a-offscreen",  # fallback global
            ]
            for sel in price_selectors:
                el = soup.select_one(sel)
                if el:
                    prix = _parse_price(el.get_text())
                    if prix > 0:
                        result["prix_neuf"] = prix
                        break

            # --- Prix occasion ---
            occasion_selectors = [
                "#usedBuySection .a-offscreen",
                "#olp_feature_div a.a-link-normal .a-offscreen",
                "#usedOfferContainer .a-offscreen",
            ]
            for sel in occasion_selectors:
                el = soup.select_one(sel)
                if el:
                    prix = _parse_price(el.get_text())
                    if prix > 0:
                        result["prix_occasion_min"] = prix
                        break

            if result["titre"] or result["prix_neuf"] > 0:
                result["disponible"] = True
                result["erreur"] = ""
                return result

            # Aucune donnee extraite -> retry
            result["erreur"] = "Aucune donnee extraite (selecteurs manques)"
            if attempt < attempts - 1:
                time.sleep(1)
                continue
            return result

        except requests.Timeout:
            result["erreur"] = "Timeout (10s)"
            if attempt < attempts - 1:
                time.sleep(2)
                continue
            return result
        except Exception as exc:
            result["erreur"] = f"{type(exc).__name__}: {exc}"
            return result

    return result


if __name__ == "__main__":
    # Test rapide
    import json
    r = get_amazon_price("B0DPB7RN2L")
    print(json.dumps(r, indent=2, ensure_ascii=False))
