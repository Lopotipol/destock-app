# -*- coding: utf-8 -*-
"""
DeStock App - scrapers/telegram_bot.py
Envoi d'alertes Telegram via l'API Bot.
"""

from __future__ import annotations

import requests


def send_message(token: str, chat_id: str, message: str) -> bool:
    """
    Envoie un message Telegram en HTML.
    Retourne True si succes, False sinon.
    """
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return r.status_code == 200 and r.json().get("ok", False)
    except Exception:
        return False


def test_connexion(token: str, chat_id: str) -> bool:
    return send_message(token, chat_id, "DeStock App connectee - alertes actives")


def alerte_nouveau_lot(token: str, chat_id: str, lot: dict) -> bool:
    msg = (
        f"<b>Nouveau lot B-Stock</b>\n"
        f"{lot.get('titre', '')[:80]}\n"
        f"Articles : {lot.get('nb_articles', 0)} | "
        f"Retail : {lot.get('retail_total', 0):,.0f} EUR\n"
        f"Score : {lot.get('score_total', '-')}/100 | "
        f"Ferme dans : {lot.get('ferme_dans', '-')}\n"
        f"<a href=\"{lot.get('url', '')}\">Voir sur B-Stock</a>"
    )
    return send_message(token, chat_id, msg)


def alerte_enchere_bientot_fermee(token: str, chat_id: str, lot: dict) -> bool:
    msg = (
        f"<b>URGENT - Enchere ferme bientot</b>\n"
        f"{lot.get('titre', '')[:80]}\n"
        f"Enchere actuelle : {lot.get('enchere', 0):,.0f} EUR\n"
        f"<a href=\"{lot.get('url', '')}\">Voir sur B-Stock</a>"
    )
    return send_message(token, chat_id, msg)


def alerte_stock_mort(token: str, chat_id: str, article: dict) -> bool:
    prix = article.get("prix_cible", 0)
    msg = (
        f"<b>Stock mort - {article.get('jours', 0)} jours sans vente</b>\n"
        f"{article.get('description', '')[:80]}\n"
        f"Prix actuel : {prix:,.0f} EUR\n"
        f"Suggestion : baisser a {prix * 0.8:,.0f} EUR"
    )
    return send_message(token, chat_id, msg)


def alerte_vente(token: str, chat_id: str, vente: dict) -> bool:
    msg = (
        f"<b>Vente enregistree !</b>\n"
        f"{vente.get('description', '')[:80]}\n"
        f"Prix : {vente.get('prix_vente', 0):,.0f} EUR via {vente.get('canal', '')}\n"
        f"Benefice : {vente.get('benefice', 0):,.0f} EUR"
    )
    return send_message(token, chat_id, msg)
