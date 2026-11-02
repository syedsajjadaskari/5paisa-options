"""The 5paisa SDK client proxy is an ALLOWLIST: every other SDK member is unreachable."""

from __future__ import annotations

import pytest

from trading_platform.brokers.fivepaisa import (
    _ALLOWED_ATTRIBUTES,
    _ALLOWED_METHODS,
    _BLOCKED_METHOD_NAMES,
    FivePaisaBroker,
    ReadOnlyBoundaryError,
    _ReadOnlyFivePaisaClientProxy,
)
from trading_platform.core.exceptions import LiveTradingPermissionError


def _sdk_public_members() -> list[str]:
    sdk = pytest.importorskip("py5paisa")
    return sorted(name for name in dir(sdk.FivePaisaClient) if not name.startswith("_"))


def _fake_client_for(names: list[str]):
    """Fake exposing every public SDK name; calling any of them would record a call."""
    calls: list[str] = []

    def make(name: str):
        def method(self, *args, **kwargs):
            calls.append(name)
            return f"CALLED:{name}"

        return method

    cls = type("FakeSdk", (), {name: make(name) for name in names})
    fake = cls()
    fake.session = object()
    fake.payload = {"body": {"TOTP": "x"}}
    fake.Jwt_token = "jwt-secret-value"
    fake.USER_KEY = "key-secret-value"
    fake.access_token = "tok-visible"
    fake.client_code = "CLIENT"
    return fake, calls


def test_every_sdk_member_outside_the_allowlist_raises_permission_error() -> None:
    names = _sdk_public_members()
    assert "place_order" in names and "fetch_market_depth" in names  # sanity: real SDK introspected
    fake, calls = _fake_client_for(names)
    proxy = _ReadOnlyFivePaisaClientProxy(fake)

    blocked = [n for n in names if n not in _ALLOWED_METHODS and n not in _ALLOWED_ATTRIBUTES]
    assert len(blocked) > 40  # orders, baskets, GTT, positions, holdings, margin, ledger, ws ...
    for name in blocked:
        with pytest.raises(PermissionError):
            getattr(proxy, name)()
    assert calls == []  # not one blocked method reached the wrapped client


def test_allowlisted_members_exist_in_sdk_and_delegate() -> None:
    names = set(_sdk_public_members())
    assert _ALLOWED_METHODS <= names, "allowlist references a member the SDK no longer has"
    fake, calls = _fake_client_for(sorted(names))
    proxy = _ReadOnlyFivePaisaClientProxy(fake)
    for name in sorted(_ALLOWED_METHODS):
        assert getattr(proxy, name)() == f"CALLED:{name}"
    assert proxy.access_token == "tok-visible"
    assert proxy.client_code == "CLIENT"


def test_allowlist_contains_no_transactional_or_account_member() -> None:
    assert not (_ALLOWED_METHODS & _BLOCKED_METHOD_NAMES)
    dangerous = (
        "order", "basket", "cover", "squareoff", "convert", "vtt", "holding", "margin", "position",
        "ledger", "tax", "tradebook", "trade_history", "bulk",
    )
    for name in _ALLOWED_METHODS | _ALLOWED_ATTRIBUTES:
        assert not any(word in name.lower() for word in dangerous), name


@pytest.mark.parametrize(
    "attribute",
    ["session", "payload", "Jwt_token", "USER_KEY", "_ro_target", "_wrapped", "__dict__", "order_request"],
)
def test_internals_of_the_wrapped_client_are_hidden(attribute: str) -> None:
    fake, _ = _fake_client_for(["fetch_market_depth"])
    proxy = _ReadOnlyFivePaisaClientProxy(fake)
    with pytest.raises(PermissionError):
        getattr(proxy, attribute)
    assert not hasattr(type(proxy), "_wrapped")


def test_proxy_is_immutable_and_repr_leaks_nothing() -> None:
    fake, _ = _fake_client_for(["fetch_market_depth"])
    proxy = _ReadOnlyFivePaisaClientProxy(fake)
    with pytest.raises(PermissionError):
        proxy.fetch_market_depth = lambda *_: None  # type: ignore[method-assign]
    with pytest.raises(PermissionError):
        del proxy.fetch_market_depth
    assert "secret" not in repr(proxy)
    assert set(dir(proxy)) == set(_ALLOWED_METHODS | _ALLOWED_ATTRIBUTES)


def test_boundary_error_is_both_permission_error_and_live_trading_permission_error() -> None:
    proxy = _ReadOnlyFivePaisaClientProxy(object())
    with pytest.raises(LiveTradingPermissionError):
        proxy.place_order()
    with pytest.raises(PermissionError):
        proxy.place_order()
    assert issubclass(ReadOnlyBoundaryError, PermissionError)


def test_broker_client_setter_wraps_once_and_none_clears() -> None:
    broker = FivePaisaBroker()
    fake, _ = _fake_client_for(["fetch_market_depth", "place_order"])
    broker.client = fake
    assert isinstance(broker.client, _ReadOnlyFivePaisaClientProxy)
    first = broker.client
    broker.client = first
    assert broker.client is first
    with pytest.raises(PermissionError):
        broker.client.place_order()
    assert broker.client.fetch_market_depth() == "CALLED:fetch_market_depth"
    broker.client = None
    assert broker.client is None
