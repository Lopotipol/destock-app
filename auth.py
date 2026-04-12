# -*- coding: utf-8 -*-
"""
DeStock App - auth.py
Authentification simple basee sur SHA256 + sel applicatif.

Fournit :
  - hash_password / verify_password : primitives de hashage
  - login_form : formulaire Streamlit de connexion
  - logout : deconnexion et nettoyage de la session
  - require_login : point d'entree utilise par app.py pour proteger les pages
"""

import hashlib

import streamlit as st

from config import APP_SECRET
from database import User, get_session


# ---------------------------------------------------------------------------
# Primitives de hashage
# ---------------------------------------------------------------------------
def hash_password(mot_de_passe: str) -> str:
    """Hash SHA256 du mot de passe prefixe par le sel applicatif."""
    payload = f"{APP_SECRET}:{mot_de_passe}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_password(mot_de_passe: str, hash_stocke: str) -> bool:
    """Verifie un mot de passe en clair contre son hash stocke."""
    return hash_password(mot_de_passe) == hash_stocke


# ---------------------------------------------------------------------------
# Logique de session Streamlit
# ---------------------------------------------------------------------------
def _authenticate(nom: str, mot_de_passe: str) -> User | None:
    """Cherche l'utilisateur en base et valide le mot de passe."""
    session = get_session()
    try:
        user = session.query(User).filter_by(nom=nom).first()
        if user and verify_password(mot_de_passe, user.mot_de_passe_hash):
            return user
        return None
    finally:
        session.close()


def login_form() -> None:
    """Affiche un formulaire de connexion centre."""
    st.title("DeStock App")
    st.caption("Connexion")

    with st.form("login_form", clear_on_submit=False):
        nom = st.text_input("Nom d'utilisateur")
        mot_de_passe = st.text_input("Mot de passe", type="password")
        submitted = st.form_submit_button("Se connecter", use_container_width=True)

    if submitted:
        user = _authenticate(nom.strip(), mot_de_passe)
        if user:
            # On stocke uniquement les infos non sensibles dans la session
            st.session_state["auth_user_id"] = user.id
            st.session_state["auth_user_nom"] = user.nom
            st.session_state["auth_user_role"] = user.role
            st.rerun()
        else:
            st.error("Nom d'utilisateur ou mot de passe invalide.")


def logout() -> None:
    """Efface les cles d'auth de la session et recharge la page."""
    for cle in ("auth_user_id", "auth_user_nom", "auth_user_role"):
        st.session_state.pop(cle, None)
    st.rerun()


def is_logged_in() -> bool:
    return bool(st.session_state.get("auth_user_id"))


def current_user_nom() -> str:
    return st.session_state.get("auth_user_nom", "")


def current_user_id() -> int | None:
    return st.session_state.get("auth_user_id")


def change_password(user_id: int, ancien: str, nouveau: str) -> tuple[bool, str]:
    """Change le mot de passe d'un utilisateur. Retourne (succes, message)."""
    session = get_session()
    try:
        user = session.query(User).filter_by(id=user_id).first()
        if not user:
            return False, "Utilisateur introuvable."
        if not verify_password(ancien, user.mot_de_passe_hash):
            return False, "Ancien mot de passe incorrect."
        if len(nouveau) < 4:
            return False, "Le nouveau mot de passe doit faire au moins 4 caracteres."
        user.mot_de_passe_hash = hash_password(nouveau)
        session.commit()
        return True, "Mot de passe mis a jour."
    finally:
        session.close()
