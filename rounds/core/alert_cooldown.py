"""Shared alert-cooldown persistence helper.

Both Investigator and ManagementService notify a signature and then need to
persist the resulting alert cooldown timestamp (`Signature.last_alerted_at`).
The retry policy is defined once here so the two callers stay consistent.
"""

import asyncio
import logging

from .models import Signature
from .ports import SignatureStorePort

logger = logging.getLogger(__name__)

# A notification channel's alert has already been sent by the time its
# cooldown timestamp is persisted here, so a store failure risks a duplicate
# alert within the cooldown window on the next poll cycle. Retry a few times
# before giving up.
ALERT_COOLDOWN_PERSIST_MAX_ATTEMPTS = 3
ALERT_COOLDOWN_PERSIST_RETRY_DELAY_SECONDS = 1.0


async def persist_alert_cooldown(store: SignatureStorePort, signature: Signature) -> None:
    """Persist a just-recorded alert cooldown timestamp, retrying on transient failure.

    The notification channel has already delivered its alert by the time
    this runs, so a lost update here means the next poll cycle sees the
    cooldown as unset and may re-send a duplicate alert. Retries give
    transient store errors a chance to clear before that happens; a final
    failure is logged loudly but not re-raised, since a duplicate alert is
    the acceptable worst case and this is not a failure of the alert itself
    - the alert was already delivered.
    """
    for attempt in range(1, ALERT_COOLDOWN_PERSIST_MAX_ATTEMPTS + 1):
        try:
            await store.update(signature)
            return
        except Exception as e:
            if attempt == ALERT_COOLDOWN_PERSIST_MAX_ATTEMPTS:
                logger.error(
                    "Failed to persist alert cooldown after alert was sent; "
                    "duplicate alerts may occur within the cooldown window",
                    extra={"signature_id": signature.id, "attempts": attempt},
                    exc_info=True,
                )
                return
            logger.warning(
                f"Failed to persist alert cooldown (attempt {attempt}/"
                f"{ALERT_COOLDOWN_PERSIST_MAX_ATTEMPTS}): {e}",
                extra={"signature_id": signature.id},
            )
            await asyncio.sleep(ALERT_COOLDOWN_PERSIST_RETRY_DELAY_SECONDS)
