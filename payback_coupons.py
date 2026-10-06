#!/usr/bin/env python3
"""
Payback Coupon Auto-Aktivierer
==============================

Loggt sich mit DEINEN eigenen Zugangsdaten auf payback.de ein und aktiviert
alle verfuegbaren eCoupons. Reine Automatisierung deiner eigenen, ohnehin
manuell durchfuehrbaren Aktionen - es wird nichts umgangen oder manipuliert.

Steuerung komplett ueber Umgebungsvariablen (siehe .env.example) bzw. CLI-Flags.

Nutzung:
    python3 payback_coupons.py              # headless, aktiviert alles
    python3 payback_coupons.py --debug      # sichtbarer Browser + Screenshots
    python3 payback_coupons.py --dry-run    # nur zaehlen, nichts klicken

Rueckgabecodes:
    0  Erfolg (Coupons aktiviert oder nichts zu tun)
    1  Login fehlgeschlagen
    2  Coupon-Seite nicht erreichbar / Struktur unerwartet
    3  Konfigurationsfehler (fehlende Zugangsdaten)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import (
        Page,
        TimeoutError as PWTimeout,
        sync_playwright,
    )
except ImportError:
    sys.stderr.write(
        "Playwright fehlt. Installieren mit:\n"
        "    pip install -r requirements.txt\n"
        "    playwright install chromium\n"
    )
    sys.exit(3)


# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #

LOGIN_URL = os.environ.get("PAYBACK_LOGIN_URL", "https://www.payback.de/login")
COUPONS_URL = os.environ.get("PAYBACK_COUPONS_URL", "https://www.payback.de/coupons")

# Text-basierte Selektoren sind robuster als CSS-Klassen, weil Payback die
# Klassennamen regelmaessig aendert. Diese Listen kannst du bei Bedarf anpassen.

COOKIE_ACCEPT_TEXTS = [
    "Alle akzeptieren",
    "Alle Cookies akzeptieren",
    "Akzeptieren",
    "Zustimmen",
    "Alle zulassen",
    "Accept All",
]

# Button, der alle Coupons auf einmal aktiviert (falls vorhanden)
ACTIVATE_ALL_TEXTS = [
    "Alle aktivieren",
    "Alle Coupons aktivieren",
    "Jetzt alle aktivieren",
]

# Einzel-Aktivieren-Buttons (Fallback, wenn es kein "Alle aktivieren" gibt)
ACTIVATE_ONE_TEXTS = [
    "Aktivieren",
    "Jetzt aktivieren",
    "Coupon aktivieren",
]

# Texte, die einen bereits aktivierten Coupon kennzeichnen (werden uebersprungen)
ALREADY_ACTIVE_TEXTS = [
    "Aktiviert",
    "aktiviert",
    "Eingeloest",
]

SCREENSHOT_DIR = Path(os.environ.get("PAYBACK_SCREENSHOT_DIR", "screenshots"))
DEFAULT_TIMEOUT_MS = int(os.environ.get("PAYBACK_TIMEOUT_MS", "20000"))


def log(msg: str) -> None:
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def shot(page: Page, name: str) -> None:
    """Screenshot zur Fehlersuche speichern (best effort)."""
    try:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOT_DIR / f"{name}.png"
        page.screenshot(path=str(path), full_page=True)
        log(f"Screenshot gespeichert: {path}")
    except Exception as exc:  # noqa: BLE001
        log(f"Screenshot fehlgeschlagen ({name}): {exc}")


# --------------------------------------------------------------------------- #
# Hilfsfunktionen fuer die Interaktion
# --------------------------------------------------------------------------- #

def click_first_matching(page: Page, texts: list[str], timeout: int = 4000) -> bool:
    """Klickt den ersten sichtbaren Button, dessen Text passt. True bei Erfolg."""
    for text in texts:
        try:
            loc = page.get_by_role("button", name=text, exact=False)
            if loc.count() == 0:
                # Manche Buttons sind <a> oder <div> - breiter suchen
                loc = page.locator(
                    f"xpath=//*[self::button or self::a or @role='button']"
                    f"[contains(normalize-space(.), {_xpath_lit(text)})]"
                )
            if loc.count() > 0:
                el = loc.first
                el.scroll_into_view_if_needed(timeout=timeout)
                el.click(timeout=timeout)
                return True
        except PWTimeout:
            continue
        except Exception:  # noqa: BLE001
            continue
    return False


def _xpath_lit(value: str) -> str:
    """String sicher in ein XPath-Literal verwandeln (Umgang mit Quotes)."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"


