import pytest

from energygraph.db import Neo4jClient

PUBLIC = ["public"]
LICENSED = ["public", "org:power-assets", "org:outages", "org:news", "org:companies", "org:projects",
            "org:price-series", "org:prices"]
# Strings that exist only in organization-source sample data.
ORG_ONLY = ["Kestrel", "org:PP-0001", "org:OUT-5001", "org:BENCH-GULF-LNG", "org:PRJ-7001", "org:NEWS-9001"]


@pytest.fixture(scope="session")
def db():
    client = Neo4jClient(read_only=True)
    if not client.ping():
        pytest.skip("Neo4j not reachable (docker compose up -d neo4j)")
    if not client.read("MATCH (a:Asset {asset_id: 'seed:harbor-lng'}) RETURN 1 LIMIT 1"):
        pytest.skip("graph not seeded (python -m energygraph seed)")
    yield client
    client.close()
