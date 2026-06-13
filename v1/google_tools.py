"""
Gmail + Google Calendar tools via Google's official API. READ-ONLY to start
(list/summarize), which is all the "morning brief" needs — sending/deleting can
be added later with broader scopes.

One-time setup:
  1. Google Cloud Console -> create a project -> enable the Gmail API and the
     Google Calendar API.
  2. APIs & Services -> Credentials -> Create OAuth client ID -> type "Desktop
     app" -> download the JSON, save it here as credentials.json (or point
     GOOGLE_CLIENT_SECRET at it).
  3. First tool call opens a browser for consent; the token is cached in
     token.json so you only consent once.
"""
import datetime as dt
import os
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]
_CRED = os.getenv("GOOGLE_CLIENT_SECRET", "credentials.json")
_TOKEN = os.getenv("GOOGLE_TOKEN", "token.json")

_services = {}


def _creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if Path(_TOKEN).exists():
        creds = Credentials.from_authorized_user_file(_TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path(_CRED).exists():
                raise RuntimeError(
                    f"Missing {_CRED}. Download an OAuth 'Desktop app' client JSON "
                    "from Google Cloud Console (Gmail + Calendar APIs enabled).")
            creds = InstalledAppFlow.from_client_secrets_file(
                _CRED, SCOPES).run_local_server(port=0)
        Path(_TOKEN).write_text(creds.to_json())
    return creds


def _service(name, version):
    if name not in _services:
        from googleapiclient.discovery import build
        _services[name] = build(name, version, credentials=_creds(),
                                 cache_discovery=False)
    return _services[name]


def gmail_recent(args, *_):
    """List recent Gmail messages matching a query (sender/subject/date/snippet)."""
    query = args.get("query", "newer_than:1d")
    maxr = int(args.get("max_results", 10))
    try:
        svc = _service("gmail", "v1")
        ids = svc.users().messages().list(
            userId="me", q=query, maxResults=maxr).execute().get("messages", [])
        out = []
        for m in ids:
            full = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]).execute()
            h = {x["name"]: x["value"]
                 for x in full.get("payload", {}).get("headers", [])}
            out.append(f"- From: {h.get('From', '?')}\n"
                       f"  Subject: {h.get('Subject', '(none)')}\n"
                       f"  Date: {h.get('Date', '')}\n"
                       f"  Snippet: {full.get('snippet', '')[:200]}")
        return "\n".join(out) or "[no messages match]"
    except Exception as e:
        return f"[gmail error: {e}]"


def calendar_events(args, *_):
    """List Google Calendar events for the next `days` (default today only)."""
    try:
        svc = _service("calendar", "v3")
        now = dt.datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + dt.timedelta(days=int(args.get("days", 1)))
        items = svc.events().list(
            calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=20).execute().get("items", [])
        out = []
        for ev in items:
            when = ev["start"].get("dateTime", ev["start"].get("date"))
            out.append(f"- {when}  {ev.get('summary', '(no title)')}"
                       + (f"  @ {ev['location']}" if ev.get("location") else ""))
        return "\n".join(out) or "[no events in range]"
    except Exception as e:
        return f"[calendar error: {e}]"
