from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    text: str
    subject: str | None = None
    html: str | None = None


def render_message(
    *,
    template_key: str,
    data: dict[str, Any],
    public_base_url: str,
    unsubscribe_token: str | None = None,
) -> RenderedMessage:
    customer_name = str(data.get("customer_name") or "there").strip()
    safe_name = escape(customer_name)
    review_token = str(data.get("review_token") or "")
    review_url = f"{public_base_url.rstrip('/')}/review/{review_token}" if review_token else ""

    if template_key == "completed_service_sms":
        return RenderedMessage(
            text=(
                f"Hi {customer_name}, your pool service has been completed. "
                "You can view your service information at "
                "https://tampavippoolservices.poolbrain.com"
            )
        )
    if template_key == "water_level_low_sms":
        return RenderedMessage(
            text=(
                f"Hi {customer_name}, your pool technician noticed that your pool water "
                "level is low. Please add water to the normal operating level when "
                "convenient. Tampa VIP Pool Services"
            )
        )
    if template_key == "initial_review_sms":
        return RenderedMessage(
            text=(
                f"Hi {customer_name}, thank you for choosing Tampa VIP Pool Services!\n\n"
                "We would appreciate your honest feedback on Google:\n\n"
                f"{review_url}\n\n"
                "If anything needs our attention, please reply to this message. "
                "Reply STOP to opt out."
            )
        )
    if template_key in {"next_day_review_email", "saturday_review_email"}:
        if not unsubscribe_token:
            raise ValueError("Email templates require an unsubscribe token")
        unsubscribe_url = f"{public_base_url.rstrip('/')}/unsubscribe/{unsubscribe_token}"
        is_final = template_key == "saturday_review_email"
        intro = (
            "This is our final follow-up regarding your recent pool service."
            if is_final
            else "We are following up about your recent pool service."
        )
        subject = (
            "Final follow-up: How was your pool service?"
            if is_final
            else "How was your recent pool service?"
        )
        text_body = (
            f"Hi {customer_name},\n\n{intro}\n\n"
            "If you have a moment, we would appreciate your honest feedback on Google:\n"
            f"{review_url}\n\n"
            "If anything needs our attention, please reply to this email.\n\n"
            f"Unsubscribe from review emails: {unsubscribe_url}\n\n"
            "Tampa VIP Pool Services"
        )
        html_body = f"""<!doctype html>
<html><body style="font-family:Arial,sans-serif;color:#172033;line-height:1.6">
<div style="max-width:600px;margin:0 auto;padding:24px">
  <p>Hi {safe_name},</p>
  <p>{escape(intro)}</p>
  <p>If you have a moment, we would appreciate your honest feedback on Google.</p>
  <p style="margin:28px 0">
    <a href="{escape(review_url)}"
       style="background:#1267d6;color:white;text-decoration:none;padding:12px 20px;
              border-radius:6px;display:inline-block">Leave an honest Google review</a>
  </p>
  <p>If anything needs our attention, please reply to this email.</p>
  <p>Thank you,<br>Tampa VIP Pool Services</p>
  <hr style="border:0;border-top:1px solid #ddd;margin-top:32px">
  <p style="font-size:12px;color:#667085">
    <a href="{escape(unsubscribe_url)}">Unsubscribe from review emails</a>
  </p>
</div></body></html>"""
        return RenderedMessage(text=text_body, subject=subject, html=html_body)
    if template_key == "admin_delivery_failure_sms":
        return RenderedMessage(text=str(data.get("message") or "An automation message failed."))
    if template_key == "admin_customer_reply_sms":
        return RenderedMessage(
            text=(
                f"Customer SMS reply from {data.get('customer_name') or 'Unknown customer'} "
                f"({data.get('customer_phone') or 'unknown number'}): "
                f"{data.get('message_body') or ''}"
            )
        )
    if template_key == "admin_website_lead_sms":
        mode = "Consultation" if data.get("mode") == "schedule" else "Callback"
        return RenderedMessage(
            text=(
                f"New website lead #{data.get('lead_id')}: {data.get('name')} | "
                f"{data.get('phone')} | {data.get('address')}, {data.get('city')} FL "
                f"{data.get('zip')} | {data.get('service')} | {mode}."
            )
        )
    if template_key == "admin_website_lead_email":
        mode = "Consultation request" if data.get("mode") == "schedule" else "Callback request"
        values = {
            "name": str(data.get("name") or "Not provided"),
            "phone": str(data.get("phone") or "Not provided"),
            "email": str(data.get("email") or "Not provided"),
            "address": str(data.get("address") or "Not provided"),
            "city": str(data.get("city") or "Not provided"),
            "state": str(data.get("state") or "FL"),
            "zip": str(data.get("zip") or "Not provided"),
            "service": str(data.get("service") or "Not provided"),
            "preferred_date": str(data.get("preferred_date") or "Not provided"),
            "preferred_time": str(data.get("preferred_time") or "Not provided"),
            "notes": str(data.get("notes") or "None"),
            "sms_consent": "Yes" if data.get("sms_consent") else "No",
        }
        subject = f"New Tampa VIP website lead: {values['name']} - {values['service']}"
        text_body = (
            f"New Tampa VIP website lead #{data.get('lead_id')}\n\n"
            f"Request: {mode}\nName: {values['name']}\nPhone: {values['phone']}\n"
            f"Email: {values['email']}\nAddress: {values['address']}, {values['city']} "
            f"{values['state']} {values['zip']}\n"
            f"Service: {values['service']}\n"
            f"Preferred date: {values['preferred_date']}\n"
            f"Preferred time: {values['preferred_time']}\n"
            f"Customer agreed to request-related texts: {values['sms_consent']}\n\n"
            f"Notes:\n{values['notes']}"
        )
        rows = "".join(
            "<tr><th style='text-align:left;padding:8px;border-bottom:1px solid #ddd'>"
            f"{escape(label)}</th>"
            f"<td style='padding:8px;border-bottom:1px solid #ddd'>{escape(value)}</td></tr>"
            for label, value in (
                ("Request", mode),
                ("Name", values["name"]),
                ("Phone", values["phone"]),
                ("Email", values["email"]),
                (
                    "Service address",
                    f"{values['address']}, {values['city']} {values['state']} {values['zip']}",
                ),
                ("Service", values["service"]),
                ("Preferred date", values["preferred_date"]),
                ("Preferred time", values["preferred_time"]),
                ("Text consent", values["sms_consent"]),
                ("Notes", values["notes"]),
            )
        )
        html_body = (
            "<!doctype html><html><body style='font-family:Arial,sans-serif;color:#172033'>"
            "<div style='max-width:680px;margin:0 auto;padding:24px'>"
            f"<h1>New website lead #{data.get('lead_id')}</h1>"
            f"<table style='border-collapse:collapse;width:100%'>{rows}</table>"
            "<p style='margin-top:24px'>Tampa VIP Pool Services website</p></div></body></html>"
        )
        return RenderedMessage(text=text_body, subject=subject, html=html_body)
    raise ValueError(f"Unknown message template: {template_key}")
