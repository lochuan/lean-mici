from openpilot.system.lanlinkd.auth import (
  MIN_PASSWORD_LEN, LoginThrottle, SessionStore, hash_password, verify_password)


class TestHashPassword:
  def test_hash_is_salted_and_verifiable(self):
    h1, h2 = hash_password("hunter22"), hash_password("hunter22")
    assert h1 != h2  # 每次盐不同
    assert verify_password("hunter22", h1)
    assert not verify_password("hunter23", h1)

  def test_malformed_stored_hash_is_rejected(self):
    assert not verify_password("x", "garbage")
    assert not verify_password("x", "")
    assert not verify_password("x", "md5$1$aa$bb")


class TestSessionStore:
  def test_issue_and_validate(self):
    s = SessionStore()
    token = s.issue()
    assert s.validate(token)
    assert not s.validate("nope")

  def test_expiry(self):
    s = SessionStore(ttl_s=-1)  # 立即过期
    assert not s.validate(s.issue())

  def test_revoke_all(self):
    s = SessionStore()
    token = s.issue()
    s.revoke_all()
    assert not s.validate(token)


class TestLoginThrottle:
  def test_locks_after_threshold(self):
    t = LoginThrottle(threshold=3, lockout_s=60)
    for _ in range(3):
      t.record_failure()
    locked, remaining = t.is_locked()
    assert locked and 55 <= remaining <= 60

  def test_reset_clears(self):
    t = LoginThrottle(threshold=3, lockout_s=60)
    t.record_failure()
    t.record_failure()
    t.reset()
    assert not t.is_locked()[0]


def test_min_password_len():
  assert MIN_PASSWORD_LEN == 6
