from datetime import datetime, timedelta
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


POOLBRAIN_BASE_URL = "https://prodapi.poolbrain.com"


def poolbrain_get(endpoint, params=None):
    api_key = os.environ.get(
        "POOLBRAIN_API_KEY"
    )

    if not api_key:
        raise Exception(
            "POOLBRAIN_API_KEY is missing"
        )

    url = POOLBRAIN_BASE_URL + endpoint

    if params:
        url += "?" + urlencode(params)

    request_object = Request(
        url,
        headers={
            "ACCESS-KEY": api_key,
            "Accept": "application/json"
        }
    )

    with urlopen(
        request_object,
        timeout=10
    ) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def get_customer(customer_id):
    result = poolbrain_get(
        "/v2/customer_detail",
        {
            "customerId": str(customer_id)
        }
    )

    customers = result.get("data", [])

    if not customers:
        return None

    if isinstance(customers, list):
        return customers[0]

    if isinstance(customers, dict):
        if "CustomerName" in customers:
            return customers

        for value in customers.values():
            if (
                isinstance(value, dict)
                and "CustomerName" in value
            ):
                return value

    return None


def get_recent_completed_jobs():
    now = datetime.now(
        ZoneInfo("America/New_York")
    )

    from_date = (
        now - timedelta(days=1)
    ).strftime("%Y-%m-%d")

    to_date = now.strftime("%Y-%m-%d")

    result = poolbrain_get(
        "/v2/route_stops_job_list",
        {
            "fromDate": from_date,
            "toDate": to_date
        }
    )

    jobs = result.get("data", [])

    return [
        job
        for job in jobs
        if job.get("JobStatus") == "Completed"
    ]
