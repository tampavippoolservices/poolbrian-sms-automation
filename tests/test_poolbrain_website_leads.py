import pytest

from app.services.poolbrain import (
    PoolBrainClient,
    PoolBrainCreatePending,
    _create_customer_payload,
    _customer_matches,
)


def lead() -> dict[str, object]:
    return {
        "mode": "schedule",
        "name": "Taylor Smith",
        "phone": "+18135550199",
        "email": "taylor@example.com",
        "address": "123 Bayshore Blvd",
        "city": "Tampa",
        "state": "FL",
        "zip": "33609",
        "service": "Weekly pool service",
        "preferred_date": "2026-09-05",
        "preferred_time": "Morning",
        "notes": "Please call first",
    }


def test_create_customer_payload_creates_only_contact_and_property_fields() -> None:
    payload = _create_customer_payload(lead())

    assert payload == {
        "firstName": "Taylor",
        "lastName": "Smith",
        "displayName": "Taylor Smith",
        "address": "123 Bayshore Blvd",
        "city": "Tampa",
        "state": "Florida",
        "zipcode": "33609",
        "contactNumber": "8135550199",
        "email": "taylor@example.com",
    }
    assert not {"serviceLevelId", "poolTypeId", "waterBodyName"} & payload.keys()


def test_customer_address_match_requires_street_city_and_zip() -> None:
    customer = {
        "Addresses": {
            "1": {
                "PrimaryAddress": "123 Bayshore Blvd.",
                "PrimaryCity": "Tampa",
                "PrimaryZip": "33609-1234",
            }
        }
    }

    assert _customer_matches(customer, lead(), "address")
    different = lead() | {"zip": "33611"}
    assert not _customer_matches(customer, different, "address")


def test_sync_existing_customer_adds_note_without_creating_customer(monkeypatch) -> None:
    client = PoolBrainClient()
    customer = {
        "RecordID": 321,
        "customerStatus": "Lead",
        "Phone": "+18135550199",
    }
    monkeypatch.setattr(client, "_find_matching_customer", lambda _lead: (customer, "phone"))
    posted: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        client,
        "get",
        lambda path, params=None: (
            {"data": []} if path == "/v2/customer_notes_detail" else {"data": [customer]}
        ),
    )

    def fake_post(path: str, body: dict[str, object]) -> dict[str, object]:
        posted.append((path, body))
        return {}

    monkeypatch.setattr(client, "post", fake_post)

    result = client.sync_website_lead("site-lead-42", lead())

    assert result["action"] == "matched"
    assert result["poolbrain_customer_id"] == 321
    assert [path for path, _body in posted] == ["/v2/create_customer_notes"]
    assert "[Website lead site-lead-42]" in str(posted[0][1]["notes"])


def test_previous_uncertain_create_is_not_repeated(monkeypatch) -> None:
    client = PoolBrainClient()
    monkeypatch.setattr(client, "_find_matching_customer", lambda _lead: (None, None))

    def unexpected_post(_path: str, _body: dict[str, object]) -> dict[str, object]:
        raise AssertionError("PoolBrain create must not be repeated")

    monkeypatch.setattr(client, "post", unexpected_post)

    with pytest.raises(PoolBrainCreatePending, match="already attempted"):
        client.sync_website_lead(
            "site-lead-42",
            lead(),
            creation_previously_attempted=True,
        )
