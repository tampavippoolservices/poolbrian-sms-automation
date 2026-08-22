from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.http import resilient_session


class PoolBrainError(RuntimeError):
    pass


class PoolBrainClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("POOLBRAIN_API_BASE_URL", "https://prodapi.poolbrain.com").rstrip(
            "/"
        )
        self.api_key = os.getenv("POOLBRAIN_API_KEY", "")
        self.session = resilient_session()

    def get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise PoolBrainError("POOLBRAIN_API_KEY is not configured")
        if not path.startswith("/"):
            raise PoolBrainError("PoolBrain path must begin with /")
        response = self.session.get(
            self.base_url + path,
            params=params,
            headers={"ACCESS-KEY": self.api_key, "Accept": "application/json"},
            timeout=(3.05, 10),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise PoolBrainError("PoolBrain returned an unexpected response")
        return payload

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
        payload = self.get("/v2/customer_detail", {"contactPhoneNumber": digits})
        data = payload.get("data", [])
        return data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None

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
