from functools import lru_cache

from httpx import AsyncClient, HTTPStatusError
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential


def should_retry_status(response) -> None:
    """Raise on retryable HTTP status codes so tenacity can handle them."""
    if response.status_code in (429, 502, 503, 504):
        response.raise_for_status()


@lru_cache
def create_retrying_client() -> AsyncClient:
    """Create an HTTP client with smart retry handling for transient failures."""
    transport = AsyncTenacityTransport(
        config=RetryConfig(
            retry=retry_if_exception_type((HTTPStatusError, ConnectionError)),
            wait=wait_retry_after(
                fallback_strategy=wait_exponential(multiplier=1, max=60),
                max_wait=300,
            ),
            stop=stop_after_attempt(5),
            reraise=True,
        ),
        validate_response=should_retry_status,
    )
    return AsyncClient(transport=transport)


def create_google_retry_model(model: str):
    return GoogleModel(model, provider=GoogleProvider(http_client=create_retrying_client()))
