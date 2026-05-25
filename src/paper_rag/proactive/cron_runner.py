"""Proactive cron runner — APScheduler loop for daily digest + weekly stale scan.

Used by the standalone `paper-rag:proactive` container (see Dockerfile +
docker-entrypoint.sh `proactive` mode). The gateway service does NOT run
this — it lives in a sidecar so cron failures do not impact API latency.

Design
------
* APScheduler BlockingScheduler (single-process, no Redis broker needed).
* Two jobs:
    - daily_digest @ 08:00 local → digest.daily_digest_for_all_users()
    - stale_scan   @ Mon 09:00   → stale.stale_scan_for_all_users()
* Both jobs run in their own thread; max 1 instance each (skip if previous
  run still in flight).
* Crashes are logged but do NOT bring down the scheduler.

Override schedule via env:
    PAPER_RAG_CRON_DIGEST="0 8 * * *"   # daily 08:00
    PAPER_RAG_CRON_STALE="0 9 * * 1"    # Monday 09:00
    PAPER_RAG_CRON_TZ="Asia/Shanghai"
"""
from __future__ import annotations

import logging
import os
import signal
import sys
from typing import Any

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("PAPER_RAG_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _job_daily_digest() -> None:
    from paper_rag.proactive.digest import daily_digest_for_all_users

    log.info("[cron] daily_digest start")
    try:
        result: dict[str, Any] = daily_digest_for_all_users()
        log.info("[cron] daily_digest done: %s", result)
    except Exception:
        log.exception("[cron] daily_digest crashed")


def _job_stale_scan() -> None:
    from paper_rag.proactive.stale import stale_scan_for_all_users

    log.info("[cron] stale_scan start")
    try:
        result: dict[str, Any] = stale_scan_for_all_users()
        log.info("[cron] stale_scan done: %s", result)
    except Exception:
        log.exception("[cron] stale_scan crashed")


def main() -> int:
    _setup_logging()
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.error(
            "APScheduler not installed. `pip install apscheduler` "
            "or rebuild the image with EXTRAS=proactive."
        )
        return 2

    tz = os.environ.get("PAPER_RAG_CRON_TZ", "Asia/Shanghai")
    digest_cron = os.environ.get("PAPER_RAG_CRON_DIGEST", "0 8 * * *")
    stale_cron = os.environ.get("PAPER_RAG_CRON_STALE", "0 9 * * 1")

    scheduler = BlockingScheduler(timezone=tz)
    scheduler.add_job(
        _job_daily_digest,
        trigger=CronTrigger.from_crontab(digest_cron, timezone=tz),
        id="daily_digest",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        _job_stale_scan,
        trigger=CronTrigger.from_crontab(stale_cron, timezone=tz),
        id="stale_scan",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    log.info(
        "[cron_runner] scheduler ready tz=%s digest='%s' stale='%s'",
        tz,
        digest_cron,
        stale_cron,
    )

    # Graceful shutdown on SIGTERM (Docker stop)
    def _stop(signum, _frame):
        log.info("[cron_runner] received signal=%s, shutting down", signum)
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
