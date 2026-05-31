import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None


GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
DEFAULT_REQUEST_DELAY_SECONDS = 16.0
DEFAULT_MIN_REMAINING_TOKENS = 5500
DEFAULT_RATE_LIMIT_BUFFER_SECONDS = 2.0

logger = logging.getLogger(__name__)


class GroqClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: int = 90,
    ):
        if load_dotenv:
            load_dotenv()

        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        self.model = model or os.getenv("GROQ_MODEL") or os.getenv("LLAMA_MODEL") or DEFAULT_GROQ_MODEL
        self.timeout_seconds = timeout_seconds
        self.request_delay_seconds = _env_float("GROQ_REQUEST_DELAY_SECONDS", DEFAULT_REQUEST_DELAY_SECONDS)
        self.max_retries = _env_int("GROQ_MAX_RETRIES", 5)
        self.min_remaining_tokens = _env_int("GROQ_MIN_REMAINING_TOKENS", DEFAULT_MIN_REMAINING_TOKENS)
        self.rate_limit_buffer_seconds = _env_float(
            "GROQ_RATE_LIMIT_BUFFER_SECONDS",
            DEFAULT_RATE_LIMIT_BUFFER_SECONDS,
        )
        self.openai_client = None

        if not self.api_key:
            raise ValueError("GROQ_API_KEY is not configured.")

        if OpenAI is not None:
            self.openai_client = OpenAI(
                api_key=self.api_key,
                base_url=GROQ_BASE_URL,
                max_retries=0,
            )

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.0) -> str:
        if self.openai_client is not None:
            return self._chat_with_openai_sdk(messages, temperature=temperature)

        return self._chat_with_urllib(messages, temperature=temperature)

    def _chat_with_openai_sdk(self, messages: List[Dict[str, str]], temperature: float = 0.0) -> str:
        for attempt in range(self.max_retries + 1):
            self._sleep_before_request()
            try:
                raw_response = self.openai_client.chat.completions.with_raw_response.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
                self._apply_rate_limit_headers(raw_response.headers)
                response = raw_response.parse()
                return response.choices[0].message.content or ""
            except Exception as exc:
                wait_seconds = self._retry_wait_from_exception(exc)
                if wait_seconds is None or attempt >= self.max_retries:
                    raise

                logger.warning(
                    "Groq request was rate limited. Waiting %.1f seconds before retry %s/%s.",
                    wait_seconds,
                    attempt + 1,
                    self.max_retries,
                )
                time.sleep(wait_seconds)

        raise RuntimeError("Groq request failed after retries.")

    def _chat_with_urllib(self, messages: List[Dict[str, str]], temperature: float = 0.0) -> str:
        self._sleep_before_request()
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            GROQ_CHAT_COMPLETIONS_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    self._apply_rate_limit_headers(response.headers)
                    data = json.loads(response.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as exc:
                retry_wait = self._retry_wait_from_headers(exc.headers)
                if exc.code == 429 and retry_wait is not None and attempt < self.max_retries:
                    logger.warning(
                        "Groq request was rate limited. Waiting %.1f seconds before retry %s/%s.",
                        retry_wait,
                        attempt + 1,
                        self.max_retries,
                    )
                    time.sleep(retry_wait)
                    continue

                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Groq request failed with HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"Groq request failed: {exc.reason}") from exc

        raise RuntimeError("Groq request failed after retries.")

    def _sleep_before_request(self) -> None:
        if self.request_delay_seconds <= 0:
            return

        logger.info("Waiting %.1f seconds before Groq request", self.request_delay_seconds)
        time.sleep(self.request_delay_seconds)

    def _apply_rate_limit_headers(self, headers: Any) -> None:
        remaining_tokens = _header_int(headers, "x-ratelimit-remaining-tokens")
        reset_tokens = _header_duration_seconds(headers, "x-ratelimit-reset-tokens")

        if remaining_tokens is None or reset_tokens is None:
            return

        logger.info(
            "Groq token limit remaining=%s, reset=%ss",
            remaining_tokens,
            round(reset_tokens, 2),
        )

        if remaining_tokens < self.min_remaining_tokens:
            wait_seconds = reset_tokens + self.rate_limit_buffer_seconds
            logger.info(
                "Groq remaining tokens below %s. Waiting %.1f seconds for token reset.",
                self.min_remaining_tokens,
                wait_seconds,
            )
            time.sleep(wait_seconds)

    def _retry_wait_from_exception(self, exc: Exception) -> Optional[float]:
        status_code = getattr(exc, "status_code", None)
        if status_code != 429:
            return None

        headers = getattr(exc, "headers", None)
        return self._retry_wait_from_headers(headers) or self.request_delay_seconds

    def _retry_wait_from_headers(self, headers: Any) -> Optional[float]:
        retry_after = _header_duration_seconds(headers, "retry-after")
        reset_tokens = _header_duration_seconds(headers, "x-ratelimit-reset-tokens")
        wait_seconds = retry_after if retry_after is not None else reset_tokens
        if wait_seconds is None:
            return None
        return wait_seconds + self.rate_limit_buffer_seconds


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _header_value(headers: Any, name: str) -> Optional[str]:
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except AttributeError:
        value = None
    if value is None:
        return None
    return str(value).strip()


def _header_int(headers: Any, name: str) -> Optional[int]:
    value = _header_value(headers, name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _header_duration_seconds(headers: Any, name: str) -> Optional[float]:
    value = _header_value(headers, name)
    if not value:
        return None
    return _duration_to_seconds(value)


def _duration_to_seconds(value: str) -> Optional[float]:
    text = value.strip().lower()
    try:
        return float(text)
    except ValueError:
        pass

    match = re.fullmatch(r"(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?", text)
    if not match:
        return None

    minutes = float(match.group(1) or 0)
    seconds = float(match.group(2) or 0)
    return minutes * 60 + seconds
