#!/usr/bin/env python3
"""
AAT Bridge Setup
Führt die Ersteinrichtung der Bridge durch:
  1. Token-A generieren (falls noch nicht vorhanden)
  2. Token-A anzeigen → User trägt es im Webservice ein
  3. OTT vom User entgegennehmen
  4. OTT beim Server einlösen → Token-B erhalten
  5. config.json speichern

Usage:
  python3 setup.py
"""

import json
import os
import secrets
import sys
from pathlib import Path

import requests

CONFIG_FILE = Path(__file__).parent / 'config.json'


def generate_token_a():
    """Kryptographisch sicheres Token-A generieren"""
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


def main():
    print("=" * 60)
    print("  AAT Bridge Setup — AI Agent Tasks")
    print("=" * 60)
    print()

    cfg = load_or_create_config()

    # ── Server URL ────────────────────────────
    default_server = cfg.get('server_url', 'https://ai-agent-tasks.computeq.de')
    server_url = input(f"Server URL [{default_server}]: ").strip()
    if not server_url:
        server_url = default_server
    cfg['server_url'] = server_url

    # ── Token-A ───────────────────────────────
    if cfg.get('token_a') and cfg.get('token_b'):
        print(f"\nBestehende Konfiguration gefunden.")
        reconfig = input("Neu konfigurieren? (j/N): ").strip().lower()
        if reconfig != 'j':
            print("Setup abgebrochen. Bestehende config.json bleibt erhalten.")
            sys.exit(0)

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
    print(f"  {server_url}/account.php")
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
    print("  Löse OTT ein...")

    try:
        r = requests.post(
            server_url + '/redeem_ott.php',
            json={'token_a': token_a, 'ott': ott},
            timeout=15,
        )
        data = r.json()
    except Exception as e:
        print(f"\nFehler beim Server-Aufruf: {e}")
        sys.exit(1)

    if r.status_code != 200 or 'token_b' not in data:
        err = data.get('error', 'unknown')
        print(f"\nFehler: {err}")
        if err == 'ott_expired':
            print("Der OTT ist abgelaufen (10 Min). Bitte neu anfordern.")
        elif err == 'ott_already_used':
            print("Dieser OTT wurde bereits verwendet.")
        elif err == 'token_a_not_found':
            print("Token-A nicht gefunden. Bitte Token-A im Portal eintragen.")
        sys.exit(1)

    token_b = data['token_b']
    cfg['token_b'] = token_b

    # ── Agent-Konfiguration ───────────────────
    print()
    print("=" * 60)
    print("  SCHRITT 3: Agent konfigurieren")
    print("=" * 60)
    print()

    default_endpoint = cfg.get('agent_endpoint', 'http://localhost:3000/api/chat/completions')
    agent_endpoint = input(f"  Agent Endpoint URL [{default_endpoint}]: ").strip()
    if not agent_endpoint:
        agent_endpoint = default_endpoint
    cfg['agent_endpoint'] = agent_endpoint

    default_token = cfg.get('agent_token', '')
    agent_token = input(f"  Agent API Token [{default_token or 'keiner'}]: ").strip()
    if not agent_token:
        agent_token = default_token
    cfg['agent_token'] = agent_token

    default_model = cfg.get('agent_model', 'chatcompletion')
    agent_model = input(f"  Agent Model [{default_model}]: ").strip()
    if not agent_model:
        agent_model = default_model
    cfg['agent_model'] = agent_model

    default_timeout = cfg.get('agent_timeout', 120)
    timeout_str = input(f"  Agent Timeout in Sekunden [{default_timeout}]: ").strip()
    cfg['agent_timeout'] = int(timeout_str) if timeout_str.isdigit() else default_timeout

    # ── MQTT-Konfiguration ────────────────────
    print()
    print("=" * 60)
    print("  SCHRITT 4: MQTT konfigurieren")
    print("=" * 60)
    print()

    default_host = cfg.get('mqtt_host', 'mqtt.ai-agent-tasks.computeq.de')
    mqtt_host = input(f"  MQTT Host [{default_host}]: ").strip()
    if not mqtt_host:
        mqtt_host = default_host
    cfg['mqtt_host'] = mqtt_host

    default_port = cfg.get('mqtt_port', 1883)
    port_str = input(f"  MQTT Port [{default_port}]: ").strip()
    cfg['mqtt_port'] = int(port_str) if port_str.isdigit() else default_port

    default_user = cfg.get('mqtt_user', '')
    mqtt_user = input(f"  MQTT User [{default_user}]: ").strip()
    if not mqtt_user:
        mqtt_user = default_user
    cfg['mqtt_user'] = mqtt_user

    mqtt_password = input("  MQTT Passwort: ").strip()
    cfg['mqtt_password'] = mqtt_password

    tls_str = input("  TLS verwenden? (j/N): ").strip().lower()
    cfg['mqtt_tls'] = (tls_str == 'j')

    cfg['lang'] = 'DE'

    # ── Speichern ─────────────────────────────
    save_config(cfg)

    print()
    print("=" * 60)
    print("  Setup abgeschlossen!")
    print("=" * 60)
    print()
    print("  config.json wurde gespeichert.")
    print()
    print("  Bridge starten:")
    print("  python3 aat_bridge.py")
    print()
    print("  Als Systemd Service:")
    print("  sudo cp aat_bridge.service /etc/systemd/system/")
    print("  sudo systemctl enable --now aat_bridge")
    print()


if __name__ == '__main__':
    main()