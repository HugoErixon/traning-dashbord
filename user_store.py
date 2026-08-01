"""Användarlagring: minnesbaserad för tester, databasbackad i drift.

Båda varianterna exponerar samma gränssnitt och returnerar användare i samma
form som security.parse_users, plus en is_admin-flagga:

    {username: {'id': int, 'password': hash, 'password_hashed': True, 'is_admin': bool}}

DB-varianten seedas från .env-användarna vid första start (tom tabell) med
bevarade user-id:n, eftersom befintliga rader i activities/journal m.fl. redan
pekar på dem. Därefter är databasen källan; .env USERS läses aldrig igen.
"""
import hmac
import re
import secrets
import time

from werkzeug.security import generate_password_hash

from security import USERNAME_RE, is_password_hash

MIN_PASSWORD_LENGTH = 8
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
VERIFICATION_TOKEN_TTL = 24 * 3600


class UserStoreError(ValueError):
    """Valideringsfel som är säkra att visa för klienten."""


def _validate_new_user(username, password):
    if not isinstance(username, str) or not USERNAME_RE.fullmatch(username):
        raise UserStoreError('Ogiltigt användarnamn (tillåtet: bokstäver, siffror, _ . -, max 64 tecken).')
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise UserStoreError(f'Lösenordet måste vara minst {MIN_PASSWORD_LENGTH} tecken.')
    if len(password) > 1024:
        raise UserStoreError('Lösenordet är för långt.')


def _validate_email(email):
    if not isinstance(email, str) or len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise UserStoreError('Ogiltig e-postadress.')
    return email.strip().lower()


def _ensure_hashed(password):
    return password if is_password_hash(password) else generate_password_hash(password)


class DuplicateUserError(UserStoreError):
    pass


class MemoryUserStore:
    """Backar upp testkörningar (APP_TESTING) där databasen aldrig rörs."""

    def __init__(self, env_users):
        self._users = {}
        for username, rec in env_users.items():
            self._users[username] = {
                'id': rec['id'],
                'password': rec['password'],
                'password_hashed': rec['password_hashed'],
                'is_admin': len(self._users) == 0,
                'widget_token_hash': None,
                'email': None,
                'email_verified': False,
            }

    def all(self):
        _internal = ('widget_token_hash', '_verification_token', '_verification_expires')
        return {
            username: {key: value for key, value in rec.items() if key not in _internal}
            for username, rec in self._users.items()
        }

    def create(self, username, password, is_admin=False):
        _validate_new_user(username, password)
        if username in self._users:
            raise DuplicateUserError('Användarnamnet är upptaget.')
        new_id = max((rec['id'] for rec in self._users.values()), default=0) + 1
        self._users[username] = {
            'id': new_id,
            'password': _ensure_hashed(password),
            'password_hashed': True,
            'is_admin': bool(is_admin),
            'widget_token_hash': None,
            'email': None,
            'email_verified': False,
        }
        return new_id

    def create_pending(self, username, email, password):
        _validate_new_user(username, password)
        email = _validate_email(email)
        if username in self._users:
            raise DuplicateUserError('Användarnamnet är upptaget.')
        if any((rec.get('email') or '').lower() == email for rec in self._users.values()):
            raise DuplicateUserError('E-postadressen används redan.')
        new_id = max((rec['id'] for rec in self._users.values()), default=0) + 1
        token = secrets.token_urlsafe(32)
        self._users[username] = {
            'id': new_id,
            'password': _ensure_hashed(password),
            'password_hashed': True,
            'is_admin': False,
            'widget_token_hash': None,
            'email': email,
            'email_verified': False,
            '_verification_token': token,
            '_verification_expires': time.time() + VERIFICATION_TOKEN_TTL,
        }
        return new_id, token

    def verify_email_token(self, token):
        now = time.time()
        for username, rec in self._users.items():
            if rec.get('_verification_token') == token and not rec.get('email_verified'):
                if rec.get('_verification_expires', 0) < now:
                    return None
                rec['email_verified'] = True
                rec.pop('_verification_token', None)
                rec.pop('_verification_expires', None)
                return username
        return None

    def delete(self, user_id):
        for username, rec in list(self._users.items()):
            if rec['id'] == user_id:
                del self._users[username]
                return True
        return False

    def set_widget_token_hash(self, user_id, token_hash):
        for rec in self._users.values():
            if rec['id'] == user_id:
                rec['widget_token_hash'] = token_hash
                return True
        return False

    def user_for_widget_token_hash(self, token_hash):
        for username, rec in self._users.items():
            stored = rec.get('widget_token_hash') or ''
            if stored and hmac.compare_digest(stored, token_hash):
                return {
                    'username': username,
                    **{key: value for key, value in rec.items() if key != 'widget_token_hash'},
                }
        return None


