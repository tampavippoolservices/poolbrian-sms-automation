import os
from urllib.parse import urlencode

from twilio.rest import Client

from communication_preferences import (
    communication_is_suppressed
)

def send_sms(
    to_number,
    message_body,
    message_type="general",
    reference_id=None
):
    if communication_is_suppressed(
        "sms",
        to_number
    ):
        raise ValueError(
            "SMS delivery suppressed for recipient"
        ) 
    
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    twilio_number = os.environ.get("TWILIO_PHONE_NUMBER")
    status_callback = os.environ.get(
        "TWILIO_STATUS_CALLBACK_URL"
    )

    client = Client(account_sid, auth_token)

    callback_url = status_callback

    if status_callback:
        callback_params = {
            "message_type": message_type
        }

        if reference_id is not None:
            callback_params["reference_id"] = str(
                reference_id
            )

        separator = "&" if "?" in status_callback else "?"
        callback_url = (
            status_callback
            + separator
            + urlencode(callback_params)
        )

    message = client.messages.create(
        body=message_body,
        from_=twilio_number,
        to=to_number,
        status_callback=callback_url
    )

    return message.sid
