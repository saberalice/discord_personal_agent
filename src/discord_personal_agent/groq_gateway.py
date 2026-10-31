"""One Groq request through LangChain; no retry, tracing, or persistent model clients."""

import logging

import groq
import httpx
from langchain_groq import ChatGroq
from langsmith import tracing_context
from pydantic import BaseModel, SecretStr

from .summary import RateLimited, SummaryError, SummaryInput, SummaryResult, validate_summary
from .transcript_question import AnswerResult, validate_answer

logger = logging.getLogger(__name__)


class GroqGateway:
    def __init__(
        self,
        *,
        model: str,
        max_output_tokens: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.transport = transport

    async def generate(self, request: SummaryInput, *, api_key: str) -> SummaryResult:
        return validate_summary(await self._complete(request, SummaryResult, api_key), request)

    async def answer(self, request: SummaryInput, *, api_key: str) -> AnswerResult:
        return validate_answer(await self._complete(request, AnswerResult, api_key), request)

    async def _complete(self, request: SummaryInput, schema: type[BaseModel], api_key: str) -> str:
        # SDK debug logging can include request bodies. This boundary never enables it.
        logging.getLogger("groq").setLevel(logging.WARNING)
        try:
            with groq.Groq(
                api_key=api_key, base_url="https://api.groq.com", max_retries=0, timeout=90
            ) as sync_client:
                async with httpx.AsyncClient(transport=self.transport, trust_env=False) as http:
                    async with groq.AsyncGroq(
                        api_key=api_key,
                        base_url="https://api.groq.com",
                        max_retries=0,
                        timeout=90,
                        http_client=http,
                    ) as client:
                        model = ChatGroq(
                            model=self.model,
                            api_key=SecretStr(api_key),
                            client=sync_client.chat.completions,
                            async_client=client.chat.completions,
                            max_retries=0,
                            timeout=90,
                            max_tokens=self.max_output_tokens,
                            temperature=0,
                            cache=False,
                            verbose=False,
                            callbacks=[],
                        )
                        chain = model.with_structured_output(
                            schema, method="json_schema", strict=True, include_raw=True
                        )
                        with tracing_context(enabled=False):
                            response = await chain.ainvoke(
                                list(request.messages), config={"callbacks": []}
                            )
            raw = response["raw"]
            if raw.response_metadata.get("finish_reason") != "stop":
                raise SummaryError("模型未完整完成輸出，請縮小範圍後重試。")
            if response["parsing_error"] is not None or not isinstance(raw.content, str):
                raise SummaryError("模型回覆格式無效，未交付結果。")
            return raw.content
        except groq.AuthenticationError:
            raise SummaryError("Groq 驗證失敗，請管理員檢查本地 API Key。") from None
        except groq.RateLimitError as exc:
            # The workflow decides whether to wait; this boundary only reports the provider's hint.
            raise RateLimited(
                "Groq 額度或速率限制，請稍後再試。", retry_after=_retry_after(exc.response)
            ) from None
        except (groq.APITimeoutError, groq.APIConnectionError):
            raise SummaryError("Groq 連線失敗或逾時，未自動重試。") from None
        except groq.APIStatusError as exc:
            raise _status_error(exc) from None


def _status_error(exc: groq.APIStatusError) -> SummaryError:
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", body)
    error = error if isinstance(error, dict) else {}
    code = error.get("code")
    if code != "json_validate_failed":
        logger.warning("groq_request_failed status=%d code=%s", exc.status_code, code)
        return SummaryError(f"Groq 請求失敗（HTTP {exc.status_code}），未自動重試。")
    # failed_generation may hold model output built from private messages, so it is only
    # compared against the provider's fixed sentence, never logged or shown.
    too_long = "max completion tokens" in str(error.get("failed_generation"))
    logger.warning("groq_generation_failed reason=%s", "too_long" if too_long else "invalid_output")
    if too_long:
        return SummaryError("模型回覆超過輸出上限，未交付結果；請改問更具體的問題或縮小範圍。")
    return SummaryError("模型未能產生符合格式的回覆，未交付結果；可再試一次。")


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return max(0.0, float(response.headers["retry-after"]))
    except (KeyError, ValueError):
        return None