class DbUserStore:
    """Användare i Postgres. db_factory är garmin_server.db (returnerar en connection)."""

    def __init__(self, db_factory):
        self._db = db_factory

    def ensure_schema(self):
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at REAL,
                    widget_token_hash TEXT,
                    widget_token_created_at REAL)''')
                cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS widget_token_hash TEXT')
                cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS widget_token_created_at REAL')
                cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS users_widget_token_hash_idx
                    ON users (widget_token_hash) WHERE widget_token_hash IS NOT NULL''')
                cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT')
                cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT false')
                cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_token TEXT')
                cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_token_expires REAL')
                cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS users_email_key
                    ON users (lower(email)) WHERE email IS NOT NULL''')
            conn.commit()

    def seed_from_env(self, env_users):
        """Engångsmigrering: fyll tom tabell från .env-användarna med bevarade id:n."""
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM users')
                if cur.fetchone()[0] > 0:
                    return False
                first = True
                for username, rec in env_users.items():
                    cur.execute(
                        'INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES (%s,%s,%s,%s,%s)',
                        (rec['id'], username, _ensure_hashed(rec['password']), first, time.time()))
                    first = False
                cur.execute("SELECT setval(pg_get_serial_sequence('users','id'), (SELECT MAX(id) FROM users))")
            conn.commit()
        return True

    def all(self):
        users = {}
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id, username, password_hash, is_admin, email, email_verified FROM users ORDER BY id')
                for user_id, username, password_hash, is_admin, email, email_verified in cur.fetchall():
                    users[username] = {
                        'id': user_id,
                        'password': password_hash,
                        'password_hashed': True,
                        'is_admin': bool(is_admin),
                        'email': email,
                        'email_verified': bool(email_verified),
                    }
        return users

    def create(self, username, password, is_admin=False):
        _validate_new_user(username, password)
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1 FROM users WHERE username=%s', (username,))
                if cur.fetchone():
                    raise DuplicateUserError('Användarnamnet är upptaget.')
                cur.execute(
                    'INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (%s,%s,%s,%s) RETURNING id',
                    (username, _ensure_hashed(password), bool(is_admin), time.time()))
                new_id = cur.fetchone()[0]
            conn.commit()
        return new_id

    def create_pending(self, username, email, password):
        """Självregistrering: skapar ett overifierat konto och returnerar (user_id, token)."""
        _validate_new_user(username, password)
        email = _validate_email(email)
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1 FROM users WHERE username=%s', (username,))
                if cur.fetchone():
                    raise DuplicateUserError('Användarnamnet är upptaget.')
                cur.execute('SELECT 1 FROM users WHERE lower(email)=lower(%s)', (email,))
                if cur.fetchone():
                    raise DuplicateUserError('E-postadressen används redan.')
                cur.execute(
                    '''INSERT INTO users
                        (username, password_hash, is_admin, created_at, email, email_verified,
                         verification_token, verification_token_expires)
                       VALUES (%s,%s,false,%s,%s,false,%s,%s) RETURNING id''',
                    (username, _ensure_hashed(password), now, email, token, now + VERIFICATION_TOKEN_TTL))
                new_id = cur.fetchone()[0]
            conn.commit()
        return new_id, token

    def verify_email_token(self, token):
        """Aktiverar kontot om token är giltig och inte har gått ut. Returnerar username eller None."""
        if not isinstance(token, str) or not token:
            return None
        now = time.time()
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    '''SELECT id, username, verification_token_expires FROM users
                       WHERE verification_token=%s AND email_verified=false''', (token,))
                row = cur.fetchone()
                if not row:
                    return None
                user_id, username, expires = row
                if expires is not None and expires < now:
                    return None
                cur.execute(
                    '''UPDATE users SET email_verified=true, verification_token=NULL,
                       verification_token_expires=NULL WHERE id=%s''', (user_id,))
            conn.commit()
        return username

    def delete(self, user_id):
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM users WHERE id=%s', (user_id,))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    def set_widget_token_hash(self, user_id, token_hash):
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''UPDATE users
                    SET widget_token_hash=%s, widget_token_created_at=%s
                    WHERE id=%s''', (token_hash, time.time(), user_id))
                updated = cur.rowcount > 0
            conn.commit()
        return updated

    def user_for_widget_token_hash(self, token_hash):
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT id, username, password_hash, is_admin
                    FROM users WHERE widget_token_hash=%s''', (token_hash,))
                row = cur.fetchone()
        if not row:
            return None
        user_id, username, password_hash, is_admin = row
        return {
            'id': user_id,
            'username': username,
            'password': password_hash,
            'password_hashed': True,
            'is_admin': bool(is_admin),
        }
