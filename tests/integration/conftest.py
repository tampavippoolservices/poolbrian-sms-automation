from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db import close_engine, init_engine, transaction


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    os.environ["DATABASE_URL"] = database_url
    close_engine()
    command.upgrade(Config("alembic.ini"), "head")
    init_engine(database_url)
    yield
    close_engine()


@pytest.fixture(autouse=True)
def clean_database(migrated_database) -> None:
    del migrated_database
    with transaction() as connection:
        connection.execute(
            text(
                """
                TRUNCATE TABLE
                    audit_events,
                    communication_preference_events,
                    communication_preferences,
                    google_reviews,
                    inbound_messages,
                    message_attempts,
                    provider_message_events,
                    unsubscribe_tokens,
                    message_jobs,
                    review_campaigns,
                    inbound_events,
                    processed_alerts,
                    processed_jobs,
                    oauth_credentials,
                    oauth_states,
                    worker_heartbeats,
                    automation_state
                RESTART IDENTITY CASCADE
                """
            )
        )
