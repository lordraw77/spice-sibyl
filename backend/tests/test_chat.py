def test_chat_completions_mock(client, auth_headers):
    response = client.post(
        "/api/v1/chat/completions",
        headers=auth_headers,
        json={
            "model": "mock/spice-sibyl-1",
            "messages": [{"role": "user", "content": "ciao"}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["role"] == "assistant"


def test_chat_requires_auth(client):
    response = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "mock/spice-sibyl-1",
            "messages": [{"role": "user", "content": "ciao"}],
            "stream": False,
        },
    )
    assert response.status_code == 401
