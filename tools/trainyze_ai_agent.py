#!/usr/bin/env python3
"""Poll Trainyze for owner-approved development jobs and run Codex or Claude on g3.

This worker deliberately cannot deploy, restart services, or elevate privileges.
Point AI_AGENT_WORKSPACE at a dedicated checkout, not the live production tree.
"""

import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import time

import requests


BASE_URL = os.environ.get('AI_AGENT_BASE_URL', 'http://127.0.0.1:3000').rstrip('/')
TOKEN = os.environ.get('AI_AGENT_TOKEN', '')
WORKSPACE = Path(os.environ.get('AI_AGENT_WORKSPACE', '')).expanduser().resolve()
AGENT_ID = os.environ.get('AI_AGENT_ID', 'g3-workspace')[:80]
POLL_SECONDS = min(max(float(os.environ.get('AI_AGENT_POLL_SECONDS', '3')), 1), 30)
JOB_TIMEOUT = min(max(int(os.environ.get('AI_AGENT_JOB_TIMEOUT', '1800')), 60), 7200)

STOPPING = False
ACTIVE_PROCESS = None


def stop(_signum, _frame):
    global STOPPING
    STOPPING = True
    if ACTIVE_PROCESS and ACTIVE_PROCESS.poll() is None:
        ACTIVE_PROCESS.terminate()


def api(session, method, path, **kwargs):
    response = session.request(method, f'{BASE_URL}{path}', timeout=15, **kwargs)
    response.raise_for_status()
    return response.json()


def emit(session, job_id, kind, message):
    try:
        api(session, 'POST', f'/api/ai/agent/jobs/{job_id}/events',
            json={'kind': kind, 'message': str(message)[:12000]})
    except requests.RequestException:
        # A temporary logging failure must not terminate the Codex run.
        pass


def final_agent_message(provider, event):
    if provider == 'claude' and event.get('type') == 'result':
        return str(event.get('result') or '')
    item = event.get('item') or {}
    if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
        return str(item.get('text') or '')
    return None


def provider_command(provider, instruction):
    if provider == 'codex':
        return ([
            'codex', 'exec', '--ephemeral', '--json', '--color', 'never',
            '--sandbox', 'workspace-write',
            '-C', str(WORKSPACE), '-',
        ], instruction)
    if provider == 'claude':
        # The systemd sandbox and dedicated checkout are the security boundary.
        # This flag is required for an unattended worker to edit and test files.
        return ([
            'claude', '-p', instruction, '--output-format', 'stream-json',
            '--verbose', '--max-turns', '30', '--dangerously-skip-permissions',
        ], None)
    raise ValueError(f'Okänd AI-leverantör: {provider}')


def run_job(session, job):
    global ACTIVE_PROCESS
    job_id = job['id']
    provider = str(job.get('provider') or 'codex').lower()
    provider_name = {'codex': 'Codex', 'claude': 'Claude'}.get(provider)
    if not provider_name:
        raise ValueError(f'Okänd AI-leverantör: {provider}')
    instruction = f'''You are the Trainyze development agent running in a dedicated checkout on g3.

User request:
{job['prompt']}

Work only inside this repository. Read AGENTS.md first. Diagnose carefully, make the smallest
appropriate implementation, and run relevant tests. Preserve unrelated existing changes.
Do not use sudo, do not restart services, do not deploy, do not push, and do not expose secrets.
Finish with a concise Swedish summary of changed files, tests, and anything the owner must review.
'''
    command, stdin_text = provider_command(provider, instruction)
    # Never pass the queue bearer token (or any dashboard secret accidentally
    # present in the service environment) into Codex or its shell commands.
    child_environment = {
        key: value for key, value in os.environ.items()
        if key in {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TERM', 'TMPDIR',
                   'CODEX_HOME', 'CLAUDE_CONFIG_DIR'}
    }
    emit(session, job_id, 'status',
         f'G3 startar {provider_name} i den isolerade projektmappen.')
    started = time.monotonic()
    last_message = ''
    try:
        ACTIVE_PROCESS = subprocess.Popen(
            command, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, cwd=WORKSPACE,
            stderr=subprocess.STDOUT, text=True, bufsize=1, env=child_environment,
        )
        if stdin_text is not None:
            ACTIVE_PROCESS.stdin.write(stdin_text)
            ACTIVE_PROCESS.stdin.close()
        selector = selectors.DefaultSelector()
        selector.register(ACTIVE_PROCESS.stdout, selectors.EVENT_READ)
        while ACTIVE_PROCESS.poll() is None:
            if STOPPING:
                raise RuntimeError('Agenttjänsten stoppades.')
            if time.monotonic() - started > JOB_TIMEOUT:
                ACTIVE_PROCESS.terminate()
                raise TimeoutError(f'Uppdraget överskred {JOB_TIMEOUT} sekunder.')
            for key, _ in selector.select(timeout=1):
                line = key.fileobj.readline()
                if not line:
                    continue
                line = line.rstrip()
                try:
                    event = json.loads(line)
                    message = final_agent_message(provider, event)
                    if message:
                        last_message = message
                    event_type = event.get('type', 'event')
                    if event_type in ('item.completed', 'result', 'turn.failed', 'error'):
                        emit(session, job_id, event_type, line)
                except json.JSONDecodeError:
                    emit(session, job_id, 'output', line)
        return_code = ACTIVE_PROCESS.wait()
        if return_code != 0:
            raise RuntimeError(f'{provider_name} avslutades med kod {return_code}.')
        api(session, 'POST', f'/api/ai/agent/jobs/{job_id}/finish',
            json={'status': 'completed',
                  'result': last_message or f'{provider_name} slutförde uppdraget.'})
    except Exception as exc:
        emit(session, job_id, 'error', str(exc))
        try:
            api(session, 'POST', f'/api/ai/agent/jobs/{job_id}/finish',
                json={'status': 'failed', 'error': str(exc)})
        except requests.RequestException:
            pass
    finally:
        ACTIVE_PROCESS = None


def validate_configuration():
    if len(TOKEN) < 32:
        raise SystemExit('AI_AGENT_TOKEN måste vara minst 32 tecken.')
    if not str(WORKSPACE) or not WORKSPACE.is_dir():
        raise SystemExit('AI_AGENT_WORKSPACE måste peka på en befintlig katalog.')
    if not (WORKSPACE / '.git').exists():
        raise SystemExit('AI_AGENT_WORKSPACE måste vara ett separat Git-checkout.')
    if WORKSPACE == Path(__file__).resolve().parents[1]:
        raise SystemExit('Vägrar använda webbserverns checkout. Ange ett separat agent-checkout.')
    missing = [binary for binary in ('codex', 'claude') if not shutil.which(binary)]
    if missing:
        raise SystemExit(f'CLI saknas i PATH: {", ".join(missing)}')


def main():
    validate_configuration()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    session = requests.Session()
    session.headers.update({'Authorization': f'Bearer {TOKEN}', 'User-Agent': 'trainyze-g3-agent/1'})
    while not STOPPING:
        try:
            payload = api(session, 'POST', '/api/ai/agent/jobs/next', json={'agentId': AGENT_ID})
            job = payload.get('job')
            if job:
                run_job(session, job)
            else:
                time.sleep(POLL_SECONDS)
        except requests.RequestException as exc:
            print(f'Agent API unavailable: {exc}', file=sys.stderr, flush=True)
            time.sleep(min(POLL_SECONDS * 2, 30))


if __name__ == '__main__':
    main()
