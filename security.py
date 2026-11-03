"""Security helpers (standard library only).

Passwords: PBKDF2-HMAC-SHA256, 200 000 iterations, 16-byte random salt per user.
Stored format: pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
"""
import hashlib, hmac, secrets, time

ITERATIONS = 200_000


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
