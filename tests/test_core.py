import dataclasses
import pytest

from webmouse.config import Config, ConfigError, default_config_text, load_config
from webmouse.net import host_allowed
from webmouse.security import AuthManager, LoginError, NetworkPolicy, clean_name


# ---------------------------------------------------------------- config
def test_defaults_file_matches_dataclasses(tmp_path):
    """The shipped default_config.toml must parse and equal the built-in defaults."""
    p = tmp_path / "c.toml"
    p.write_text(default_config_text())
    cfg, found = load_config(str(p), env={})
    assert found == p
    assert dataclasses.asdict(cfg) == dataclasses.asdict(Config())


def test_precedence_file_env_cli(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[server]\nport = 9000\n")
    cfg, _ = load_config(str(p), env={})
    assert cfg.server.port == 9000
    cfg, _ = load_config(str(p), env={"PADLINK_PORT": "9100"})
    assert cfg.server.port == 9100
    cfg, _ = load_config(str(p), cli={"server": {"port": 9200}}, env={"PADLINK_PORT": "9100"})
    assert cfg.server.port == 9200


@pytest.mark.parametrize("toml", [
    "[server]\nport = 'x'\n",
    "[server]\nport = 70000\n",
    "[security]\npin = 'ab'\n",
    "[security]\nallowed_networks = ['nonsense']\n",
    "[ui]\ntheme = 'purple'\n",
    "[stream]\nfps = 0\n",
])
def test_invalid_config(tmp_path, toml):
    p = tmp_path / "c.toml"
    p.write_text(toml)
    with pytest.raises(ConfigError):
        load_config(str(p), env={})


def test_numeric_pin_accepted(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[security]\npin = 123456\n")
    cfg, _ = load_config(str(p), env={})
    assert cfg.security.pin == "123456"


# ---------------------------------------------------------------- LAN policy
@pytest.mark.parametrize("addr,ok", [
    ("127.0.0.1", True), ("192.168.1.20", True), ("10.4.5.6", True),
    ("172.16.0.1", True), ("172.31.255.255", True), ("169.254.1.1", True),
    ("::1", True), ("fe80::1%eth0", True), ("fd12:3456::1", True),
    ("::ffff:192.168.1.5", True),
    ("8.8.8.8", False), ("172.32.0.1", False), ("100.64.0.1", False),
    ("2001:4860:4860::8888", False), ("::ffff:8.8.8.8", False),
    ("", False), (None, False), ("garbage", False),
])
def test_network_policy(addr, ok):
    assert NetworkPolicy().allows(addr) is ok


def test_extra_networks():
    assert NetworkPolicy(["100.64.0.0/10"]).allows("100.100.1.1")


def test_host_header():
    names = {"mybox", "localhost"}
    for good in ["192.168.1.2:8765", "[::1]:8765", "localhost", "mybox:80", "printer.local:1"]:
        assert host_allowed(good, names), good
    for bad in ["evil.example.com", "evil.example.com:8765", "", "192.168.1.2.evil.com"]:
        assert not host_allowed(bad, names), bad


# ---------------------------------------------------------------- auth
class Clock:
    t = 1000.0
    def __call__(self):
        return self.t


def make_auth(**kw):
    cfg = Config().security
    for k, v in kw.items():
        setattr(cfg, k, v)
    clock = Clock()
    return AuthManager(cfg, "246810", now=clock), clock


def test_login_ok_and_session_expiry():
    auth, clock = make_auth(session_hours=1)
    s = auth.login("1.1.1.1", "246810", "Phone")
    assert auth.get(s.token) is s
    clock.t += 3601
    assert auth.get(s.token) is None


def test_wrong_pin_lockout_escalates():
    auth, clock = make_auth(max_failed_attempts=3, lockout_seconds=10)
    for _ in range(2):
        with pytest.raises(LoginError) as e:
            auth.login("1.1.1.1", "000000", "x")
        assert e.value.status == 401
    with pytest.raises(LoginError) as e:
        auth.login("1.1.1.1", "000000", "x")
    assert e.value.status == 429 and e.value.retry_after == 10
    # even the right PIN is refused while locked
    with pytest.raises(LoginError) as e:
        auth.login("1.1.1.1", "246810", "x")
    assert e.value.status == 429
    # other IPs are unaffected
    auth.login("2.2.2.2", "246810", "x")
    clock.t += 11
    for _ in range(3):
        with pytest.raises(LoginError) as e:
            auth.login("1.1.1.1", "000000", "x")
    assert e.value.retry_after == 20  # doubled


def test_device_limit_evicts_least_recent():
    auth, clock = make_auth(max_devices=2)
    a = auth.login("1.1.1.1", "246810", "a"); clock.t += 1
    b = auth.login("1.1.1.2", "246810", "b"); clock.t += 1
    auth.get(a.token); clock.t += 1  # a is now more recent than b
    auth.login("1.1.1.3", "246810", "c")
    assert auth.get(a.token) and auth.get(b.token) is None


def test_clean_name():
    assert clean_name("  Bob\x00\n ") == "Bob"
    assert clean_name("") == "Unnamed device"
    assert len(clean_name("x" * 100)) == 32
