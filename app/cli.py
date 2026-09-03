from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from app.config import AppConfig
from app.db import close_engine, init_engine
from app.logging_config import configure_logging
from app.repositories.oauth import save_refresh_token
from app.repositories.retention import apply_retention_policy
from app.repositories.state import delete_state, get_state
from app.workers import (
    backfill_campaign_contacts,
    initialize_completed_service_baseline,
    poll_completed_services,
    process_all,
    process_due_messages,
    process_inbound_events,
    process_website_lead_messages,
    recover_stale_work,
    sync_google_reviews,
    sync_outlook_bounces,
)

logger = logging.getLogger(__name__)


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(prog="tampa-vip-automation")
    subcommands = command_parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("process-all")
    subcommands.add_parser("poll-completed-services")
    subcommands.add_parser("process-inbound-events")
    subcommands.add_parser("process-messages")
    subcommands.add_parser("process-website-leads")
    subcommands.add_parser("recover-stale-work")
    subcommands.add_parser("sync-google-reviews")
    subcommands.add_parser("sync-outlook-bounces")
    baseline = subcommands.add_parser("initialize-baseline")
    baseline.add_argument("--confirm", action="store_true")
    backfill = subcommands.add_parser("backfill-campaign-contacts")
    backfill.add_argument("--limit", type=int, default=50)
    migrate_token = subcommands.add_parser("migrate-legacy-google-token")
    migrate_token.add_argument("--delete-plaintext", action="store_true")
    retention = subcommands.add_parser("retention-cleanup")
    retention.add_argument(
        "--message-content-days",
        type=int,
        default=_environment_int("MESSAGE_CONTENT_RETENTION_DAYS", 90),
    )
    retention.add_argument(
        "--operational-event-days",
        type=int,
        default=_environment_int("OPERATIONAL_EVENT_RETENTION_DAYS", 365),
    )
    retention.add_argument(
        "--audit-days",
        type=int,
        default=_environment_int("AUDIT_RETENTION_DAYS", 730),
    )
    return command_parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = AppConfig.from_environment()
    configure_logging(config.LOG_LEVEL)
    init_engine(config.DATABASE_URL)
    try:
        if args.command == "process-all":
            result = process_all(config)
        elif args.command == "poll-completed-services":
            result = poll_completed_services(config)
        elif args.command == "process-inbound-events":
            result = process_inbound_events(config)
        elif args.command == "process-messages":
            result = process_due_messages(config)
        elif args.command == "process-website-leads":
            result = process_website_lead_messages(config)
        elif args.command == "recover-stale-work":
            result = recover_stale_work()
        elif args.command == "sync-google-reviews":
            result = sync_google_reviews(config)
        elif args.command == "sync-outlook-bounces":
            result = sync_outlook_bounces(config)
        elif args.command == "initialize-baseline":
            if not args.confirm:
                raise RuntimeError(
                    "Baseline initialization records current jobs without messaging them. "
                    "Run again with --confirm after reviewing this command."
                )
            result = initialize_completed_service_baseline(config)
        elif args.command == "backfill-campaign-contacts":
            result = backfill_campaign_contacts(config, limit=args.limit)
        elif args.command == "migrate-legacy-google-token":
            token = get_state("google_refresh_token")
            if not token:
                result = {"migrated": False, "reason": "legacy token not found"}
            else:
                save_refresh_token("google", token)
                if args.delete_plaintext:
                    delete_state("google_refresh_token")
                result = {"migrated": True, "plaintext_deleted": args.delete_plaintext}
        elif args.command == "retention-cleanup":
            _validate_retention_days(args)
            result = apply_retention_policy(
                message_content_days=args.message_content_days,
                operational_event_days=args.operational_event_days,
                audit_days=args.audit_days,
            )
        else:
            raise AssertionError("Unhandled command")
        print(json.dumps(result, default=str, sort_keys=True))
        return 0 if not isinstance(result, dict) or result.get("success", True) else 1
    except Exception:
        logger.exception("Command failed", extra={"event": "command_failed"})
        return 1
    finally:
        close_engine()


def _validate_retention_days(args: argparse.Namespace) -> None:
    values = {
        "message-content-days": args.message_content_days,
        "operational-event-days": args.operational_event_days,
        "audit-days": args.audit_days,
    }
    invalid = [name for name, value in values.items() if not 30 <= value <= 3650]
    if invalid:
        raise RuntimeError(
            "Retention values must be between 30 and 3650 days: " + ", ".join(invalid)
        )


def _environment_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


if __name__ == "__main__":
    sys.exit(main())
