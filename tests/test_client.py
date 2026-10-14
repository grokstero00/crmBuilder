import httpx
import pytest

from crmbuilder.bitrix import BitrixClient, BitrixError

from .conftest import WEBHOOK, FakePortal, paginate


def test_call_posts_json_to_method_url(portal: FakePortal):
    portal.on("profile", lambda p: {"result": {"ID": "1"}})
    assert portal.client().call("profile", {"a": 1}) == {"ID": "1"}
    assert portal.calls == [("profile", {"a": 1})]


def test_portal_host_is_taken_from_webhook():
    assert BitrixClient(WEBHOOK).portal == "clinic.bitrix24.ru"


def test_iter_list_follows_next(portal: FakePortal):
    items = [{"ID": str(i)} for i in range(5)]
    portal.on("crm.deal.list", lambda p: paginate(items, p))
    assert list(portal.client().iter_list("crm.deal.list")) == items
    assert [c[1].get("start") for c in portal.calls] == [None, 2, 4]


def test_error_raises_bitrix_error(portal: FakePortal):
    with pytest.raises(BitrixError) as info:
        portal.client().call("crm.unknown")
    assert info.value.code == "ERROR_METHOD_NOT_FOUND"


def test_query_limit_is_retried():
    attempts = []

    def handle(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, json={"error": "QUERY_LIMIT_EXCEEDED"})
        return httpx.Response(200, json={"result": True})

    sleeps: list[float] = []
    client = BitrixClient(WEBHOOK, transport=httpx.MockTransport(handle), sleep=sleeps.append)
    assert client.call("crm.deal.list") is True
    assert sleeps == [0.5, 1.0]


def test_query_limit_gives_up_after_max_retries():
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "QUERY_LIMIT_EXCEEDED"})

    client = BitrixClient(
        WEBHOOK, transport=httpx.MockTransport(handle), max_retries=2, sleep=lambda _: None
    )
    with pytest.raises(BitrixError, match="QUERY_LIMIT_EXCEEDED"):
        client.call("crm.deal.list")
