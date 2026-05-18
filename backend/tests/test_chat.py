from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_chat_completions_mock():
    response = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "mock/spice-sibyl-1",
            "messages": [{"role": "user", "content": "ciao"}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["role"] == "assistant"
