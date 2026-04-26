#!/usr/bin/env python3
"""
AAT Bridge Setup
Führt die Ersteinrichtung der Bridge durch:
  1. Token-A generieren
  2. Token-A anzeigen → User trägt es im Webportal ein
  3. OTT vom User entgegennehmen
  4. OTT beim Server einlösen → Token-B + MQTT-Daten erhalten
  5. Agent-Endpunkt konfigurieren
  6. config.json speichern

Usage:
  python3 setup.py
  oder: ./start.sh --setup
"""

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import requests

CONFIG_FILE = Path(__file__).parent / 'config.json'
DEFAULT_SERVER = 'https://ai-agent-tasks.computeq.de'


def generate_token_a():
    return secrets.token_hex(32)  # 64 Zeichen hex


def load_or_create_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(cfg):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)
    os.chmod(CONFIG_FILE, 0o600)


def install_service(install_dir):
    """Installiert aat_bridge als systemd User-Service"""
    service_template = Path(__file__).parent / 'aat_bridge.service'
    if not service_template.exists():
        print("  ⚠ aat_bridge.service nicht gefunden, übersprungen.")
        return

    content = service_template.read_text()
    content = content.replace('__INSTALL_DIR__', str(install_dir))

    systemd_dir = Path.home() / '.config' / 'systemd' / 'user'
    systemd_dir.mkdir(parents=True, exist_ok=True)

    dest = systemd_dir / 'aat_bridge.service'
    dest.write_text(content)
    print(f"  ✓ Service-Datei geschrieben: {dest}")

    try:
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', '--user', 'enable', '--now', 'aat_bridge'], check=True)
        print("  ✓ Service aktiviert und gestartet")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠ systemctl Fehler: {e}")
        print("  Manuell: systemctl --user enable --now aat_bridge")
    except FileNotFoundError:
        print("  ⚠ systemctl nicht gefunden. Service manuell installieren:")
        print(f"  cp {dest} ~/.config/systemd/user/")
        print("  systemctl --user enable --now aat_bridge")


