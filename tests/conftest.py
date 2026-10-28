import os
os.environ['API_KEY'] = 'test-api-key-at-least-24-characters'
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL', 'sqlite://')

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import Base, get_db
from app.main import app


@pytest.fixture
def client(monkeypatch):
    url = os.environ['DATABASE_URL']
    kwargs = {'connect_args': {'check_same_thread': False}, 'poolclass': StaticPool} if url == 'sqlite://' else {}
    engine = create_engine(url, **kwargs)
    # TEST_DATABASE_URL must point to a dedicated, empty test database.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def override():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr("app.main.engine", engine)
    with TestClient(app, headers={'X-API-Key': os.environ['API_KEY']}) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()
