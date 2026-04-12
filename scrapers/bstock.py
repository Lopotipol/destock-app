# -*- coding: utf-8 -*-
"""
DeStock App - scrapers/bstock.py
Scraper Playwright pour la marketplace B-Stock.

Fonctions exposees :
    - login(email, password)            -> sauvegarde la session dans bstock_session.json
    - get_lots_europe()                  -> liste des lots disponibles en Europe
    - get_lot_detail(url_lot)            -> fiche detaillee d'un lot
    - download_manifest(url_lot, path)   -> telecharge le manifeste CSV
    - parse_manifest(csv_path, lot_data) -> enrichit les articles (cout reel, ROI...)

Regles importantes :
    - Les selecteurs CSS sont indiques en commentaire et sont a ajuster apres
      la premiere execution reelle (le DOM de B-Stock n'est pas fige).
    - L'import de Playwright est tardif (a l'interieur des fonctions) : ainsi
      le module reste importable meme si Playwright n'est pas installe, ce
      qui evite de casser Streamlit au demarrage.
    - Toutes les fonctions ont un try/except qui remonte un message clair.
    - Si `bstock_session.json` existe, la session est reutilisee (pas de
      re-login), cela accelere beaucoup le scraping au quotidien.
"""

from __future__ import annotations

import asyncio
import functools
import html as _html
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

from config import BASE_DIR

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
# Dossier du profil Chrome persistant (contient cookies, localStorage, cache).
# Cette approche permet a Cloudflare Turnstile de faire confiance au navigateur
# apres une connexion manuelle initiale.
PROFILE_DIR = BASE_DIR / "bstock_profile"
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

URL_HOME = "https://bstock.com/"
URL_LOGIN = "https://bstock.com/acct/signin?redirectAfterLogin=%2F"
URL_ACCOUNT = "https://bstock.com/acct/settings"
# URL confirmee par l'utilisateur pour la liste des lots Europe
URL_LOTS_EUROPE = 'https://bstock.com/all-auctions?region=%5B%22Europe%22%5D'

# ---------------------------------------------------------------------------
# Endpoints API B-Stock (decouverts via sniff_api)
# ---------------------------------------------------------------------------
API_SEARCH_LISTINGS = "https://search.bstock.com/v1/all-listings/listings"
API_SEARCH_FILTERS = "https://search.bstock.com/v1/all-listings/listings/filters"
API_SEARCH_SELLERS = "https://search.bstock.com/v1/all-listings/sellers"
API_BRIDGE_AUCTIONS = "https://bridge.bstock.com/v1/ent-auctions"

# Regions Europe (28 codes pays) utilisees par le filtre UI B-Stock
EU_REGIONS = [
    "/AT", "/BE", "/BG", "/HR", "/CY", "/CZ", "/DK", "/EE", "/FI", "/FR",
    "/DE", "/GR", "/HU", "/IE", "/IT", "/LV", "/LT", "/LU", "/MT", "/NL",
    "/PL", "/PT", "/RO", "/SK", "/SI", "/ES", "/SE", "/GB",
]

# Headers pour faire croire qu'on vient du site officiel (pas d'auth requis)
_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Origin": "https://bstock.com",
    "Referer": "https://bstock.com/all-auctions",
}

# Coefficients de revalorisation par etat (estimation marche FR)
CONDITION_COEFFS = {
    "warehouse damage": 0.65,
    "customer damage":  0.45,
    "carrier damage":   0.45,
    "defective":        0.30,
}
DEFAULT_COEFF = 0.40


# ---------------------------------------------------------------------------
# Import tardif de Playwright (protege l'import du module)
# ---------------------------------------------------------------------------
def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        return sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright n'est pas installe. Execute : "
            "pip install playwright && playwright install chromium"
        ) from exc


