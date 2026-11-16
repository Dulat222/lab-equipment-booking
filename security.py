"""Security helpers (standard library only).

Passwords: PBKDF2-HMAC-SHA256, 600 000 iterations, 16-byte random salt per user.
Stored format: pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
"""
import hashlib, hmac, secrets, time

ITERATIONS = 600_000  # OWASP Password Storage Cheat Sheet recommendation for PBKDF2-HMAC-SHA256


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(digest.hex(), hash_hex)  # constant-time comparison
    except (ValueError, TypeError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


class LoginLimiter:
    """Blocks an email for LOCK_SECONDS after MAX_FAILS failed logins (brute-force protection)."""
    MAX_FAILS, LOCK_SECONDS = 5, 300

    def __init__(self):
        self.fails = {}

    def locked(self, key):
        count, until = self.fails.get(key, (0, 0))
        return count >= self.MAX_FAILS and time.time() < until

    def fail(self, key):
        count, _ = self.fails.get(key, (0, 0))
        self.fails[key] = (count + 1, time.time() + self.LOCK_SECONDS)

    def reset(self, key):
        self.fails.pop(key, None)


# ---- CSRF protection --------------------------------------------------------------
# Logged-in users: token = HMAC(server secret, session token), so it is bound to the session and
# cannot be guessed or reused by another site. Visitors (login/register forms): random value kept
# in an HttpOnly cookie and repeated in a hidden form field (double-submit).
import os as _os
CSRF_SECRET = (_os.environ.get("CSRF_SECRET") or secrets.token_hex(32)).encode()


def csrf_for_session(session_token: str) -> str:
    return hmac.new(CSRF_SECRET, session_token.encode(), hashlib.sha256).hexdigest()


def csrf_equal(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a, b)
