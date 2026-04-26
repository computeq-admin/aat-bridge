#!/usr/bin/env python3
"""
AAT Bridge — AI Agent Tasks
Verbindet Alexa (via MQTT Wakeup) mit dem lokalen KI-Agenten (OpenWebUI / OpenAI-compatible)

Ablauf:
  1. Bridge startet, liest config.json
  2. Subscribed auf MQTT Topic: aat/{token_a}
  3. Bei Wakeup-Message: holt Job von Server (Token-B)
  4. Übergibt Prompt an den KI-Agenten
  5. Schreibt Antwort zurück an Server (Token-B rotiert)

Installation:
  pip install paho-mqtt requests

Konfiguration: config.json im gleichen Verzeichnis
"""

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import requests

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('aat_bridge.log'),
    ]
)
log = logging.getLogger('aat_bridge')

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent / 'config.json'

def load_config():
    if not CONFIG_FILE.exists():
        log.error('config.json not found. Run setup first.')
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)

# ─────────────────────────────────────────────
# Server API calls
# ─────────────────────────────────────────────
def get_job(cfg):
    """Holt offenen Job vom Server, rotiert Token-B"""
    try:
        r = requests.post(
            cfg['server_url'] + '/get_job.php',
            json={'token_b': cfg['token_b']},
            timeout=10,
        )
        data = r.json()
    except Exception as e:
        log.error(f'get_job failed: {e}')
        return None

    # Token-B immer aktualisieren
    if 'token_b_new' in data:
        cfg['token_b'] = data['token_b_new']
        save_config(cfg)

    if r.status_code == 401:
        log.error('Token-B rejected by server. Re-registration required.')
        return None

    if data.get('status') == 'no_job':
        log.info('No pending job.')
        return None

    if 'job_id' in data:
        log.info(f"Job received: #{data['job_id']}")
        return data

    log.warning(f'Unexpected get_job response: {data}')
    return None


def send_pong(cfg):
    """Antwortet auf Server-Ping, rotiert Token-B"""
    try:
        r = requests.post(
            cfg['server_url'] + '/ping.php',
            json={'token_b': cfg['token_b']},
            timeout=10,
        )
        data = r.json()
    except Exception as e:
        log.error(f'send_pong failed: {e}')
        return

    if 'token_b_new' in data:
        cfg['token_b'] = data['token_b_new']
        save_config(cfg)

    if data.get('status') == 'pong':
        log.info('Pong sent — bridge confirmed online')
    else:
        log.warning(f'Unexpected ping response: {data}')


def put_answer(cfg, job_id, answer):
    """Schreibt Antwort zurück, rotiert Token-B"""
    try:
        r = requests.post(
            cfg['server_url'] + '/put_answer.php',
            json={
                'token_b': cfg['token_b'],
                'job_id':  job_id,
                'answer':  answer,
            },
            timeout=10,
        )
        data = r.json()
    except Exception as e:
        log.error(f'put_answer failed: {e}')
        return False

    if 'token_b_new' in data:
        cfg['token_b'] = data['token_b_new']
        save_config(cfg)

    if data.get('status') == 'ok':
        log.info(f'Answer submitted for job #{job_id}')
        return True

    log.error(f'put_answer error: {data}')
    return False


# ─────────────────────────────────────────────
# Agent call (OpenAI-compatible endpoint)
# ─────────────────────────────────────────────
def call_agent(cfg, prompt):
    """Sendet Prompt an den KI-Agenten, gibt Antwort zurück"""
    agent_url   = cfg['agent_endpoint']
    agent_token = cfg.get('agent_token', '')
    agent_model = cfg.get('agent_model', 'chatcompletion')

    headers = {'Content-Type': 'application/json'}
    if agent_token:
        headers['Authorization'] = f'Bearer {agent_token}'
    payload = {
        'model':    agent_model,
        'messages': [{'role': 'user', 'content': prompt}],
        'stream':   False,
    }

    try:
        log.info(f'Calling agent: {agent_url}')
        r = requests.post(
            agent_url,
            headers=headers,
            json=payload,
            timeout=cfg.get('agent_timeout', 120),
        )
        r.raise_for_status()
        data    = r.json()
        answer  = data['choices'][0]['message']['content']
        log.info(f'Agent answered ({len(answer)} chars)')
        return answer
    except requests.exceptions.Timeout:
        log.error('Agent call timed out')
        return None
    except Exception as e:
        log.error(f'Agent call failed: {e}')
        return None


# ─────────────────────────────────────────────
# Job processing
# ─────────────────────────────────────────────
def process_wakeup(cfg):
    """Wird aufgerufen wenn MQTT Wakeup-Message eintrifft"""
    log.info('Wakeup received — fetching job...')

    job = get_job(cfg)
    if not job:
        return

    prompt = job.get('prompt', '')
    job_id = job.get('job_id')

    if not prompt or not job_id:
        log.warning('Job has no prompt or id, skipping.')
        return

    log.info(f'Processing job #{job_id}: "{prompt[:60]}..."')

    answer = call_agent(cfg, prompt)

    if answer:
        put_answer(cfg, job_id, answer)
    else:
        # Fehlermeldung zurückschreiben damit User Feedback bekommt
        lang = cfg.get('lang', 'EN')
        err_msg = (
            'Es ist leider ein Fehler aufgetreten. Bitte versuche es erneut.'
            if lang == 'DE' else
            'An error occurred. Please try again.'
        )
        put_answer(cfg, job_id, err_msg)


# ─────────────────────────────────────────────
# MQTT
# ─────────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    cfg = userdata
    if rc == 0:
        topic = f"aat/{cfg['token_a']}"
        client.subscribe(topic, qos=1)
        log.info(f'Connected to MQTT broker, subscribed to: {topic}')
    else:
        log.error(f'MQTT connect failed, rc={rc}')


def on_message(client, userdata, msg):
    cfg = userdata
    try:
        payload = json.loads(msg.payload.decode())
    except Exception:
        payload = {}

    action = payload.get('action', '')
    log.info(f'MQTT message received: action={action}')

    if action == 'wakeup':
        process_wakeup(cfg)
    elif action == 'ping':
        send_pong(cfg)
    else:
        log.warning(f'Unknown MQTT action: {action}')


def on_disconnect(client, userdata, rc):
    if rc != 0:
        log.warning(f'Unexpected MQTT disconnect (rc={rc}), reconnecting...')


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    cfg = load_config()

    required = ['token_a', 'token_b', 'server_url', 'agent_endpoint',
                'mqtt_host', 'mqtt_port', 'mqtt_user', 'mqtt_password']
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        log.error(f'Missing config keys: {missing}')
        sys.exit(1)

    client = mqtt.Client(
        client_id=f"aat-bridge-{cfg['token_a'][:8]}",
        userdata=cfg,
    )
    client.username_pw_set(cfg['mqtt_user'], cfg['mqtt_password'])
    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    # TLS falls konfiguriert
    if cfg.get('mqtt_tls', False):
        client.tls_set()

    def shutdown(sig, frame):
        log.info('Shutting down bridge...')
        client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log.info(f"AAT Bridge starting — server: {cfg['server_url']}")
    log.info(f"MQTT: {cfg['mqtt_host']}:{cfg['mqtt_port']}, topic: aat/{cfg['token_a']}")

    while True:
        try:
            client.connect(cfg['mqtt_host'], int(cfg['mqtt_port']), keepalive=60)
            client.loop_forever()
        except Exception as e:
            log.error(f'MQTT connection error: {e}, retrying in 30s...')
            time.sleep(30)


if __name__ == '__main__':
    main()