# Payback Coupon Auto-Aktivierer

Automatisiert das Aktivieren **deiner eigenen** Payback-eCoupons auf
`payback.de`. Das Skript macht nichts anderes als das, was du manuell im
Browser tust: einloggen → Coupon-Center öffnen → alle Coupons aktivieren.
Ideal als täglicher Cron-Job / systemd-Timer auf einem Linux-Server.

## Warum Browser-Automation statt „API reverse engineeren"?

Die Payback-App nutzt eine private API mit Certificate Pinning und
App-Signaturen, die sich mit jedem Update ändern können. Ein darauf
aufgebautes Skript wäre fragil und ginge nur mit gerootetem Gerät + Frida
überhaupt zu bauen. Die **Website** bietet dieselben Coupons über denselben
Login — die Automation darüber ist stabil, legitim (dein Account, deine
Aktion) und server-tauglich. Genau diesen Weg nutzen auch die gängigen
Open-Source-Tools.

## Voraussetzungen

- Python 3.9+
- Ein Payback-Account (du gibst deine Zugangsdaten per `.env` an)

## Installation

```bash
git clone <dieses-repo> payback && cd payback

# virtuelle Umgebung (empfohlen)
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium          # lädt den Headless-Browser
# Falls 'playwright install' Systempakete braucht:
#   playwright install-deps chromium
```

## Zugangsdaten hinterlegen

```bash
cp .env.example .env
nano .env        # PAYBACK_USER und PAYBACK_PASS eintragen
```

Die `.env` steht in `.gitignore` und wird **nie** eingecheckt.

## Nutzung

```bash
set -a; source .env; set +a      # .env laden

python3 payback_coupons.py              # headless, aktiviert alle Coupons
python3 payback_coupons.py --dry-run    # nur zählen, nichts klicken
python3 payback_coupons.py --debug      # sichtbarer Browser + Screenshots
```

**Rückgabecodes:** `0` Erfolg · `1` Login fehlgeschlagen ·
`2` Seitenstruktur unerwartet · `3` Zugangsdaten fehlen.

## Selektoren anpassen (falls nötig)

Payback ändert gelegentlich Button-Texte und Seitenaufbau. Das Skript sucht
**text-basiert** (robuster als CSS-Klassen). Oben in `payback_coupons.py`
findest du die Listen, die du bei Bedarf in ~2 Minuten anpasst:

- `COOKIE_ACCEPT_TEXTS` – Text des Cookie-Zustimmen-Buttons
- `ACTIVATE_ALL_TEXTS` – Text des „Alle aktivieren"-Buttons
- `ACTIVATE_ONE_TEXTS` – Text der Einzel-„Aktivieren"-Buttons
- `ALREADY_ACTIVE_TEXTS` – kennzeichnet bereits aktivierte Coupons

Mit `--debug` erzeugst du Screenshots in `screenshots/`, an denen du die
echten Button-Beschriftungen ablesen und die Listen ergänzen kannst.

## Als täglicher Job einrichten

### Variante A: Cron

```bash
crontab -e
# Täglich 08:17 Uhr:
17 8 * * * cd /opt/payback && set -a && . ./.env && set +a && \
  /opt/payback/.venv/bin/python payback_coupons.py >> /var/log/payback.log 2>&1
```

### Variante B: systemd-Timer

Die mitgelieferten `systemd-payback-coupons.service` und `.timer` nach Anpassung
der Pfade installieren:

```bash
sudo cp systemd-payback-coupons.service /etc/systemd/system/
sudo cp systemd-payback-coupons.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now systemd-payback-coupons.timer
systemctl list-timers | grep payback          # prüfen
```

## Sicherheit & Fairness

- Zugangsdaten nur in `.env` (bzw. systemd `EnvironmentFile`), nie im Code.
- Service nicht als `root` laufen lassen (eigener User im Unit-File).
- Das Skript arbeitet mit Pausen zwischen Klicks und läuft einmal täglich —
  kein Dauerlast-Polling gegen Payback.
- Automatisiert ausschließlich deine eigenen Konto-Aktionen.

## Troubleshooting

| Symptom | Ursache / Lösung |
|---|---|
| Exit 3 | `.env` nicht geladen → `set -a; source .env; set +a` |
| Exit 1 | Login abgelehnt → Zugangsdaten prüfen; mit `--debug` Screenshot ansehen |
| Exit 2 | Button nicht gefunden → `--debug` ausführen, Texte oben im Skript anpassen |
| Browser startet nicht | `playwright install chromium` bzw. `install-deps` ausführen |
