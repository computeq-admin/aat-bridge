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
import subprocess
import sys
import time
import uuid
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
    except requests.exceptions.JSONDecodeError:
        log.error(f'send_pong: server returned no JSON (HTTP {r.status_code}): {r.text[:200]}')
        return
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
# Session management
# ─────────────────────────────────────────────
_current_session_id = None


def get_or_reset_session(reset):
    """Gibt die aktuelle Session-ID zurück; bei reset=True wird eine neue erzeugt."""
    global _current_session_id
    if reset or _current_session_id is None:
        _current_session_id = str(uuid.uuid4())
        log.info(f'New session started: {_current_session_id}')
    else:
        log.info(f'Continuing session: {_current_session_id}')
    return _current_session_id


# ─────────────────────────────────────────────
# Agent call (CLI)
# ─────────────────────────────────────────────
def call_agent_cli(cfg, prompt, system_prompt='', session_id=None):
    """Ruft den KI-Agenten als lokalen CLI-Prozess auf, gibt stdout zurück"""
    cmd = [cfg['cli_command']]

    # Session-ID übergeben (z.B. --resume <uuid>) wenn konfiguriert und vorhanden
    session_param = cfg.get('cli_session_id_param', '')
    if session_param and session_id:
        cmd += [session_param, session_id]

    sp_param = cfg.get('cli_system_prompt_param', '')
    if sp_param and system_prompt:
        cmd += [sp_param, system_prompt]

    for arg in cfg.get('cli_extra_params', []):
        cmd.append(str(arg))

    prompt_param = cfg.get('cli_prompt_param', '')
    if prompt_param:
        cmd += [prompt_param, prompt]
    else:
        cmd.append(prompt)

    env = os.environ.copy()
    env.update(cfg.get('cli_env', {}))

    cwd     = cfg.get('cli_working_dir') or None
    timeout = cfg.get('cli_timeout', 600)

    log.info(f'Calling CLI agent: {cmd[0]} (timeout={timeout}s, cwd={cwd})')
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd,
            timeout=timeout,
        )
        if result.returncode != 0:
            log.error(f'CLI exited {result.returncode}: {result.stderr[:300]}')
            return None
        answer = result.stdout.strip()
        if not answer:
            log.error('CLI returned empty output')
            return None
        log.info(f'CLI answered ({len(answer)} chars)')
        return answer
    except subprocess.TimeoutExpired:
        log.error(f'CLI timed out after {timeout}s')
        return None
    except Exception as e:
        log.error(f'CLI call failed: {e}')
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

    prompt        = job.get('prompt', '')
    job_id        = job.get('job_id')
    system_prompt = job.get('system_prompt', '')
    reset         = job.get('reset_history', True)

    if not prompt or not job_id:
        log.warning('Job has no prompt or id, skipping.')
        return

    session_id = get_or_reset_session(reset)
    log.info(f'Processing job #{job_id}: "{prompt[:60]}..."')

    answer = call_agent_cli(cfg, prompt, system_prompt, session_id)

    if answer:
        put_answer(cfg, job_id, answer)
    else:
        lang = cfg.get('lang', 'DE')
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
    raw = msg.payload.decode().strip()
    try:
        payload = json.loads(raw)
        action = payload.get('action', '')
    except Exception:
        # Plain-string payload (z.B. "ping" oder "wakeup")
        payload = {}
        action = raw

    log.info(f'MQTT message received: raw={raw!r} action={action}')

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

    required = ['token_a', 'token_b', 'server_url', 'cli_command',
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