def dismiss_cookie_banner(page: Page) -> None:
    # Cookie-Banner liegt oft in einem iFrame (Usercentrics o.ae.). Beide Wege testen.
    if click_first_matching(page, COOKIE_ACCEPT_TEXTS, timeout=5000):
        log("Cookie-Banner akzeptiert.")
        page.wait_for_timeout(1000)
        return
    for frame in page.frames:
        for text in COOKIE_ACCEPT_TEXTS:
            try:
                loc = frame.get_by_role("button", name=text, exact=False)
                if loc.count() > 0:
                    loc.first.click(timeout=4000)
                    log(f"Cookie-Banner (iframe) akzeptiert: '{text}'")
                    page.wait_for_timeout(1000)
                    return
            except Exception:  # noqa: BLE001
                continue
    log("Kein Cookie-Banner gefunden (evtl. nicht noetig).")


# --------------------------------------------------------------------------- #
# Hauptablauf
# --------------------------------------------------------------------------- #

def login(page: Page, user: str, password: str, debug: bool) -> bool:
    log(f"Oeffne Login-Seite: {LOGIN_URL}")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    dismiss_cookie_banner(page)

    # Benutzername/Kartennummer-Feld finden (verschiedene moegliche Attribute).
    # Reihenfolge gemaess Live-Pruefung: autocomplete='username' zuerst.
    user_selectors = [
        "input[autocomplete='username']",
        "input[name='username']",
        "input[name='email']",
        "input[type='email']",
        "input#username",
    ]
    pass_selectors = [
        "input[autocomplete='current-password']",
        "input[type='password']",
        "input[name='password']",
        "input#password",
    ]
    # Payback nutzt einen zweistufigen Login (erst Kennung -> "Weiter" -> Passwort).
    next_texts = ["Weiter", "Fortfahren", "Next"]
    submit_texts = ["Anmelden", "Einloggen", "Login", "Jetzt anmelden", "Weiter"]

    if not _fill_first(page, user_selectors, user):
        log("FEHLER: Login-Feld (Benutzer) nicht gefunden.")
        shot(page, "login_user_not_found")
        return False

    # Ist das Passwortfeld schon sichtbar? Dann einstufiger Login.
    if not _password_visible(page, pass_selectors):
        # Zweistufig: "Weiter" klicken und auf Passwortfeld warten.
        if not click_first_matching(page, next_texts, timeout=5000):
            page.keyboard.press("Enter")
        if debug:
            shot(page, "after_username_step")
        if not _wait_for_password(page, pass_selectors):
            log("FEHLER: Passwort-Feld nach 'Weiter' nicht erschienen "
                "(Benutzerkennung evtl. unbekannt?).")
            shot(page, "login_no_password_step")
            return False

    if not _fill_first(page, pass_selectors, password):
        log("FEHLER: Passwort-Feld nicht gefunden.")
        shot(page, "login_pass_not_found")
        return False

    # Absenden: Button mit typischem Text oder Enter.
    if not click_first_matching(page, submit_texts, timeout=5000):
        page.keyboard.press("Enter")

    # Auf Navigation / Account-Bereich warten.
    try:
        page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
    except PWTimeout:
        pass
    page.wait_for_timeout(2000)

    if debug:
        shot(page, "after_login")

    # Heuristik: Login erfolgreich, wenn wir nicht mehr auf der Login-Seite sind
    # und/oder ein Logout-/Konto-Element existiert.
    current = page.url.lower()
    if "login" in current and "coupons" not in current:
        # Pruefen, ob eine Fehlermeldung sichtbar ist.
        body = page.content().lower()
        if "passwort" in body and ("falsch" in body or "fehler" in body):
            log("FEHLER: Login abgelehnt (falsche Zugangsdaten?).")
            shot(page, "login_failed")
            return False
    log("Login scheint erfolgreich.")
    return True


def _fill_first(page: Page, selectors: list[str], value: str) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.fill(value, timeout=5000)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _password_visible(page: Page, selectors: list[str]) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _wait_for_password(page: Page, selectors: list[str], tries: int = 15) -> bool:
    """Nach dem 'Weiter'-Schritt auf das Erscheinen des Passwortfelds warten."""
    for _ in range(tries):
        if _password_visible(page, selectors):
            return True
        page.wait_for_timeout(700)
    return False


