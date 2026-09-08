"""密码认证纯逻辑：PBKDF2 哈希、session token、登录防爆破。无外部依赖。"""
import hashlib
import hmac
import secrets
import time

MIN_PASSWORD_LEN = 6
PBKDF2_ITERATIONS = 100_000
SESSION_TTL_S = 7 * 24 * 3600
LOCKOUT_THRESHOLD = 5
LOCKOUT_SECONDS = 300


def hash_password(password: str) -> str:
  salt = secrets.token_bytes(16)
  digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
  return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
  try:
    algo, iterations, salt_hex, hash_hex = stored.split("$")
    if algo != "pbkdf2_sha256":
      return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), hash_hex)
  except (ValueError, AttributeError):
    return False


class SessionStore:
  def __init__(self, ttl_s: int = SESSION_TTL_S):
    self._ttl_s = ttl_s
    self._sessions: dict[str, float] = {}

  def issue(self) -> str:
    token = secrets.token_urlsafe(32)
    self._sessions[token] = time.monotonic() + self._ttl_s
    return token

  def validate(self, token: str) -> bool:
    expiry = self._sessions.get(token)
    if expiry is None:
      return False
    if time.monotonic() > expiry:
      del self._sessions[token]
      return False
    return True

  def revoke_all(self) -> None:
    self._sessions.clear()


class LoginThrottle:
  def __init__(self, threshold: int = LOCKOUT_THRESHOLD, lockout_s: int = LOCKOUT_SECONDS):
    self._threshold = threshold
    self._lockout_s = lockout_s
    self._failures = 0
    self._locked_until = 0.0

  def record_failure(self) -> None:
    self._failures += 1

  def is_locked(self) -> tuple[bool, int]:
    now = time.monotonic()
    if now < self._locked_until:
      return True, int(self._locked_until - now)
    if self._failures >= self._threshold:
      self._locked_until = now + self._lockout_s
      self._failures = 0
      return True, self._lockout_s
    return False, 0

  def reset(self) -> None:
    self._failures = 0
    self._locked_until = 0.0
