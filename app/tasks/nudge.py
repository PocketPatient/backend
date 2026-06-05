from __future__ import annotations

import logging

from app.celery_app import celery

logger = logging.getLogger(__name__)


@celery.task
def check_and_send_nudges() -> None:
    logger.info("check_and_send_nudges: not yet implemented")
