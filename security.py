import hmac
import re
import threading
import time
from collections import defaultdict, deque

from werkzeug.security import check_password_hash, generate_password_hash


HASH_PREFIXES = ('scrypt:', 'pbkdf2:')
USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
_DUMMY_PASSWORD_HASH = generate_password_hash('invalid-login-placeholder')


def is_password_hash(value):
    return isinstance(value, str) and value.startswith(HASH_PREFIXES)


def verify_password(stored_value, candidate):
    """Verify hashed credentials while supporting one-time legacy migration."""
    if not isinstance(stored_value, str) or not isinstance(candidate, str):
        return False
    if is_password_hash(stored_value):
        try:
            return check_password_hash(stored_value, candidate)
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(stored_value.encode('utf-8'), candidate.encode('utf-8'))


def verify_user(users, username, password):
    """Use a real hash check for unknown users to reduce username timing leaks."""
    user = users.get(username)
    stored_value = user['password'] if user else _DUMMY_PASSWORD_HASH
    valid = verify_password(stored_value, password)
    return user if user and valid else None


def parse_users(raw_users, legacy_password=None, default_username='hugo'):
    users = {}
    for entry in str(raw_users or '').split(','):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(':', 1)
        if len(parts) != 2:
            raise ValueError('USERS entries must use username:password_hash')
        username, password = (part.strip() for part in parts)
        if not USERNAME_RE.fullmatch(username):
            raise ValueError(f'Invalid username in USERS: {username!r}')
        if not password:
            raise ValueError(f'Missing credential for user {username!r}')
        if username in users:
            raise ValueError(f'Duplicate user in USERS: {username!r}')
        users[username] = {
            'id': len(users) + 1,
            'password': password,
            'password_hashed': is_password_hash(password),
        }

    if not users and legacy_password:
        users[default_username] = {
            'id': 1,
            'password': str(legacy_password),
            'password_hashed': is_password_hash(str(legacy_password)),
        }
    if not users:
        raise ValueError('USERS must contain at least one configured account')
    return users


class LoginRateLimiter:
    def __init__(self, max_attempts=8, window_seconds=15 * 60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, attempts, now):
        cutoff = now - self.window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()

    def check(self, key, now=None):
        now = time.time() if now is None else now
        with self._lock:
            attempts = self._attempts[key]
            self._prune(attempts, now)
            if len(attempts) < self.max_attempts:
                return True, 0
            retry_after = max(1, int(self.window_seconds - (now - attempts[0])))
            return False, retry_after

    def record_failure(self, key, now=None):
        now = time.time() if now is None else now
        with self._lock:
            attempts = self._attempts[key]
            self._prune(attempts, now)
            attempts.append(now)

    def reset(self, key):
        with self._lock:
            self._attempts.pop(key, None)

    def clear(self):
        with self._lock:
            self._attempts.clear()
