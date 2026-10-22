import pytest

from notes import create_app


@pytest.fixture
def app():
    app = create_app()
    app.config["TESTING"] = True  # propagate exceptions to the test client
    return app


@pytest.fixture
def client(app):
    return app.test_client()