# ---------------------------------------------------------------------------
# Decorateur : execute le scraping dans un thread worker avec Proactor loop
# ---------------------------------------------------------------------------
# Pourquoi : sur Windows, Streamlit/tornado installe un SelectorEventLoop
# qui ne supporte pas asyncio.subprocess_exec. Or Playwright a besoin de
# subprocess_exec pour spawn son driver Node.js. On force donc chaque tache
# Playwright a s'executer dans un thread dedie avec WindowsProactorEventLoopPolicy.
def _in_playwright_thread(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        box: dict = {}

        def worker():
            try:
                if sys.platform == "win32":
                    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    box["value"] = func(*args, **kwargs)
                finally:
                    try:
                        loop.close()
                    except Exception:
                        pass
            except BaseException as exc:
                box["error"] = exc

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join()
        if "error" in box:
            raise box["error"]
        return box.get("value")

    return wrapper


# ---------------------------------------------------------------------------
# Helpers bas niveau
# ---------------------------------------------------------------------------
def is_profile_configured() -> bool:
    """
    Retourne True si le profil Chrome B-Stock existe et contient au moins
    un fichier (indique qu'une connexion manuelle a deja ete effectuee).
    """
    try:
        return (
            PROFILE_DIR.exists()
            and PROFILE_DIR.is_dir()
            and any(PROFILE_DIR.iterdir())
        )
    except Exception:
        return False


def reset_profile() -> tuple[bool, str]:
    """
    Supprime le dossier du profil Chrome B-Stock.
    Apres cet appel, is_profile_configured() retourne False et la
    prochaine connexion necessitera un nouveau setup_profile() manuel.
    """
    import shutil
    try:
        if PROFILE_DIR.exists():
            shutil.rmtree(PROFILE_DIR, ignore_errors=True)
        return True, f"Profil reinitialise ({PROFILE_DIR.name} supprime)."
    except Exception as exc:
        return False, f"Erreur lors de la reinitialisation : {exc}"


CHROME_CANDIDATE_PATHS = [
    Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path(r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
]


def _find_real_chrome() -> Path | None:
    """Retourne le chemin vers le vrai Chrome Windows s'il est installe."""
    for p in CHROME_CANDIDATE_PATHS:
        try:
            if p.exists():
                return p
        except Exception:
            continue
    return None


def _launch_persistent(pw, headless: bool = False):
    """
    Ouvre un contexte Chrome PERSISTANT (cookies et localStorage dans
    PROFILE_DIR). Utilise en priorite le vrai Chrome installe sur Windows
    (via executable_path) car Playwright / Chromium bundle est detecte par
    Cloudflare Turnstile meme avec tous les flags anti-detection.

    Strategie de fallback :
      1. Vrai Chrome Windows (executable_path explicite)
      2. channel='chrome' (Playwright resout lui-meme l'executable)
      3. Chromium bundle (dernier recours, probablement bloque par Cloudflare)

    Retourne directement un BrowserContext (pas de Browser separe :
    avec launch_persistent_context, il n'y a qu'un seul objet).
    """
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # Args anti-detection + options compatibles vrai Chrome
    base_kwargs: dict[str, Any] = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-default-apps",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-site-isolation-trials",
        ],
        "viewport": {"width": 1400, "height": 900},
        "locale": "fr-FR",
        "accept_downloads": True,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }

    context = None
    # --- Tentative 1 : vrai Chrome via executable_path ---
    real_chrome = _find_real_chrome()
    if real_chrome is not None:
        try:
            print(f"[bstock] Lancement de Chrome Windows : {real_chrome}")
            context = pw.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                executable_path=str(real_chrome),
                **base_kwargs,
            )
        except Exception as exc:
            print(f"[bstock] Echec executable_path ({exc}), tentative channel='chrome'")

    # --- Tentative 2 : channel='chrome' (Playwright resout le chemin) ---
    if context is None:
        try:
            context = pw.chromium.launch_persistent_context(
                str(PROFILE_DIR), channel="chrome", **base_kwargs
            )
        except Exception as exc:
            print(f"[bstock] Echec channel='chrome' ({exc}), fallback Chromium bundle")

    # --- Tentative 3 : Chromium bundle (dernier recours) ---
    if context is None:
        context = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), **base_kwargs
        )

    # Patch JS : masque navigator.webdriver (defense supplementaire)
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return context


def _get_or_create_page(context):
    """Retourne la page initiale du contexte persistant ou en cree une."""
    if context.pages:
        return context.pages[0]
    return context.new_page()


def _accept_cookies(page) -> None:
    """Ferme la banniere cookies si elle est presente. Les selecteurs sont a ajuster."""
    candidats = [
        "button#onetrust-accept-btn-handler",         # OneTrust
        "button:has-text('Accept All')",
        "button:has-text('Accept all')",
        "button:has-text('Accepter')",
        "button:has-text('I accept')",
        "button[aria-label*='accept' i]",
    ]
    for sel in candidats:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1200):
                loc.click()
                page.wait_for_timeout(400)
                return
        except Exception:
            continue


