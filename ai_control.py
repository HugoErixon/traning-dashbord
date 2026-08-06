"""Storage for the owner-only Trainyze development agent.

The web process owns authentication and the job queue.  A separate, narrowly
scoped worker on g3 claims jobs and runs Codex or Claude inside the repository. Tests use
the in-memory implementation; production uses the same class with PostgreSQL.
"""

from __future__ import annotations

import json
import threading
import time
import uuid


class AiControlStore:
    def __init__(self, db_factory=None):
        self._db = db_factory
        self._lock = threading.Lock()
        self._credentials = {}
        self._jobs = {}
        self._events = {}

    @property
    def persistent(self):
        return self._db is not None

    def ensure_schema(self):
        if not self._db:
            return
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''CREATE TABLE IF NOT EXISTS ai_passkey_credentials (
                    credential_id BYTEA PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    public_key BYTEA NOT NULL,
                    sign_count BIGINT NOT NULL DEFAULT 0,
                    transports JSONB NOT NULL DEFAULT '[]'::jsonb,
                    label TEXT NOT NULL DEFAULT 'Face ID',
                    created_at REAL NOT NULL,
                    last_used_at REAL)''')
                cur.execute('''CREATE INDEX IF NOT EXISTS ai_passkeys_user_idx
                    ON ai_passkey_credentials (user_id)''')
                cur.execute('''CREATE TABLE IF NOT EXISTS ai_jobs (
                    id UUID PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    prompt TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'codex',
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    claimed_at REAL,
                    finished_at REAL,
                    agent_id TEXT,
                    result TEXT,
                    error TEXT)''')
                cur.execute("""ALTER TABLE ai_jobs ADD COLUMN IF NOT EXISTS provider TEXT
                               NOT NULL DEFAULT 'codex'""")
                cur.execute('''CREATE INDEX IF NOT EXISTS ai_jobs_recent_idx
                    ON ai_jobs (user_id, created_at DESC)''')
                cur.execute('''CREATE INDEX IF NOT EXISTS ai_jobs_queue_idx
                    ON ai_jobs (status, created_at)''')
                cur.execute('''CREATE TABLE IF NOT EXISTS ai_job_events (
                    seq BIGSERIAL PRIMARY KEY,
                    job_id UUID NOT NULL REFERENCES ai_jobs(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at REAL NOT NULL)''')
                cur.execute('''CREATE INDEX IF NOT EXISTS ai_job_events_job_idx
                    ON ai_job_events (job_id, seq)''')
            conn.commit()

    def credentials_for_user(self, user_id):
        if not self._db:
            with self._lock:
                return [dict(value) for value in self._credentials.values()
                        if value['user_id'] == user_id]
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT credential_id, user_id, public_key, sign_count,
                                      transports, label, created_at, last_used_at
                               FROM ai_passkey_credentials WHERE user_id=%s
                               ORDER BY created_at''', (user_id,))
                rows = cur.fetchall()
        return [self._credential_row(row) for row in rows]

    def credential(self, credential_id):
        credential_id = bytes(credential_id)
        if not self._db:
            with self._lock:
                value = self._credentials.get(credential_id)
                return dict(value) if value else None
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT credential_id, user_id, public_key, sign_count,
                                      transports, label, created_at, last_used_at
                               FROM ai_passkey_credentials WHERE credential_id=%s''',
                            (psycopg2_binary(credential_id),))
                row = cur.fetchone()
        return self._credential_row(row) if row else None

    @staticmethod
    def _credential_row(row):
        credential_id, user_id, public_key, sign_count, transports, label, created_at, last_used_at = row
        if isinstance(transports, str):
            transports = json.loads(transports)
        return {
            'credential_id': bytes(credential_id),
            'user_id': user_id,
            'public_key': bytes(public_key),
            'sign_count': int(sign_count or 0),
            'transports': list(transports or []),
            'label': label,
            'created_at': created_at,
            'last_used_at': last_used_at,
        }

    def add_credential(self, user_id, credential_id, public_key, sign_count,
                       transports=None, label='Face ID'):
        credential_id = bytes(credential_id)
        public_key = bytes(public_key)
        transports = [str(value) for value in (transports or [])][:8]
        now = time.time()
        if not self._db:
            with self._lock:
                if credential_id in self._credentials:
                    raise ValueError('Passkeyn är redan registrerad.')
                self._credentials[credential_id] = {
                    'credential_id': credential_id, 'user_id': user_id,
                    'public_key': public_key, 'sign_count': int(sign_count or 0),
                    'transports': transports, 'label': str(label)[:80],
                    'created_at': now, 'last_used_at': None,
                }
            return
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''INSERT INTO ai_passkey_credentials
                    (credential_id,user_id,public_key,sign_count,transports,label,created_at)
                    VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s)''',
                    (psycopg2_binary(credential_id), user_id, psycopg2_binary(public_key),
                     int(sign_count or 0), json.dumps(transports), str(label)[:80], now))
            conn.commit()

    def update_credential_counter(self, credential_id, sign_count):
        credential_id = bytes(credential_id)
        now = time.time()
        if not self._db:
            with self._lock:
                if credential_id in self._credentials:
                    self._credentials[credential_id]['sign_count'] = int(sign_count or 0)
                    self._credentials[credential_id]['last_used_at'] = now
            return
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''UPDATE ai_passkey_credentials
                    SET sign_count=%s,last_used_at=%s WHERE credential_id=%s''',
                    (int(sign_count or 0), now, psycopg2_binary(credential_id)))
            conn.commit()

    def create_job(self, user_id, prompt, provider='codex'):
        provider = str(provider or '').lower()
        if provider not in ('codex', 'claude'):
            raise ValueError('Okänd AI-leverantör.')
        job_id = str(uuid.uuid4())
        now = time.time()
        job = {'id': job_id, 'user_id': user_id, 'prompt': prompt, 'provider': provider,
               'status': 'pending',
               'created_at': now, 'claimed_at': None, 'finished_at': None,
               'agent_id': None, 'result': None, 'error': None}
        if not self._db:
            with self._lock:
                self._jobs[job_id] = job
                self._events[job_id] = []
            return dict(job)
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''INSERT INTO ai_jobs (id,user_id,prompt,provider,status,created_at)
                    VALUES (%s,%s,%s,%s,'pending',%s)''',
                    (job_id, user_id, prompt, provider, now))
            conn.commit()
        return job

    def list_jobs(self, user_id, limit=20):
        limit = min(max(int(limit), 1), 50)
        if not self._db:
            with self._lock:
                jobs = [dict(job) for job in self._jobs.values() if job['user_id'] == user_id]
            return sorted(jobs, key=lambda job: job['created_at'], reverse=True)[:limit]
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT id,user_id,prompt,provider,status,created_at,claimed_at,finished_at,
                                      agent_id,result,error FROM ai_jobs
                               WHERE user_id=%s ORDER BY created_at DESC LIMIT %s''',
                            (user_id, limit))
                rows = cur.fetchall()
        return [self._job_row(row) for row in rows]

    def get_job(self, job_id, user_id=None):
        if not self._db:
            with self._lock:
                job = self._jobs.get(str(job_id))
                if not job or (user_id is not None and job['user_id'] != user_id):
                    return None
                result = dict(job)
                result['events'] = [dict(event) for event in self._events.get(str(job_id), [])]
                return result
        params = [str(job_id)]
        where = 'id=%s'
        if user_id is not None:
            where += ' AND user_id=%s'
            params.append(user_id)
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute(f'''SELECT id,user_id,prompt,provider,status,created_at,claimed_at,finished_at,
                                       agent_id,result,error FROM ai_jobs WHERE {where}''', params)
                row = cur.fetchone()
                if not row:
                    return None
                job = self._job_row(row)
                cur.execute('''SELECT seq,kind,message,created_at FROM ai_job_events
                               WHERE job_id=%s ORDER BY seq DESC LIMIT 250''', (str(job_id),))
                events = cur.fetchall()
        job['events'] = [
            {'seq': seq, 'kind': kind, 'message': message, 'created_at': created_at}
            for seq, kind, message, created_at in reversed(events)
        ]
        return job

    @staticmethod
    def _job_row(row):
        keys = ('id', 'user_id', 'prompt', 'provider', 'status', 'created_at', 'claimed_at',
                'finished_at', 'agent_id', 'result', 'error')
        value = dict(zip(keys, row))
        value['id'] = str(value['id'])
        return value

    def claim_next(self, agent_id):
        now = time.time()
        agent_id = str(agent_id or 'g3')[:80]
        if not self._db:
            with self._lock:
                pending = sorted((job for job in self._jobs.values() if job['status'] == 'pending'),
                                 key=lambda job: job['created_at'])
                if not pending:
                    return None
                pending[0].update(status='running', claimed_at=now, agent_id=agent_id)
                return dict(pending[0])
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT id FROM ai_jobs WHERE status='pending'
                               ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1''')
                row = cur.fetchone()
                if not row:
                    return None
                cur.execute('''UPDATE ai_jobs SET status='running',claimed_at=%s,agent_id=%s
                               WHERE id=%s RETURNING id,user_id,prompt,provider,status,created_at,
                               claimed_at,finished_at,agent_id,result,error''',
                            (now, agent_id, row[0]))
                job = self._job_row(cur.fetchone())
            conn.commit()
        return job

    def append_event(self, job_id, kind, message):
        job_id = str(job_id)
        kind = str(kind or 'log')[:32]
        message = str(message or '')[:12000]
        now = time.time()
        if not self._db:
            with self._lock:
                if job_id not in self._jobs:
                    return False
                events = self._events.setdefault(job_id, [])
                events.append({'seq': len(events) + 1, 'kind': kind,
                               'message': message, 'created_at': now})
                del events[:-250]
            return True
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''INSERT INTO ai_job_events (job_id,kind,message,created_at)
                               SELECT id,%s,%s,%s FROM ai_jobs WHERE id=%s''',
                            (kind, message, now, job_id))
                inserted = cur.rowcount > 0
            conn.commit()
        return inserted

    def finish_job(self, job_id, status, result=None, error=None):
        if status not in ('completed', 'failed', 'cancelled'):
            raise ValueError('Ogiltig jobbstatus.')
        job_id = str(job_id)
        now = time.time()
        result = str(result)[:50000] if result is not None else None
        error = str(error)[:12000] if error is not None else None
        if not self._db:
            with self._lock:
                job = self._jobs.get(job_id)
                if not job:
                    return False
                job.update(status=status, result=result, error=error, finished_at=now)
            return True
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''UPDATE ai_jobs SET status=%s,result=%s,error=%s,finished_at=%s
                               WHERE id=%s AND status IN ('pending','running')''',
                            (status, result, error, now, job_id))
                updated = cur.rowcount > 0
            conn.commit()
        return updated


def psycopg2_binary(value):
    # Import lazily so the in-memory test store has no database coupling.
    from psycopg2 import Binary
    return Binary(value)
