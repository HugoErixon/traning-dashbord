"""Användarlagring: minnesbaserad för tester, databasbackad i drift.

Båda varianterna exponerar samma gränssnitt och returnerar användare i samma
form som security.parse_users, plus en is_admin-flagga:

    {username: {'id': int, 'password': hash, 'password_hashed': True, 'is_admin': bool}}

DB-varianten seedas från .env-användarna vid första start (tom tabell) med
bevarade user-id:n, eftersom befintliga rader i activities/journal m.fl. redan
pekar på dem. Därefter är databasen källan; .env USERS läses aldrig igen.
"""
import time

from werkzeug.security import generate_password_hash

from security import USERNAME_RE, is_password_hash

MIN_PASSWORD_LENGTH = 8


class UserStoreError(ValueError):
    """Valideringsfel som är säkra att visa för klienten."""


def _validate_new_user(username, password):
    if not isinstance(username, str) or not USERNAME_RE.fullmatch(username):
        raise UserStoreError('Ogiltigt användarnamn (tillåtet: bokstäver, siffror, _ . -, max 64 tecken).')
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise UserStoreError(f'Lösenordet måste vara minst {MIN_PASSWORD_LENGTH} tecken.')
    if len(password) > 1024:
        raise UserStoreError('Lösenordet är för långt.')


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
            }

    def all(self):
        return {u: dict(rec) for u, rec in self._users.items()}

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
        }
        return new_id

    def delete(self, user_id):
        for username, rec in list(self._users.items()):
            if rec['id'] == user_id:
                del self._users[username]
                return True
        return False


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
                    created_at REAL)''')
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
                cur.execute('SELECT id, username, password_hash, is_admin FROM users ORDER BY id')
                for user_id, username, password_hash, is_admin in cur.fetchall():
                    users[username] = {
                        'id': user_id,
                        'password': password_hash,
                        'password_hashed': True,
                        'is_admin': bool(is_admin),
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

    def delete(self, user_id):
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM users WHERE id=%s', (user_id,))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted
