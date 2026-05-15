#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DeStock App - import_backup.py
Importe un backup JSON dans la nouvelle base Supabase.
Usage: python import_backup.py <chemin_backup.json>
"""

import sys
import json
import os
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import (
    Base, User, Parametre, Lot, Article, Reception,
    Vente, Annonce, AlerteLog, PrixCache, Template
)
from auth import hash_password


def import_backup(backup_file: str) -> dict:
    """Importe un backup JSON dans la base."""
    if not os.path.exists(backup_file):
        return {"success": False, "error": f"Fichier non trouvé: {backup_file}"}

    # Lire le backup
    try:
        with open(backup_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return {"success": False, "error": f"Erreur lecture JSON: {exc}"}

    # Connexion DB
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return {"success": False, "error": "DATABASE_URL non definie"}

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    try:
        engine = create_engine(db_url, future=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
    except Exception as exc:
        return {"success": False, "error": f"Erreur connexion DB: {exc}"}

    counts = {}
    try:
        # Mapping table_name -> (Model, primary_key_col)
        models = [
            (Parametre, "parametres"),
            (User, "users"),
            (Lot, "lots"),
            (Article, "articles"),
            (Reception, "receptions"),
            (Vente, "ventes"),
            (Annonce, "annonces"),
            (AlerteLog, "alertes_log"),
            (PrixCache, "prix_cache"),
            (Template, "templates"),
        ]

        for model, table_key in models:
            rows = data.get(table_key, [])
            n = 0
            for row in rows:
                try:
                    # Cas special : User avec mot_de_passe (hash si present)
                    if model == User and "mot_de_passe" in row and "mot_de_passe_hash" not in row:
                        row["mot_de_passe_hash"] = hash_password(row.pop("mot_de_passe"))

                    # Merge (update si existe, insert sinon)
                    obj = session.merge(model(**row))
                    n += 1
                except Exception as exc:
                    print(f"  [WARN] {table_key} ligne {n+1}: {exc}")
                    continue

            counts[table_key] = n
            print(f"✅ {table_key}: {n} lignes importees")

        session.commit()
        print("\n✅ Import termine avec succes!")
        return {"success": True, "counts": counts}

    except Exception as exc:
        session.rollback()
        return {"success": False, "error": f"Erreur import: {exc}"}
    finally:
        session.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_backup.py <backup.json>")
        sys.exit(1)

    backup_file = sys.argv[1]
    result = import_backup(backup_file)
    if result["success"]:
        print("\nCounts:")
        for k, v in result["counts"].items():
            print(f"  {k}: {v}")
    else:
        print(f"\n❌ Erreur: {result['error']}")
        sys.exit(1)
