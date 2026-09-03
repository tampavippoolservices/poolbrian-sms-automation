from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from requests import HTTPError, RequestException

from app.services.http import resilient_session


class PoolBrainError(RuntimeError):
    pass


class PoolBrainCreatePending(PoolBrainError):
    pass


class PoolBrainClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("POOLBRAIN_API_BASE_URL", "https://prodapi.poolbrain.com").rstrip(
            "/"
        )
        self.api_key = os.getenv("POOLBRAIN_API_KEY", "")
        self.company_id = os.getenv("POOLBRAIN_COMPANY_ID", "").strip()
        self.session = resilient_session()

    def _headers(self) -> dict[str, str]:
        headers = {"ACCESS-KEY": self.api_key, "Accept": "application/json"}
        if self.company_id:
            headers["COMPANY-ID"] = self.company_id
        return headers

    def get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise PoolBrainError("POOLBRAIN_API_KEY is not configured")
        if not path.startswith("/"):
            raise PoolBrainError("PoolBrain path must begin with /")
        response = self.session.get(
            self.base_url + path,
            params=params,
            headers=self._headers(),
            timeout=(3.05, 10),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise PoolBrainError("PoolBrain returned an unexpected response")
        return payload

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise PoolBrainError("POOLBRAIN_API_KEY is not configured")
        if not path.startswith("/"):
            raise PoolBrainError("PoolBrain path must begin with /")
        response = self.session.post(
            self.base_url + path,
            json=body,
            headers={**self._headers(), "Content-Type": "application/json"},
            timeout=(3.05, 15),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise PoolBrainError("PoolBrain returned an unexpected response")
        return payload

    def customer_details(self, params: dict[str, str]) -> list[dict[str, Any]]:
        payload = self.get("/v2/customer_detail", {**params, "limit": "100"})
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise PoolBrainError("Customer details response is not a list")
        return [record for record in data if isinstance(record, dict)]

    def customer(self, customer_id: int) -> dict[str, Any] | None:
        payload = self.get("/v2/customer_detail", {"customerId": str(customer_id)})
        data = payload.get("data", [])
        if isinstance(data, list):
            return data[0] if data and isinstance(data[0], dict) else None
        if isinstance(data, dict):
            if "CustomerName" in data:
                return data
            for value in data.values():
                if isinstance(value, dict) and "CustomerName" in value:
                    return value
        return None

    def customer_by_phone(self, digits: str) -> dict[str, Any] | None:
        data = self.customer_details({"contactPhoneNumber": digits})
        return data[0] if data else None

    def sync_website_lead(
        self,
        event_id: str,
        lead: dict[str, Any],
        *,
        creation_previously_attempted: bool = False,
    ) -> dict[str, Any]:
        existing, matched_by = self._find_matching_customer(lead)
        action = "matched"
        if existing is None:
            if creation_previously_attempted:
                raise PoolBrainCreatePending(
                    "A PoolBrain create was already attempted; waiting for the customer to appear"
                )
            try:
                self.post("/v2/create_customer", _create_customer_payload(lead))
            except HTTPError as exc:
                if exc.response is not None and exc.response.status_code < 500:
                    raise
                raise PoolBrainCreatePending(
                    "PoolBrain customer creation had an uncertain server response"
                ) from exc
            except RequestException as exc:
                raise PoolBrainCreatePending(
                    "PoolBrain customer creation had an uncertain network response"
                ) from exc
            existing, matched_by = self._find_matching_customer(lead)
            action = "created"
        if existing is None:
            raise PoolBrainCreatePending(
                "PoolBrain accepted the customer request but the new customer is not readable yet"
            )

        customer_id = _positive_int(existing.get("RecordID"))
        if customer_id is None:
            raise PoolBrainError("PoolBrain customer response did not include RecordID")
        note_status = self._ensure_website_lead_note(customer_id, event_id, lead)
        return {
            "action": action,
            "matched_by": matched_by,
            "poolbrain_customer_id": customer_id,
            "poolbrain_customer_status": str(existing.get("customerStatus") or "Unknown"),
            "note_status": note_status,
        }

    def _find_matching_customer(
        self, lead: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        searches = (
            (
                "phone",
                "contactPhoneNumber",
                re.sub(r"\D", "", str(lead["phone"]))[-10:],
            ),
            ("email", "contactEmailAddress", str(lead["email"])),
            ("address", "primaryAddress", str(lead["address"])),
        )
        for match_type, parameter, value in searches:
            for customer in self.customer_details({parameter: value}):
                if _customer_matches(customer, lead, match_type):
                    return customer, match_type
        return None, None

    def _ensure_website_lead_note(
        self, customer_id: int, event_id: str, lead: dict[str, Any]
    ) -> str:
        marker = f"[Website lead {event_id}]"
        payload = self.get("/v2/customer_notes_detail", {"customerId": str(customer_id)})
        notes = payload.get("data", [])
        if isinstance(notes, list) and any(
            marker in str(note.get("Note") or "") for note in notes if isinstance(note, dict)
        ):
            return "already_present"
        self.post(
            "/v2/create_customer_notes",
            {"customerId": customer_id, "notes": _website_lead_note(marker, lead)},
        )
        return "created"

    def recent_completed_jobs(
        self,
        *,
        timezone_name: str,
        lookback_days: int,
    ) -> list[dict[str, Any]]:
        now = datetime.now(ZoneInfo(timezone_name))
        from_date = (now - timedelta(days=lookback_days)).date().isoformat()
        to_date = now.date().isoformat()
        payload = self.get(
            "/v2/route_stops_job_list",
            {"fromDate": from_date, "toDate": to_date},
        )
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise PoolBrainError("Completed jobs response is not a list")
        return [
            job for job in data if isinstance(job, dict) and job.get("JobStatus") == "Completed"
        ]


def _create_customer_payload(lead: dict[str, Any]) -> dict[str, Any]:
    name = str(lead["name"]).strip()
    parts = name.split()
    first_name = parts[0]
    last_name = " ".join(parts[1:]) if len(parts) > 1 else "Not provided"
    return {
        "firstName": first_name,
        "lastName": last_name,
        "displayName": name,
        "address": str(lead["address"]).strip(),
        "city": str(lead["city"]).strip(),
        "state": "Florida",
        "zipcode": str(lead["zip"]).strip(),
        "contactNumber": re.sub(r"\D", "", str(lead["phone"]))[-10:],
        "email": str(lead["email"]).strip(),
    }


def _customer_matches(customer: dict[str, Any], lead: dict[str, Any], match_type: str) -> bool:
    if match_type == "phone":
        expected = re.sub(r"\D", "", str(lead["phone"]))[-10:]
        return any(
            re.sub(r"\D", "", str(customer.get(key) or ""))[-10:] == expected
            for key in ("Phone", "PhoneNumber", "ContactPhoneNumber", "MobilePhone")
        )
    if match_type == "email":
        expected = str(lead["email"]).strip().casefold()
        return any(
            str(customer.get(key) or "").strip().casefold() == expected
            for key in ("Email", "EmailAddress", "ContactEmail", "PrimaryEmail")
        )
    expected_address = _normalized_address(str(lead["address"]))
    expected_city = _normalized_address(str(lead["city"]))
    expected_zip = re.sub(r"\D", "", str(lead["zip"]))[:5]
    addresses = customer.get("Addresses")
    if not isinstance(addresses, dict):
        return False
    for address in addresses.values():
        if not isinstance(address, dict):
            continue
        street = address.get("PrimaryAddress") or address.get("address")
        city = address.get("PrimaryCity") or address.get("city")
        zipcode = address.get("PrimaryZip") or address.get("zipcode")
        if (
            _normalized_address(str(street or "")) == expected_address
            and _normalized_address(str(city or "")) == expected_city
            and re.sub(r"\D", "", str(zipcode or ""))[:5] == expected_zip
        ):
            return True
    return False


def _normalized_address(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _website_lead_note(marker: str, lead: dict[str, Any]) -> str:
    request_type = "Consultation" if lead.get("mode") == "schedule" else "Callback"
    lines = [
        marker,
        f"Source: Tampa VIP website - {request_type}",
        f"Requested service: {lead.get('service')}",
    ]
    if lead.get("preferred_date"):
        lines.append(f"Preferred date: {lead['preferred_date']}")
    if lead.get("preferred_time"):
        lines.append(f"Preferred time: {lead['preferred_time']}")
    if lead.get("notes"):
        lines.append(f"Customer message: {lead['notes']}")
    lines.append("Lead only - no route, job, invoice, or billing was created automatically.")
    return "\n".join(lines)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None
