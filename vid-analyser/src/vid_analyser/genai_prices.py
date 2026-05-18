import logging

from genai_prices import UpdatePrices

logger = logging.getLogger(__name__)

_price_updates: UpdatePrices | None = None


def start_price_updates() -> None:
    global _price_updates
    if _price_updates is not None:
        return
    try:
        _price_updates = UpdatePrices()
        _price_updates.start(wait=False)
        logger.info("Started genai-prices background updates")
    except Exception:
        _price_updates = None
        logger.warning("genai-prices background updates not configured")


def stop_price_updates() -> None:
    global _price_updates
    if _price_updates is None:
        return
    try:
        _price_updates.stop()
    except Exception:
        logger.warning("Failed to stop genai-prices background updates")
    finally:
        _price_updates = None