def _text_or_empty(locator) -> str:
    """Retourne le texte d'un locator ou '' si absent / non visible."""
    try:
        if locator.count() == 0:
            return ""
        return (locator.first.inner_text(timeout=1500) or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Client API HTTP (pas de Playwright)
# ---------------------------------------------------------------------------
_api_session_cache: requests.Session | None = None


def _api_session() -> requests.Session:
    """Retourne (et met en cache) une session requests pre-configuree."""
    global _api_session_cache
    if _api_session_cache is None:
        s = requests.Session()
        s.headers.update(_API_HEADERS)
        _api_session_cache = s
    return _api_session_cache


def _post_listings(offset: int = 0, limit: int = 50,
                   sort_by: str = "recommended",
                   sort_order: str = "asc") -> dict:
    """
    Appelle le endpoint POST search/listings avec un filtre region EU complete.
    Retourne le JSON brut tel que renvoye par l'API.
    """
    payload = {
        "region": EU_REGIONS,
        "limit": limit,
        "offset": offset,
        "sortBy": sort_by,
        "sortOrder": sort_order,
    }
    r = _api_session().post(API_SEARCH_LISTINGS, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


def _parse_iso_utc(iso_str: str) -> datetime | None:
    """Parse une date ISO type '2026-04-11T12:40:00.000Z' en datetime aware UTC."""
    if not iso_str:
        return None
    try:
        # On remplace le Z final par +00:00 pour fromisoformat (Python 3.11+)
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None


def _compute_statut(end_time_iso: str) -> tuple[str, int]:
    """
    Calcule le statut et le nombre de secondes restantes avant fermeture.
    Retourne ('ouvert' | 'bientot_ferme' | 'ferme', secondes_restantes).
    """
    dt_end = _parse_iso_utc(end_time_iso)
    if dt_end is None:
        return "ouvert", -1
    delta = (dt_end - datetime.now(timezone.utc)).total_seconds()
    sec = int(delta)
    if sec <= 0:
        return "ferme", 0
    if sec < 2 * 3600:
        return "bientot_ferme", sec
    return "ouvert", sec


def _map_listing(raw: dict) -> dict:
    """
    Convertit un listing brut de l'API en dict normalise consomme par l'UI
    (meme shape que l'ancien scraper Playwright).
    """
    titre_raw = raw.get("title") or ""
    titre = _html.unescape(titre_raw)

    end_time = raw.get("endTime") or raw.get("buyNowEndTime") or ""
    statut, sec_restant = _compute_statut(end_time)

    localisation = raw.get("region") or ""
    if not localisation:
        ville = raw.get("sellerCity") or ""
        pays = raw.get("sellerCountry") or ""
        localisation = f"{ville} {pays}".strip()

    return {
        # Champs attendus par marketplace.py (onglet "Lots disponibles")
        "titre":                titre,
        "url":                  raw.get("auctionUrl") or "",
        # winningBidAmount = enchere en cours si > 0, sinon startPrice = prix d'ouverture
        "enchere":              float(raw.get("winningBidAmount") or raw.get("startPrice") or 0),
        "start_price":          float(raw.get("startPrice") or 0),
        "winning_bid":          float(raw.get("winningBidAmount") or 0),
        "nb_articles":          int(raw.get("units") or 0),
        "retail_total":         float(raw.get("retailPrice") or 0),
        "date_cloture":         end_time,
        "localisation":         localisation,
        "statut":               statut,
        "ferme_dans_secondes":  sec_restant,
        # Champs additionnels exposes pour la fiche detaillee / filtres
        "lot_id":               str(raw.get("listingId") or ""),
        "listing_pretty_id":    raw.get("listingPrettyId") or raw.get("id") or "",
        "condition":            raw.get("condition") or "",
        "categories":           raw.get("categories") or [],
        "categorie":            ", ".join(raw.get("categories") or []),
        "currency":             raw.get("currency") or "EUR",
        "percent_msrp":         float(raw.get("percentMsrp") or 0),
        "primary_image":        raw.get("primaryImageUrl") or "",
        "site_name":            raw.get("siteName") or "",
        "site_url":             raw.get("siteUrl") or "",
        "storefront_name":      raw.get("storefrontName") or "",
        "seller_country":       raw.get("sellerCountry") or "",
        "seller_location_name": raw.get("sellerLocationName") or "",
        "number_of_bids":       int(raw.get("numberOfBids") or 0),
        "inventory_type":       raw.get("inventoryType") or "",
        "shipment_type":        raw.get("shipmentType") or "",
        "pricing_strategy":     raw.get("pricingStrategy") or "",
    }


_MONEY_RE = re.compile(r"([0-9][0-9\s\.,]*)")


def _parse_money(txt) -> float:
    """Extrait un montant numerique d'une chaine du type '1 234,56 EUR'.
    Gere aussi les cas ou pandas a deja converti la valeur en float/int."""
    if txt is None:
        return 0.0
    if isinstance(txt, (int, float)):
        import math
        return 0.0 if math.isnan(txt) else float(txt)
    txt = str(txt).strip()
    if not txt or txt.lower() == "nan":
        return 0.0
    m = _MONEY_RE.search(txt.replace("\u00a0", " "))
    if not m:
        return 0.0
    brut = m.group(1).strip().replace(" ", "")
    # Cas europeen : virgule decimale, point milliers
    if "," in brut and "." in brut:
        brut = brut.replace(".", "").replace(",", ".")
    elif "," in brut:
        brut = brut.replace(",", ".")
    try:
        return float(brut)
    except ValueError:
        return 0.0


def _parse_int(txt: str) -> int:
    if not txt:
        return 0
    m = re.search(r"(\d[\d\s\.]*)", txt.replace("\u00a0", " "))
    if not m:
        return 0
    try:
        return int(m.group(1).replace(" ", "").replace(".", ""))
    except ValueError:
        return 0


def _extract_lot_id(url: str) -> str:
    """Extrait l'ID numerique d'un lot a partir de son URL B-Stock."""
    m = re.search(r"/id/(\d+)", url or "")
    return m.group(1) if m else (url or "")


# ---------------------------------------------------------------------------
# TEST : validation que patchright passe Cloudflare Turnstile
# ---------------------------------------------------------------------------
def test_patchright_login() -> bool:
    """
    Teste si patchright (fork anti-detection de Playwright) passe le
    challenge Cloudflare Turnstile sur la page de signin B-Stock.

    Ouvre un contexte persistant sur PROFILE_DIR, navigue vers signin,
    attend 10 secondes, affiche URL + titre puis ferme.

    Retourne True si le titre n'est PAS "Just a moment..." (ou equivalent
    localise), False sinon.
    """
    try:
        from patchright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        print("[test-patchright] Erreur : patchright n'est pas installe.")
        return False

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # Patchright recommande explicitement :
    #   - channel='chrome' (plutot que le Chromium bundle)
    #   - pas de args anti-detection manuels
    #   - pas de viewport / user_agent custom (fingerprint mismatch)
    #   - pas d'ignore_default_args
    launch_kwargs: dict[str, Any] = {
        "headless": False,
        "locale": "fr-FR",
        "accept_downloads": True,
        "no_viewport": True,
    }

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            channel="chrome",   # patchright sait utiliser le Chrome installe
            **launch_kwargs,
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            print(f"[test-patchright] Goto : {URL_LOGIN}")
            page.goto(URL_LOGIN, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(10000)  # laisse le temps a Cloudflare

            url = page.url
            title = page.title()
            print("=" * 70)
            print(f"[test-patchright] URL finale : {url}")
            print(f"[test-patchright] Titre      : {title}")

            # Indicateurs d'un challenge Cloudflare encore actif
            cf_markers = [
                "just a moment",
                "un instant",
                "checking your browser",
                "verification",
            ]
            title_lower = (title or "").lower()
            is_cf_blocked = any(m in title_lower for m in cf_markers)

            # Compte les inputs visibles (le vrai formulaire login a un champ email)
            try:
                n_email = page.locator("input[type='email']").count()
                n_password = page.locator("input[type='password']").count()
                n_inputs_total = page.locator("input").count()
                print(f"[test-patchright] inputs total={n_inputs_total} "
                      f"email={n_email} password={n_password}")
            except Exception as exc:
                print(f"[test-patchright] count inputs error : {exc}")
                n_email = 0

            print("=" * 70)
            passed = (not is_cf_blocked) and n_email > 0
            if passed:
                print("[test-patchright] SUCCES : Cloudflare semble passe, "
                      "formulaire login visible.")
            else:
                print("[test-patchright] ECHEC : Cloudflare bloque toujours "
                      "OU le formulaire n'est pas charge.")
            return passed
        finally:
            try:
                context.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 1. SETUP PROFIL CHROME PERSISTANT (connexion manuelle initiale)
# ---------------------------------------------------------------------------
@_in_playwright_thread
def setup_profile(timeout_seconds: int = 120) -> tuple[bool, str]:
    """
    Ouvre Chrome sur la page de connexion B-Stock et laisse l'utilisateur
    se connecter manuellement pendant `timeout_seconds`. Le succes est
    detecte quand l'URL :
      - ne contient plus "signin"
      - contient toujours "bstock.com" (on evite les pages d'erreur ou
        redirections externes inattendues)

    Pendant l'attente, l'URL courante est loggee toutes les 5 secondes
    via print() (visible dans le terminal Streamlit).

    Headless=False obligatoire : l'utilisateur doit pouvoir interagir.

    Retourne (succes, message).
    """
    sync_playwright = _import_playwright()
    with sync_playwright() as pw:
        context = _launch_persistent(pw, headless=False)
        page = _get_or_create_page(context)
        try:
            page.goto(URL_LOGIN, wait_until="domcontentloaded", timeout=30000)

            print("=" * 70)
            print(f"[bstock] Navigateur ouvert. Connectez-vous a B-Stock manuellement.")
            print(f"[bstock] Vous avez {timeout_seconds} secondes.")
            print("=" * 70)
            print(f"[bstock] URL initiale : {page.url}")

            deadline = time.time() + timeout_seconds
            last_log = time.time()
            start = time.time()

            while time.time() < deadline:
                try:
                    current = page.url
                except Exception:
                    current = ""

                # Log de progression toutes les 5 secondes
                if time.time() - last_log >= 5:
                    elapsed = int(time.time() - start)
                    remaining = int(deadline - time.time())
                    print(f"[bstock] t+{elapsed}s (reste {remaining}s) -> URL : {current}")
                    last_log = time.time()

                # Critere de succes combine
                low = current.lower()
                if current and "signin" not in low and "bstock.com" in low:
                    # Petite attente pour laisser les cookies de session se poser
                    page.wait_for_timeout(1500)
                    print(f"[bstock] Succes : profil sauvegarde. URL finale : {page.url}")
                    return True, (
                        f"Profil sauvegarde dans {PROFILE_DIR.name}. "
                        f"URL finale : {page.url}"
                    )
                page.wait_for_timeout(1000)

            print(f"[bstock] Timeout {timeout_seconds}s atteint. URL finale : {page.url}")
            return False, (
                f"Timeout {timeout_seconds}s - connexion non detectee. "
                f"URL courante : {page.url}"
            )
        except Exception as exc:
            return False, f"Erreur pendant le setup : {type(exc).__name__} : {exc}"
        finally:
            # Fermeture propre -> le profil persiste sur disque
            try:
                context.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# DEBUG : sniff des requetes reseau pour trouver l'API JSON des lots
# ---------------------------------------------------------------------------
@_in_playwright_thread
def sniff_api() -> dict:
    """
    Sniffe le trafic reseau pendant le chargement de la page des lots Europe.
    Affiche dans le terminal toutes les requetes dont l'URL contient un des
    mots-cles API (api, auction, lot, listing, graphql, search, query) et
    sauvegarde le resultat complet dans sniff_results.json.
    """
    if not is_profile_configured():
        raise RuntimeError(
            "Profil B-Stock non configure. "
            "Lance d'abord setup_profile() depuis la page Parametres."
        )

    import json as _json

    # Windows cp1252 peut crash sur caracteres Unicode -> on force stdout en UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    def _sp(msg):
        """Print defensif qui remplace les caracteres non-encodables."""
        try:
            print(msg)
        except UnicodeEncodeError:
            print(str(msg).encode("ascii", errors="replace").decode("ascii"))

    keywords = ("api", "auction", "lot", "listing", "graphql", "search", "query")
    captured: list[dict] = []      # requetes matchant un keyword
    xhr_fetch: list[dict] = []     # toutes les requetes XHR / fetch (meme non-matchees)

    def _matches(url: str) -> bool:
        low = url.lower()
        return any(k in low for k in keywords)

    sync_playwright = _import_playwright()
    with sync_playwright() as pw:
        context = _launch_persistent(pw, headless=False)
        page = _get_or_create_page(context)

        # ----------------------------------------------------------------
        # Handler pour les REQUETES (on voit tout ce qui part)
        # ----------------------------------------------------------------
        def on_request(request):
            try:
                rtype = request.resource_type
                url = request.url
                if rtype in ("xhr", "fetch"):
                    xhr_fetch.append({
                        "method": request.method,
                        "url": url,
                        "resource_type": rtype,
                        "headers": dict(request.headers),
                        "post_data": (request.post_data or "")[:500],
                    })
            except Exception as exc:
                print(f"[sniff] on_request err: {exc}")

        # ----------------------------------------------------------------
        # Handler pour les REPONSES (on capture le body)
        # ----------------------------------------------------------------
        def on_response(response):
            try:
                url = response.url
                if not _matches(url):
                    return
                req = response.request
                rtype = req.resource_type
                entry: dict = {
                    "url": url,
                    "status": response.status,
                    "method": req.method,
                    "resource_type": rtype,
                    "request_headers": dict(req.headers),
                    "response_headers": dict(response.headers),
                    "post_data": (req.post_data or "")[:2000],
                }
                # Tentative de recuperation du body (en priorite JSON)
                body_txt = ""
                try:
                    body_bytes = response.body()
                    body_txt = body_bytes.decode("utf-8", errors="replace")
                except Exception as exc:
                    body_txt = f"<body indisponible: {exc}>"

                # Tentative de parse JSON
                parsed = None
                content_type = (response.headers.get("content-type", "") or "").lower()
                if "json" in content_type or body_txt.strip().startswith(("{", "[")):
                    try:
                        parsed = _json.loads(body_txt)
                    except Exception:
                        parsed = None

                entry["content_type"] = content_type
                entry["body_preview"] = body_txt[:500]
                entry["body_full_bytes"] = len(body_txt)
                entry["is_json"] = parsed is not None
                if parsed is not None:
                    # Stocke la version parsee complete pour le fichier final
                    entry["json"] = parsed
                captured.append(entry)
            except Exception as exc:
                print(f"[sniff] on_response err: {exc}")

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            print(f"[sniff] Goto : {URL_LOTS_EUROPE}")
            page.goto(URL_LOTS_EUROPE, wait_until="domcontentloaded", timeout=30000)

            # Attente de chargement + scrolling pour declencher le lazy-load
            print("[sniff] Attente 10s pour laisser partir les requetes initiales...")
            page.wait_for_timeout(10000)

            print("[sniff] Scroll x5 pour declencher le lazy-load...")
            for i in range(5):
                page.evaluate("window.scrollBy(0, 800)")
                page.wait_for_timeout(1000)

            # Petite attente finale
            page.wait_for_timeout(2000)

            # ------------------------------------------------------------
            # Sauvegarde D'ABORD (robustesse : meme si un print crash,
            # le fichier sniff_results.json sera deja ecrit)
            # ------------------------------------------------------------
            out_file = BASE_DIR / "sniff_results.json"
            payload = {
                "url_page": URL_LOTS_EUROPE,
                "captured_matching": captured,
                "all_xhr_fetch": xhr_fetch,
            }
            try:
                out_file.write_text(
                    _json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                _sp(f"[sniff] Resultats complets sauves dans : {out_file}")
            except Exception as exc:
                _sp(f"[sniff] Erreur sauvegarde : {exc}")

            # ------------------------------------------------------------
            # Rapport terminal
            # ------------------------------------------------------------
            _sp("=" * 70)
            _sp(f"[sniff] Total requetes matchant keywords : {len(captured)}")
            _sp(f"[sniff] Total XHR/fetch captures         : {len(xhr_fetch)}")
            _sp("=" * 70)

            _sp("\n[sniff] === REQUETES MATCHANT KEYWORDS ===\n")
            for i, e in enumerate(captured):
                _sp(f"--- [{i}] {e['method']} {e['status']} ({e['resource_type']}) ---")
                _sp(f"URL : {e['url']}")
                _sp(f"Content-Type : {e.get('content_type', '')}")
                auth = e["request_headers"].get("authorization", "")
                cookie = e["request_headers"].get("cookie", "")
                if auth:
                    _sp(f"Authorization : {auth[:120]}")
                if cookie:
                    _sp(f"Cookie : {cookie[:120]}...")
                if e.get("post_data"):
                    _sp(f"POST data : {e['post_data'][:300]}")
                _sp(f"Body ({e.get('body_full_bytes', 0)} octets) : {e.get('body_preview', '')}")
                _sp("")

            _sp("\n[sniff] === TOUTES LES REQUETES XHR/FETCH ===\n")
            for i, r in enumerate(xhr_fetch):
                _sp(f"[{i:3d}] {r['method']} {r['resource_type']} -> {r['url']}")

            return {
                "n_captured": len(captured),
                "n_xhr_fetch": len(xhr_fetch),
                "out_file": str(out_file),
            }
        finally:
            try:
                context.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# DEBUG : dump de la page des lots Europe
# ---------------------------------------------------------------------------
@_in_playwright_thread
def debug_lots_page() -> dict:
    """
    Ouvre la page des lots Europe avec le profil persistant, dump le HTML
    dans debug_page.html et affiche les candidats de selecteurs dans le
    terminal pour faciliter l'ajustement des scrapers.
    """
    if not is_profile_configured():
        raise RuntimeError(
            "Profil B-Stock non configure. "
            "Lance d'abord setup_profile() depuis la page Parametres."
        )

    sync_playwright = _import_playwright()
    with sync_playwright() as pw:
        context = _launch_persistent(pw, headless=False)
        page = _get_or_create_page(context)
        try:
            print(f"[debug] Goto : {URL_LOTS_EUROPE}")
            page.goto(URL_LOTS_EUROPE, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            final_url = page.url
            title = page.title()
            html = page.content()

            out_file = BASE_DIR / "debug_page.html"
            out_file.write_text(html, encoding="utf-8")

            print("=" * 70)
            print(f"[debug] URL finale : {final_url}")
            print(f"[debug] Titre      : {title}")
            print(f"[debug] HTML dump  : {out_file} ({len(html)} octets)")
            print("=" * 70)

            # --- Liens <a href> qui contiennent "auction" ---
            print("\n[debug] Liens <a> contenant 'auction' :")
            a_nodes = page.locator("a[href*='auction']")
            n_a = a_nodes.count()
            print(f"  total : {n_a}")
            seen_hrefs: set[str] = set()
            for i in range(min(n_a, 25)):
                try:
                    a = a_nodes.nth(i)
                    href = a.get_attribute("href") or ""
                    if href in seen_hrefs:
                        continue
                    seen_hrefs.add(href)
                    txt = (a.inner_text(timeout=500) or "").strip().replace("\n", " ")
                    print(f"  {i:3d}: href={href!r}")
                    if txt:
                        print(f"       text={txt[:80]!r}")
                except Exception as exc:
                    print(f"  {i:3d}: err={exc}")

            # --- Elements avec classes suspectes ---
            print("\n[debug] Elements avec class contenant card/lot/auction/item/listing :")
            class_keywords = ["card", "lot", "auction", "item", "listing"]
            for kw in class_keywords:
                sel = f"[class*='{kw}' i]"
                try:
                    loc = page.locator(sel)
                    cnt = loc.count()
                    print(f"  {sel:35s} -> {cnt} element(s)")
                    # Affiche les 3 premieres classes distinctes trouvees
                    classes_seen: set[str] = set()
                    for i in range(min(cnt, 50)):
                        try:
                            cls = loc.nth(i).get_attribute("class") or ""
                            if cls and cls not in classes_seen:
                                classes_seen.add(cls)
                                if len(classes_seen) <= 5:
                                    print(f"      class={cls[:120]!r}")
                        except Exception:
                            continue
                except Exception as exc:
                    print(f"  {sel:35s} -> erreur : {exc}")

            print("\n[debug] Termine.")
            return {
                "url": final_url,
                "title": title,
                "html_bytes": len(html),
                "html_path": str(out_file),
            }
        finally:
            try:
                context.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 2. LISTE DES LOTS EUROPE (via API HTTP directe)
# ---------------------------------------------------------------------------
def get_lots_europe(max_lots: int = 100, page_size: int = 50) -> list[dict]:
    """
    Recupere les lots disponibles en Europe via l'API search.bstock.com.
    Aucun navigateur, aucune authentification necessaire.

    - max_lots : nombre maximum de lots a recuperer (pagination automatique)
    - page_size: taille de page API (defaut 50, B-Stock supporte jusqu'a 100+)
    """
    lots: list[dict] = []
    offset = 0
    vus: set[str] = set()

    while len(lots) < max_lots:
        try:
            data = _post_listings(offset=offset, limit=page_size)
        except Exception as exc:
            print(f"[bstock] Erreur API listings (offset={offset}) : {exc}")
            break

        batch = data.get("listings") or []
        if not batch:
            break

        for raw in batch:
            mapped = _map_listing(raw)
            url = mapped["url"]
            if url and url not in vus:
                vus.add(url)
                lots.append(mapped)
                if len(lots) >= max_lots:
                    break

        # Fin de pagination : moins de resultats que la page demande
        if len(batch) < page_size:
            break
        offset += len(batch)

    return lots


# ---------------------------------------------------------------------------
# 3. DETAIL D'UN LOT (via API HTTP directe)
# ---------------------------------------------------------------------------
def get_lot_detail(url_lot: str, frais_bstock_pct: float = 5.0,
                   max_scan_pages: int = 30) -> dict:
    """
    Recupere la fiche detaillee d'un lot via l'API.

    Strategie : on pagine l'endpoint search/listings (region EU) jusqu'a
    trouver le lot dont le listingId correspond a celui extrait de l'URL.
    Les frais B-Stock sont calcules en % (parametre), les frais de
    livraison ne sont pas exposes par cette API et restent a 0 (l'utilisateur
    les saisira manuellement avant de prendre une decision).

    - url_lot          : URL B-Stock du lot (contient /id/NNNN/)
    - frais_bstock_pct : pourcentage de frais a appliquer (defaut 5%)
    - max_scan_pages   : nombre maximum de pages a scanner avant abandon
    """
    if not url_lot:
        raise ValueError("URL du lot vide.")

    target_id = _extract_lot_id(url_lot)
    if not target_id:
        raise ValueError(f"Impossible d'extraire le listingId de {url_lot}")

    # Pagination jusqu'a trouver le lot cible
    page_size = 100
    offset = 0
    for _ in range(max_scan_pages):
        try:
            data = _post_listings(offset=offset, limit=page_size)
        except Exception as exc:
            raise RuntimeError(f"Erreur API listings (offset={offset}) : {exc}") from exc

        batch = data.get("listings") or []
        if not batch:
            break

        for raw in batch:
            if str(raw.get("listingId") or "") == target_id:
                mapped = _map_listing(raw)

                enchere = mapped["enchere"]
                retail = mapped["retail_total"]
                nb_articles = mapped["nb_articles"]

                # Calculs financiers
                frais_bstock = round(enchere * frais_bstock_pct / 100, 2)
                # La livraison n'est pas exposee par l'API : saisie manuelle requise
                frais_livraison = 0.0
                cout_total = round(enchere + frais_bstock + frais_livraison, 2)
                ratio_retail = round(cout_total / retail * 100, 2) if retail > 0 else 0.0
                cout_moyen_unite = round(cout_total / nb_articles, 2) if nb_articles > 0 else 0.0

                # Enrichit le dict avec les champs attendus par marketplace.py
                mapped.update({
                    "frais_bstock":      frais_bstock,
                    "frais_livraison":   frais_livraison,
                    "cout_total":        cout_total,
                    "ratio_retail":      ratio_retail,
                    "cout_moyen_unite":  cout_moyen_unite,
                    "lien_manifeste":    "",  # telechargement via Playwright
                })
                return mapped

        if len(batch) < page_size:
            break
        offset += len(batch)

    raise RuntimeError(
        f"Lot {target_id} non trouve dans l'API (scan de {offset} lots). "
        f"Verifie que le lot existe encore et qu'il est dans la region Europe."
    )


# ---------------------------------------------------------------------------
# 4. TELECHARGEMENT DU MANIFESTE CSV
# ---------------------------------------------------------------------------
SESSION_EXPIRED_MSG = "Session expiree - reconfigurer le profil B-Stock"


def _is_logged_in(page) -> bool:
    """
    Verifie qu'un element accessible uniquement aux utilisateurs connectes
    est present dans le DOM. On cherche le menu compte / watchlist / logout.
    Robuste aux variations de DOM : plusieurs selecteurs candidats.
    """
    connected_selectors = [
        "a[href*='/acct/settings']",
        "a[href*='/acct/watchlist']",
        "a[href*='/acct/logout']",
        "a[href*='logout']",
        "button:has-text('My Account')",
        "button:has-text('Account')",
        "[data-testid*='account-menu']",
        "[data-testid*='user-menu']",
    ]
    for sel in connected_selectors:
        try:
            if page.locator(sel).first.count() > 0:
                return True
        except Exception:
            continue
    return False


@_in_playwright_thread
def download_manifest(url_lot: str, save_path: str | Path | None = None,
                      headless: bool = False) -> Path:
    """
    Telecharge le manifeste CSV du lot.
    Verifie d'abord que l'utilisateur est bien connecte avec le profil.
    Si save_path est None, sauvegarde dans downloads/manifest_<lot_id>.csv.

    Leve RuntimeError(SESSION_EXPIRED_MSG) si la session B-Stock a expire.
    """
    if not url_lot:
        raise ValueError("URL du lot vide.")
    if not is_profile_configured():
        raise RuntimeError(
            "Profil B-Stock non configure. "
            "Lance d'abord setup_profile() depuis la page Parametres."
        )

    lot_id = _extract_lot_id(url_lot)
    if save_path is None:
        save_path = DOWNLOADS_DIR / f"manifest_{lot_id}.csv"
    save_path = Path(save_path)

    sync_playwright = _import_playwright()
    with sync_playwright() as pw:
        context = _launch_persistent(pw, headless=headless)
        page = _get_or_create_page(context)
        try:
            # "domcontentloaded" est moins strict que "networkidle"
            # (networkidle echoue souvent sur les pages Next.js avec analytics)
            page.goto(url_lot, wait_until="domcontentloaded", timeout=30000)
            _accept_cookies(page)

            # Petite attente pour laisser React hydrater le header / menu
            page.wait_for_timeout(2500)

            # Verification explicite de la connexion.
            # Si on est redirige vers /signin, ou qu'aucun element "connecte"
            # n'est trouve, la session a expire.
            current_url = page.url.lower()
            if "signin" in current_url or "login" in current_url:
                raise RuntimeError(SESSION_EXPIRED_MSG)
            if not _is_logged_in(page):
                raise RuntimeError(SESSION_EXPIRED_MSG)

            # On cherche le lien de telechargement du manifeste
            dl_selectors = [
                "a:has-text('Manifest')",
                "a:has-text('manifest')",
                "a:has-text('Download')",
                "a[href*='manifest']",
                "a[href$='.csv']",
                "button:has-text('Manifest')",
            ]
            link = None
            for sel in dl_selectors:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        link = loc
                        break
                except Exception:
                    continue
            if link is None:
                raise RuntimeError(
                    "Lien de telechargement du manifeste introuvable. "
                    "Verifie que tu as acces a ce lot (eligibilite B-Stock)."
                )

            with page.expect_download(timeout=30000) as dl_info:
                link.click()
            download = dl_info.value
            download.save_as(str(save_path))
            return save_path
        finally:
            try:
                context.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 5. PARSING DU MANIFESTE + CALCULS ROI
# ---------------------------------------------------------------------------
def _read_csv_flex(csv_path: Path) -> pd.DataFrame:
    """Lit un CSV en tolerant plusieurs encodages / separateurs."""
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        for sep in (",", ";", "\t"):
            try:
                df = pd.read_csv(csv_path, encoding=enc, sep=sep)
                if df.shape[1] >= 3:
                    return df
            except Exception:
                continue
    raise RuntimeError(f"Impossible de lire le CSV {csv_path}")


def _find_col(df: pd.DataFrame, *candidats: str) -> str | None:
    """Trouve la premiere colonne correspondant a un des candidats (insensible a la casse)."""
    norm = {c.strip().lower(): c for c in df.columns}
    for cand in candidats:
        key = cand.strip().lower()
        if key in norm:
            return norm[key]
        for k, v in norm.items():
            if key in k:
                return v
    return None


def _score_from_marge(marge_pct: float) -> int:
    """Convertit une marge en % en un score ROI 1-5."""
    if marge_pct < 0:
        return 1
    if marge_pct < 20:
        return 2
    if marge_pct < 50:
        return 3
    if marge_pct < 100:
        return 4
    return 5


def _canal_from_retail(retail: float) -> str:
    if retail >= 200:
        return "LBC"
    if retail < 100:
        return "Vinted"
    return "eBay"


def parse_manifest(csv_path: str | Path, lot_data: dict) -> list[dict]:
    """
    Lit le CSV manifeste et retourne une liste d'articles enrichis :
    cout_reel alloue, prix_cible, marge estimee, score ROI, canal recommande.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    df = _read_csv_flex(csv_path)

    col_asin        = _find_col(df, "ASIN")
    col_ean         = _find_col(df, "EAN", "UPC")
    # "Item Desc" doit etre teste AVANT "DESCRIPTION" car _find_col
    # ferait match "GL DESCRIPTION" (= gl_kitchen) sur "DESCRIPTION".
    col_desc        = _find_col(df, "ITEM DESC", "Item Desc", "TITLE", "PRODUCT NAME")
    col_condition   = _find_col(df, "CONDITION")
    col_retail      = _find_col(df, "UNIT RETAIL", "RETAIL PRICE", "RETAIL", "MSRP")
    col_lpn         = _find_col(df, "LPN", "B-STOCK LPN")
    # DEPARTMENT = categorie principale (Kitchen, etc.)
    # CATEGORY   = sous-categorie (Floorcare, Hot Beverage Makers, etc.)
    col_categ       = _find_col(df, "DEPARTMENT", "CATEGORY", "CATEGORIE")
    col_scateg      = _find_col(df, "SUBCATEGORY", "CATEGORY", "SOUS CATEGORIE")

    # Somme des UNIT RETAIL pour l'allocation proportionnelle du cout total
    retail_series = df[col_retail].apply(_parse_money) if col_retail else pd.Series([0.0] * len(df))
    retail_total = float(retail_series.sum()) or 1.0  # evite la division par zero
    cout_total_lot = float(lot_data.get("cout_total") or 0.0)

    # Frais de remise en etat par defaut selon la condition
    FRAIS_REMISE_DEFAUT = {
        "warehouse damage": 0.0,
        "customer damage":  15.0,
        "carrier damage":   10.0,
        "defective":        35.0,
    }

    articles: list[dict] = []
    for idx, row in df.iterrows():
        retail = float(retail_series.iloc[idx])
        condition_raw = str(row[col_condition]) if col_condition else ""
        cond_key = condition_raw.strip().lower()
        coeff = CONDITION_COEFFS.get(cond_key, None)
        if coeff is None:
            for k, v in CONDITION_COEFFS.items():
                if k in cond_key:
                    coeff = v
                    break
        if coeff is None:
            coeff = DEFAULT_COEFF

        # Cout reel = part proportionnelle du cout total du lot
        cout_reel = round((retail / retail_total) * cout_total_lot, 2) if retail_total else 0.0
        prix_cible = round(retail * coeff, 2)

        # Frais de remise en etat par defaut
        frais_remise = 0.0
        for k, v in FRAIS_REMISE_DEFAUT.items():
            if k in cond_key:
                frais_remise = v
                break

        # Benefice net = prix_cible - cout_reel - frais_remise
        benef_net = round(prix_cible - cout_reel - frais_remise, 2)
        # Marge nette = benefice / (cout_reel + frais_remise) * 100
        cout_total_article = cout_reel + frais_remise
        marge_pct = (
            round(benef_net / cout_total_article * 100, 2)
            if cout_total_article > 0 else 0.0
        )
        score = _score_from_marge(marge_pct)
        canal = _canal_from_retail(retail)

        asin_raw = row[col_asin] if col_asin else ""
        asin = str(asin_raw).strip() if pd.notna(asin_raw) else ""
        if asin.lower() == "nan":
            asin = ""

        desc_raw = row[col_desc] if col_desc else ""
        desc = str(desc_raw).strip() if pd.notna(desc_raw) else ""
        if desc.lower() == "nan":
            desc = ""

        ean_raw = row[col_ean] if col_ean else ""
        ean_str = str(ean_raw).strip() if pd.notna(ean_raw) else ""
        if ean_str.lower() == "nan" or ean_str == "":
            ean_str = ""
        elif "." in ean_str:
            ean_str = ean_str.split(".")[0]

        categ_raw = row[col_categ] if col_categ else ""
        categ = str(categ_raw).strip() if pd.notna(categ_raw) else ""
        if categ.lower() == "nan":
            categ = ""

        scateg_raw = row[col_scateg] if col_scateg else ""
        scateg = str(scateg_raw).strip() if pd.notna(scateg_raw) else ""
        if scateg.lower() == "nan":
            scateg = ""

        lpn_raw = row[col_lpn] if col_lpn else ""
        lpn = str(lpn_raw).strip() if pd.notna(lpn_raw) else ""
        if lpn.lower() == "nan":
            lpn = ""

        articles.append({
            "lot_id":           lot_data.get("lot_id", ""),
            "lpn":              lpn,
            "asin":             asin,
            "ean":              ean_str,
            "description":      desc,
            "condition":        condition_raw.strip(),
            "categorie":        categ,
            "sous_categorie":   scateg,
            "retail_price":     retail,
            "cout_reel":        cout_reel,
            "frais_remise":     frais_remise,
            "prix_cible":       prix_cible,
            "benef_net":        benef_net,
            "marge_estimee":    marge_pct,
            "score_roi":        score,
            "canal_recommande": canal,
            "lien_amazon":      f"https://www.amazon.fr/dp/{asin}" if asin else "",
        })
    return articles