def main():
    print()
    print("=" * 60)
    print("  AAT Bridge Setup — AI Agent Tasks")
    print("=" * 60)
    print()

    cfg = load_or_create_config()

    # ── Neu konfigurieren? ────────────────────
    if cfg.get('token_a') and cfg.get('token_b'):
        print("Bestehende Konfiguration gefunden.")
        reconfig = input("Neu konfigurieren? (j/N): ").strip().lower()
        if reconfig != 'j':
            print("Setup abgebrochen. Bestehende config.json bleibt erhalten.")
            sys.exit(0)

    # ── Token-A generieren ────────────────────
    token_a = generate_token_a()
    cfg['token_a'] = token_a
    cfg.pop('token_b', None)
    save_config(cfg)

    print()
    print("=" * 60)
    print("  SCHRITT 1: Token-A")
    print("=" * 60)
    print()
    print("  Dein Token-A (Bridge-Identifikation):")
    print()
    print(f"  >>> {token_a} <<<")
    print()
    print("  Trage diesen Token jetzt in dein Webportal ein:")
    print(f"  {DEFAULT_SERVER}/index.php")
    print()
    print("  Das Portal gibt dir einen One-Time-Token (OTT) zurück.")
    print()
    input("  Drücke ENTER wenn du den OTT erhalten hast...")

    # ── OTT einlösen ──────────────────────────
    print()
    print("=" * 60)
    print("  SCHRITT 2: OTT einlösen")
    print("=" * 60)
    print()
    ott = input("  OTT aus dem Webportal eingeben: ").strip()

    if not ott:
        print("Kein OTT eingegeben. Abbruch.")
        sys.exit(1)

    print()
    print("  Verbinde mit Server...")

    try:
        r = requests.post(
            DEFAULT_SERVER + '/redeem_ott.php',
            json={'token_a': token_a, 'ott': ott},
            timeout=15,
        )
        data = r.json()
    except Exception as e:
        print(f"\n  Fehler beim Server-Aufruf: {e}")
        sys.exit(1)

    if r.status_code != 200 or 'token_b' not in data:
        err = data.get('error', 'unknown')
        print(f"\n  Fehler: {err}")
        if err == 'ott_expired':
            print("  Der OTT ist abgelaufen (10 Min). Bitte neu anfordern.")
        elif err == 'ott_already_used':
            print("  Dieser OTT wurde bereits verwendet.")
        elif err == 'token_a_not_found':
            print("  Token-A nicht gefunden. Bitte Token-A im Portal eintragen.")
        sys.exit(1)

    # MQTT-Daten + Token-B vom Server übernehmen
    cfg['token_b']       = data['token_b']
    cfg['mqtt_host']     = data['mqtt_host']
    cfg['mqtt_port']     = data['mqtt_port']
    cfg['mqtt_user']     = data['mqtt_user']
    cfg['mqtt_password'] = data['mqtt_password']
    cfg['mqtt_tls']      = data.get('mqtt_tls', False)
    cfg['server_url']    = data.get('server_url', DEFAULT_SERVER)

    print("  ✓ Token-B erhalten")
    print("  ✓ MQTT-Zugangsdaten erhalten")

    # ── Agent konfigurieren ───────────────────
    print()
    print("=" * 60)
    print("  SCHRITT 3: KI-Agent konfigurieren")
    print("=" * 60)
    print()

    default_endpoint = cfg.get('agent_endpoint', 'http://localhost:18796/v1')
    agent_endpoint = input(f"  Agent Endpoint URL [{default_endpoint}]: ").strip()
    cfg['agent_endpoint'] = agent_endpoint or default_endpoint

    default_token = cfg.get('agent_token', '')
    agent_token = input(f"  Agent API Token [{default_token or 'keiner'}]: ").strip()
    cfg['agent_token'] = agent_token or default_token

    default_model = cfg.get('agent_model', 'chatcompletion')
    agent_model = input(f"  Agent Model [{default_model}]: ").strip()
    cfg['agent_model'] = agent_model or default_model

    default_timeout = cfg.get('agent_timeout', 120)
    timeout_str = input(f"  Agent Timeout Sekunden [{default_timeout}]: ").strip()
    cfg['agent_timeout'] = int(timeout_str) if timeout_str.isdigit() else default_timeout

    cfg['lang'] = 'DE'

    # ── Speichern ─────────────────────────────
    save_config(cfg)
    print("  ✓ config.json gespeichert (Berechtigungen: 600).")

    # ── Service installieren ───────────────────
    print()
    print("=" * 60)
    print("  SCHRITT 4: Systemd User-Service")
    print("=" * 60)
    print()
    print("  Der Bridge-Service läuft als dein Benutzer (kein sudo nötig).")
    print()
    ans = input("  Service jetzt einrichten und starten? (J/n): ").strip().lower()
    if ans != 'n':
        install_service(Path(__file__).parent)
        print()
        print("  Tipp: Damit der Service auch ohne Login startet:")
        print("  loginctl enable-linger $USER")
    else:
        install_dir = Path(__file__).parent
        print()
        print("  Manuell einrichten:")
        print(f"  python3 -c \"")
        print(f"    from pathlib import Path; import setup")
        print(f"    setup.install_service(Path('{install_dir}'))\"")
        print("  oder: systemctl --user enable --now aat_bridge")

    print()
    print("=" * 60)
    print("  Setup abgeschlossen!")
    print("=" * 60)
    print()
    print("  Bridge manuell starten (ohne Service):")
    print("  ./start.sh")
    print()
    print("  Service-Befehle:")
    print("  systemctl --user status aat_bridge")
    print("  systemctl --user restart aat_bridge")
    print("  journalctl --user -u aat_bridge -f")
    print()


if __name__ == '__main__':
    main()