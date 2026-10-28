import json

import httpx
import pytest
from test_summary import summary_input

from discord_personal_agent.groq_gateway import GroqGateway
from discord_personal_agent.summary import SummaryError


async def test_langchain_groq_sends_strict_schema_and_returns_validated_summary():
    calls = []

    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["messages"][0]["role"] == "system"
        assert body["max_tokens"] == 4000
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer unit-test-key"
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "openai/gpt-oss-20b",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "status": "ok",
                                    "segments": [
                                        {
                                            "title": "部署",
                                            "summary": "仍待確認。",
                                            "source_ids": ["m001"],
                                        }
                                    ],
                                }
                            ),
                        },
                    }
                ],
            },
        )

    gateway = GroqGateway(
        model="openai/gpt-oss-20b", max_output_tokens=4000, transport=httpx.MockTransport(handler)
    )
    result = await gateway.generate(summary_input(), api_key="unit-test-key")
    assert result.segments[0].title == "部署"
    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_provider_error_never_retries_or_echoes_response(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, json={"error": {"message": "PRIVATE_RESPONSE", "type": "error"}}
        )

    gateway = GroqGateway(
        model="openai/gpt-oss-20b", max_output_tokens=4000, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(SummaryError) as error:
        await gateway.generate(summary_input(), api_key="unit-test-key")
    assert len(calls) == 1
    assert "PRIVATE_RESPONSE" not in str(error.value)


@pytest.mark.parametrize(
    "finish,content",
    [
        ("length", '{"status":"ok","segments":[]}'),
        ("stop", "PRIVATE_INVALID_JSON"),
        ("stop", '{"status":"ok","segments":[{"title":"a","summary":"b","source_ids":["m999"]}]}'),
    ],
)
async def test_invalid_or_truncated_output_fails_and_closes_transport(finish, content):
    class Transport(httpx.AsyncBaseTransport):
        closed = False
        calls = 0

        async def handle_async_request(self, request):
            self.calls += 1
            return httpx.Response(
                200,
                json={
                    "id": "test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "openai/gpt-oss-20b",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": finish,
                            "message": {"role": "assistant", "content": content},
                        }
                    ],
                },
            )

        async def aclose(self):
            self.closed = True

    transport = Transport()
    gateway = GroqGateway(model="openai/gpt-oss-20b", max_output_tokens=4000, transport=transport)
    with pytest.raises(SummaryError) as error:
        await gateway.generate(summary_input(), api_key="fake")
    assert transport.closed
    assert transport.calls == 1
    assert "PRIVATE_INVALID_JSON" not in str(error.value)


@pytest.mark.parametrize("header,expected", [({"retry-after": "3"}, 3.0), ({}, None)])
async def test_rate_limit_exposes_retry_after_without_retrying(header, expected):
    from discord_personal_agent.summary import RateLimited

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429, headers=header, json={"error": {"message": "PRIVATE", "type": "rate_limit"}}
        )

    gateway = GroqGateway(
        model="openai/gpt-oss-20b", max_output_tokens=4000, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(RateLimited) as error:
        await gateway.generate(summary_input(), api_key="unit-test-key")
    assert error.value.retry_after == expected
    assert len(calls) == 1