def activate_coupons(page: Page, dry_run: bool, debug: bool) -> int:
    log(f"Oeffne Coupon-Seite: {COUPONS_URL}")
    page.goto(COUPONS_URL, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    dismiss_cookie_banner(page)
    try:
        page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
    except PWTimeout:
        pass
    page.wait_for_timeout(2000)

    if debug:
        shot(page, "coupons_page")

    activated = 0

    # 1) Bevorzugt den "Alle aktivieren"-Button nutzen.
    if not dry_run and click_first_matching(page, ACTIVATE_ALL_TEXTS, timeout=5000):
        log("'Alle aktivieren' geklickt.")
        page.wait_for_timeout(3000)
        if debug:
            shot(page, "after_activate_all")
        return -1  # -1 = Sammelaktion ausgefuehrt (exakte Zahl unbekannt)

    # 2) Fallback: einzelne Aktivieren-Buttons durchklicken, bis keiner mehr da ist.
    max_rounds = 60
    for round_no in range(max_rounds):
        # Alle aktuell sichtbaren Einzel-Buttons sammeln.
        buttons = _collect_activate_buttons(page)
        if not buttons:
            break
        if dry_run:
            log(f"[dry-run] {len(buttons)} aktivierbare Coupons gefunden "
                f"(Runde {round_no + 1}) - klicke nichts.")
            return len(buttons)

        clicked_this_round = 0
        for el in buttons:
            try:
                el.scroll_into_view_if_needed(timeout=3000)
                el.click(timeout=3000)
                activated += 1
                clicked_this_round += 1
                page.wait_for_timeout(400)
            except Exception:  # noqa: BLE001
                continue
        log(f"Runde {round_no + 1}: {clicked_this_round} Coupons aktiviert.")
        if clicked_this_round == 0:
            break
        page.wait_for_timeout(1500)  # DOM aktualisiert sich nach Aktivierung

    if debug:
        shot(page, "coupons_done")
    return activated


def _collect_activate_buttons(page: Page):
    """Sichtbare Aktivieren-Buttons einsammeln, bereits aktivierte ausschliessen."""
    result = []
    for text in ACTIVATE_ONE_TEXTS:
        loc = page.locator(
            f"xpath=//*[self::button or self::a or @role='button']"
            f"[contains(normalize-space(.), {_xpath_lit(text)})]"
        )
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                label = (el.inner_text() or "").strip()
                if any(a.lower() in label.lower() for a in ALREADY_ACTIVE_TEXTS):
                    continue
                result.append(el)
            except Exception:  # noqa: BLE001
                continue
    return result


def run(debug: bool, dry_run: bool) -> int:
    user = os.environ.get("PAYBACK_USER", "").strip()
    password = os.environ.get("PAYBACK_PASS", "")
    if not user or not password:
        log("FEHLER: PAYBACK_USER und/oder PAYBACK_PASS nicht gesetzt. "
            "Siehe .env.example.")
        return 3

    with sync_playwright() as p:
        launch_kwargs = {"headless": not debug}
        exe = os.environ.get("PAYBACK_CHROMIUM_EXECUTABLE")
        if exe:
            launch_kwargs["executable_path"] = exe
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            locale="de-DE",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        context.set_default_timeout(DEFAULT_TIMEOUT_MS)
        page = context.new_page()

        try:
            if not login(page, user, password, debug):
                return 1
            result = activate_coupons(page, dry_run, debug)
            if result == -1:
                log("Fertig: Sammelaktion 'Alle aktivieren' ausgefuehrt.")
            elif dry_run:
                log(f"Fertig (dry-run): {result} aktivierbare Coupons gefunden.")
            else:
                log(f"Fertig: {result} Coupons aktiviert.")
            return 0
        except PWTimeout as exc:
            log(f"FEHLER: Timeout - Seitenstruktur unerwartet. {exc}")
            shot(page, "timeout_error")
            return 2
        except Exception as exc:  # noqa: BLE001
            log(f"FEHLER: Unerwartet - {exc}")
            shot(page, "unexpected_error")
            return 2
        finally:
            context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Payback Coupon Auto-Aktivierer")
    parser.add_argument("--debug", action="store_true",
                        help="Sichtbarer Browser + Screenshots in jedem Schritt")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur zaehlen, nichts aktivieren")
    args = parser.parse_args()
    return run(debug=args.debug, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
