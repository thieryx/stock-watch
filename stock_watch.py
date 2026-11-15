#!/usr/bin/env python3
"""
Surveille le stock d'une variante Shopify (ici YoungLA EU) et envoie une
notification push via ntfy.sh dès qu'elle repasse en stock.

Aucune dépendance : Python 3.8+ suffit.

Usage :
    python3 stock_watch.py            # boucle infinie (vérifie toutes les INTERVAL secondes)
    python3 stock_watch.py --once     # une seule vérification (pour cron / GitHub Actions)
    python3 stock_watch.py --test     # envoie une notif de test
"""

import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

# ---------------------------------------------------------------- CONFIG ----
PRODUCT_URL = "https://eu.youngla.com/products/5241"
VARIANT_ID = 53733990728043          # Heather Grey / Medium

# Choisis un nom de topic unique et difficile à deviner (n'importe qui
# connaissant le nom peut lire/écrire dessus). Abonne-toi au même nom
# dans l'app ntfy sur ton téléphone.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")  # via variable d'env / secret GitHub
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")

INTERVAL = int(os.environ.get("INTERVAL", "300"))   # secondes entre 2 vérifs (mode boucle)
STATE_FILE = os.environ.get("STATE_FILE", "stock_state.json")
# -----------------------------------------------------------------------------

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


class RateLimited(Exception):
    """Le site renvoie 429 même après plusieurs essais."""


def _get_json(url, attempts=4):
    """GET JSON avec retry/backoff sur 429 et erreurs 5xx."""
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                raise
            if i == attempts - 1:
                if e.code == 429:
                    raise RateLimited(url) from e
                raise
            retry_after = e.headers.get("Retry-After", "")
            delay = int(retry_after) if retry_after.isdigit() else 5 * 2 ** i
            delay = min(delay, 60) + random.uniform(0, 3)
            log(f"HTTP {e.code} sur {url}, nouvel essai dans {delay:.0f}s")
            time.sleep(delay)


def fetch_variant():
    """Retourne (titre_produit, titre_variante, disponible, prix_en_euros)."""
    data = _get_json(PRODUCT_URL.rstrip("/") + ".js")
    for v in data["variants"]:
        if v["id"] == VARIANT_ID:
            return data["title"], v["title"], bool(v["available"]), v["price"] / 100
    raise ValueError(f"Variante {VARIANT_ID} introuvable dans le produit")


def notify(title, message, priority="high", click=None):
    if not NTFY_TOPIC:
        raise SystemExit("NTFY_TOPIC non défini (variable d'environnement)")
    req = urllib.request.Request(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        method="POST",
        headers={
            "Title": title.encode("utf-8"),
            "Priority": priority,
            "Tags": "shopping_cart,tada",
            **({"Click": click} if click else {}),
        },
    )
    urllib.request.urlopen(req, timeout=20).read()


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f).get("available")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_state(available):
    with open(STATE_FILE, "w") as f:
        json.dump({"available": available, "checked": datetime.now().isoformat()}, f)


def check():
    product, variant, available, price = fetch_variant()
    previous = load_state()
    log(f"{product} — {variant} : {'EN STOCK' if available else 'rupture'}")

    # On notifie seulement au passage rupture -> en stock (pas de spam)
    if available and previous is not True:
        link = f"{PRODUCT_URL}?variant={VARIANT_ID}"
        notify(
            f"EN STOCK : {variant}",
            f"{product} ({variant}) est dispo à {price:.2f} € !",
            priority="urgent",
            click=link,
        )
        log("Notification envoyée")
    save_state(available)


def main():
    if "--test" in sys.argv:
        notify("Test stock_watch", "Les notifications fonctionnent 👍", priority="default")
        log("Notif de test envoyée")
        return
    if "--once" in sys.argv:
        try:
            check()
        except RateLimited:
            # IP partagée des runners GitHub souvent limitée par Shopify :
            # on saute cette vérif sans faire échouer le job (état inchangé).
            log("Rate-limité (429) malgré les essais, vérif sautée")
        return

    log(f"Surveillance démarrée (toutes les {INTERVAL}s) — Ctrl+C pour arrêter")
    while True:
        try:
            check()
        except Exception as e:  # réseau, site down, etc. : on réessaie plus tard
            log(f"Erreur : {e}")
        # petit jitter pour ne pas taper le site à heure fixe
        time.sleep(INTERVAL + random.randint(0, 30))


if __name__ == "__main__":
    main()
