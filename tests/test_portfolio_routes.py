import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import ASGITransport, AsyncClient


TEST_SECRET = "test-secret-key-for-testing"


def _make_token(roles=None):
    import jwt
    return jwt.encode(
        {"sub": "user-1", "email": "test@example.com", "username": "Test",
         "roles": roles or ["viewer"], "groups": [],
         "iss": "easylife-auth", "aud": "easylife-api", "exp": 4102444800},
        TEST_SECRET, algorithm="HS256",
    )


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {_make_token(['viewer'])}"}


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {_make_token(['administrator'])}"}


@pytest.fixture
def mock_db():
    db = MagicMock()
    colls = {}
    for name in ["jira_issues", "rollups_current", "rollups_snapshots"]:
        coll = AsyncMock()
        cursor = AsyncMock()
        cursor.to_list = AsyncMock(return_value=[])
        # Chainable: find().sort().skip().limit().to_list()
        chain = MagicMock()
        chain.to_list = cursor.to_list
        chain.sort = MagicMock(return_value=chain)
        chain.skip = MagicMock(return_value=chain)
        chain.limit = MagicMock(return_value=chain)
        coll.find = MagicMock(return_value=chain)
        coll.find_one = AsyncMock(return_value=None)
        coll.count_documents = AsyncMock(return_value=0)
        colls[name] = coll
    db.__getitem__ = MagicMock(side_effect=lambda n: colls.get(n, AsyncMock()))
    db._colls = colls  # expose for test manipulation
    return db


@pytest_asyncio.fixture
async def client(mock_db):
    with patch("src.db._db", mock_db), \
         patch("src.db.get_db", return_value=mock_db):
        from src.config import Config
        from src.auth import init_auth
        cfg = Config.__new__(Config)
        cfg._data = {
            "jwt": {"secret_key": TEST_SECRET, "algorithm": "HS256",
                    "issuer": "easylife-auth", "audience": "easylife-api"},
            "portfolio": {
                "capability_issue_type": "Capability",
                "remaining_statuses": ["Backlog", "In Progress"],
                "tshirt_fallback_statuses": ["Backlog", "Discovery"],
                "tshirt_size_map": {"M": 13, "L": 21},
                "done_statuses": ["Done"],
                "cycle_time_buckets": {"dev": ["In Progress"]},
            },
        }
        cfg.get = lambda key, default=None: {
            "jwt.secret_key": TEST_SECRET,
            "jwt.algorithm": "HS256",
            "jwt.issuer": "easylife-auth",
            "jwt.audience": "easylife-api",
            "portfolio.capability_issue_type": "Capability",
            "portfolio.remaining_statuses": ["Backlog", "In Progress"],
            "portfolio.tshirt_fallback_statuses": ["Backlog", "Discovery"],
            "portfolio.tshirt_size_map": {"M": 13, "L": 21},
            "portfolio.done_statuses": ["Done"],
            "portfolio.cycle_time_buckets": {"dev": ["In Progress"]},
            "server.root_path": "",
        }.get(key, default)
        init_auth(cfg)

        from src.services.rollup_engine import RollupEngine
        from src.services.snapshot_service import SnapshotService
        from src.routes.portfolio import init_portfolio_routes
        init_portfolio_routes(RollupEngine(cfg), SnapshotService(), cfg)

        from main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


class TestListCapabilities:
    @pytest.mark.asyncio
    async def test_empty(self, client, auth_headers):
        resp = await client.get("/api/v1/portfolio/capabilities?project_key=PROJ",
                                headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["total"] == 0
        assert body["has_more"] is False

    @pytest.mark.asyncio
    async def test_requires_auth(self, client):
        resp = await client.get("/api/v1/portfolio/capabilities?project_key=PROJ")
        assert resp.status_code in (401, 403)


class TestCapabilityTree:
    @pytest.mark.asyncio
    async def test_not_found(self, client, auth_headers):
        resp = await client.get("/api/v1/portfolio/capabilities/MISSING/tree",
                                headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_found(self, client, auth_headers, mock_db):
        # Set up mock to return a capability
        mock_db._colls["jira_issues"].find_one = AsyncMock(return_value={
            "key": "CAP-1", "summary": "Test Cap", "status": "Active",
            "issue_type": "Capability", "project_key": "PROJ",
        })
        # Epics query
        epic_cursor = AsyncMock()
        epic_cursor.to_list = AsyncMock(return_value=[])
        mock_db._colls["jira_issues"].find = MagicMock(return_value=epic_cursor)
        # Rollup
        mock_db._colls["rollups_current"].find_one = AsyncMock(return_value=None)
        mock_db._colls["rollups_current"].find = MagicMock(
            return_value=AsyncMock(to_list=AsyncMock(return_value=[])))

        resp = await client.get("/api/v1/portfolio/capabilities/CAP-1/tree",
                                headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["key"] == "CAP-1"
        assert body["epics"] == []


class TestEpicChildren:
    @pytest.mark.asyncio
    async def test_empty(self, client, auth_headers):
        resp = await client.get("/api/v1/portfolio/epics/EPIC-1/children",
                                headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["total"] == 0


class TestSnapshots:
    @pytest.mark.asyncio
    async def test_run_requires_admin(self, client, auth_headers):
        resp = await client.post("/api/v1/portfolio/snapshots/run",
                                 json={"project_key": "PROJ"},
                                 headers=auth_headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_run_as_admin(self, client, admin_headers):
        resp = await client.post("/api/v1/portfolio/snapshots/run",
                                 json={"project_key": "PROJ"},
                                 headers=admin_headers)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_series(self, client, auth_headers, mock_db):
        cursor = AsyncMock()
        cursor.to_list = AsyncMock(return_value=[])
        mock_sort = MagicMock(return_value=cursor)
        mock_db._colls["rollups_snapshots"].find = MagicMock(
            return_value=MagicMock(sort=mock_sort))

        resp = await client.get("/api/v1/portfolio/snapshots/CAP-1?metric=remaining",
                                headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["key"] == "CAP-1"
        assert body["series"] == []


class TestRecompute:
    @pytest.mark.asyncio
    async def test_requires_admin(self, client, auth_headers):
        resp = await client.post("/api/v1/portfolio/recompute?project_key=PROJ",
                                 headers=auth_headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_as_admin(self, client, admin_headers, mock_db):
        # Mock the DB calls that recompute_all makes
        for coll_name in ["jira_issues", "rollups_current"]:
            cursor = AsyncMock()
            cursor.to_list = AsyncMock(return_value=[])
            mock_db._colls[coll_name].find = MagicMock(return_value=cursor)

        resp = await client.post("/api/v1/portfolio/recompute?project_key=PROJ",
                                 headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "capabilities_computed" in body
