import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from crmbuilder.bitrix import BitrixClient  # noqa: E402
from crmbuilder_web.config import Settings  # noqa: E402
from crmbuilder_web.main import create_app  # noqa: E402
from crmbuilder_web.migrations import upgrade  # noqa: E402

from ..fake_bitrix import FakeBitrix, seeded_portal  # noqa: E402

WEBHOOK = "https://test.bitrix24.ru/rest/1/abc123/"


@pytest.fixture
def fake() -> FakeBitrix:
    return seeded_portal()


@pytest.fixture
def opened(fake: FakeBitrix) -> list[BitrixClient]:
    return []


@pytest.fixture
def app(tmp_path, fake, opened):
    url = f"sqlite:///{tmp_path / 'web.db'}"
    upgrade(url)  # the real migrations, not create_all

    def factory(webhook: str) -> BitrixClient:
        client = BitrixClient(webhook, transport=fake.transport(), sleep=lambda _: None)
        opened.append(client)
        return client

    settings = Settings(database_url=url, secret_key="x" * 40)
    return create_app(settings, bitrix_factory=factory)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def user(client: TestClient) -> TestClient:
    """The client logged in as a fresh user."""
    r = client.post("/api/auth/register", json={"email": "a@b.kz", "password": "secret-123"})
    assert r.status_code == 201, r.text
    return client


@pytest.fixture
def project(user: TestClient) -> dict:
    r = user.post("/api/projects", json={"name": "Тест", "webhook": WEBHOOK})
    assert r.status_code == 201, r.text
    return r.json()
