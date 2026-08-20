from __future__ import annotations

import os
from typing import Any

from flask import Blueprint, redirect, request, session, url_for
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

youtube_bp = Blueprint(
    "youtube",
    __name__,
    url_prefix="/youtube",
)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

CLIENT_SECRETS_FILE = "client_secret.json"


def wbsc_time_to_youtube(value: str) -> str:
    timezone = ZoneInfo("Europe/London")

    local_time = datetime.strptime(
        value,
        "%Y-%m-%d %H:%M:%S",
    ).replace(tzinfo=timezone)

    now = datetime.now(timezone)

    if local_time <= now:
        local_time = now + timedelta(minutes=5)

    return local_time.isoformat()


def build_broadcast_body(box_score: dict[str, Any]) -> dict[str, Any]:

    if "title" in box_score:  # Generic
        title = box_score["title"]
        start_time = datetime.strftime(
            datetime.now(ZoneInfo("Europe/London")) + timedelta(minutes=5),
            "%Y-%m-%d %H:%M:%S",
        )
        description = title

    else:
        away = box_score["away_name"]
        home = box_score["home_name"]
        start_time = box_score["start_time"]

        title = f"{away} @ {home} - {start_time}"

        description = f"{away} @ {home}\n{box_score['location']} - {start_time}"

    return {
        "snippet": {
            "title": title,
            "description": description,
            "scheduledStartTime": wbsc_time_to_youtube(start_time),
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
        "contentDetails": {
            "enableAutoStart": False,
            "enableAutoStop": False,
            "enableDvr": True,
            "recordFromStart": True,
            "monitorStream": {
                "enableMonitorStream": False,
            },
        },
    }

def get_redirect_uri():
    if request.host.startswith("localhost") or request.host.startswith("127.0.0.1"):
        return "http://localhost:8080/youtube/oauth/callback"

    return "https://scorebug.richmondbaseball.co.uk/youtube/oauth/callback"

@youtube_bp.get("/login")
def login():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        # autogenerate_code_verifier=True
    )

    flow.redirect_uri = get_redirect_uri()
    # flow.redirect_uri = url_for(
    #     "youtube.oauth_callback",
    #     _external=True,
    # )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    session["youtube_oauth_state"] = state
    # session["youtube_code_verifier"] = flow.code_verifier

    return redirect(authorization_url)


@youtube_bp.get("/logout")
def logout():
    session.pop("youtube_credentials", None)
    session.pop("youtube_oauth_state", None)
    session.pop("youtube_code_verifier", None)

    return redirect("/")


@youtube_bp.get("/oauth/callback")
def oauth_callback():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=session.get("youtube_oauth_state"),
        # code_verifier=session.get("youtube_code_verifier"),
    )

    flow.redirect_uri = get_redirect_uri()
    # flow.redirect_uri = url_for(
    #     "youtube.oauth_callback",
    #     _external=True,
    # )

    authorization_response = request.url.replace(
        "http://scorebug.richmondbaseball.co.uk",
        "https://scorebug.richmondbaseball.co.uk",
    )

    flow.fetch_token(
        authorization_response=authorization_response,
    )

    credentials = flow.credentials

    session["youtube_credentials"] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }

    return redirect("/")


@youtube_bp.get("/status")
def status():
    credentials_data = session.get("youtube_credentials")

    if not credentials_data:
        return {
            "connected": False,
        }

    credentials = Credentials(
        token=credentials_data["token"],
        refresh_token=credentials_data["refresh_token"],
        token_uri=credentials_data["token_uri"],
        client_id=credentials_data["client_id"],
        client_secret=credentials_data["client_secret"],
        scopes=credentials_data["scopes"],
    )

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
    )

    response = (
        youtube.channels()
        .list(
            part="snippet",
            mine=True,
        )
        .execute()
    )

    channels = response.get("items", [])

    if not channels:
        return {
            "connected": True,
            "channel": None,
        }

    channel = channels[0]

    return {
        "connected": True,
        "channel": channel["snippet"]["title"],
        "channel_id": channel["id"],
    }


@youtube_bp.get("/streams")
def streams():
    credentials_data = session.get("youtube_credentials")

    if not credentials_data:
        return {
            "connected": False,
            "streams": [],
        }, 401

    credentials = Credentials(
        token=credentials_data["token"],
        refresh_token=credentials_data["refresh_token"],
        token_uri=credentials_data["token_uri"],
        client_id=credentials_data["client_id"],
        client_secret=credentials_data["client_secret"],
        scopes=credentials_data["scopes"],
    )

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
    )

    response = (
        youtube.liveStreams()
        .list(
            part="snippet,status,cdn",
            mine=True,
            maxResults=50,
        )
        .execute()
    )

    streams = []

    for item in response.get("items", []):
        health = item.get("status", {}).get("healthStatus", {})
        streams.append(
            {
                "id": item["id"],
                "title": item.get("snippet", {}).get("title", ""),
                "stream_status": item.get("status", {}).get("streamStatus", ""),
                "health_status": health.get("status", ""),
                "configuration_issues": health.get("configurationIssues", []),
                "ingestion_type": item.get("cdn", {}).get("ingestionType", ""),
                "ingestion_address": item.get("cdn", {})
                .get("ingestionInfo", {})
                .get("ingestionAddress", ""),
            }
        )

    return {
        "connected": True,
        "streams": streams,
    }


@youtube_bp.get("/broadcasts")
def broadcasts():
    credentials_data = session.get("youtube_credentials")

    if not credentials_data:
        return {
            "connected": False,
            "broadcasts": [],
        }, 401

    credentials = Credentials(
        token=credentials_data["token"],
        refresh_token=credentials_data["refresh_token"],
        token_uri=credentials_data["token_uri"],
        client_id=credentials_data["client_id"],
        client_secret=credentials_data["client_secret"],
        scopes=credentials_data["scopes"],
    )

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
    )

    response = (
        youtube.liveBroadcasts()
        .list(
            part="snippet,status,contentDetails",
            # broadcastStatus="all",
            # broadcastStatus="upcoming",
            mine=True,
            maxResults=50,
        )
        .execute()
    )

    broadcasts = []

    for item in response.get("items", []):
        broadcasts.append(
            {
                "id": item["id"],
                "title": item.get("snippet", {}).get("title", ""),
                "scheduled_start": item.get("snippet", {}).get(
                    "scheduledStartTime", ""
                ),
                "life_cycle_status": item.get("status", {}).get("lifeCycleStatus", ""),
                "privacy_status": item.get("status", {}).get("privacyStatus", ""),
                "bound_stream_id": item.get("contentDetails", {}).get(
                    "boundStreamId", ""
                ),
            }
        )

    return {
        "connected": True,
        "broadcasts": broadcasts,
    }


@youtube_bp.post("/broadcasts")
def create_broadcast():
    credentials_data = session.get("youtube_credentials")

    if not credentials_data:
        return {"ok": False, "error": "Not connected to YouTube"}, 401

    box_score = request.get_json()

    if not box_score:
        return {"ok": False, "error": "No game information supplied"}, 400

    credentials = Credentials(
        token=credentials_data["token"],
        refresh_token=credentials_data["refresh_token"],
        token_uri=credentials_data["token_uri"],
        client_id=credentials_data["client_id"],
        client_secret=credentials_data["client_secret"],
        scopes=credentials_data["scopes"],
    )

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
    )

    broadcast = (
        youtube.liveBroadcasts()
        .insert(
            part="snippet,status,contentDetails",
            body=build_broadcast_body(box_score),
        )
        .execute()
    )

    return {
        "ok": True,
        "broadcast": broadcast,
    }


@youtube_bp.post("/broadcasts/<broadcast_id>/bind")
def bind_broadcast(broadcast_id):
    credentials_data = session.get("youtube_credentials")

    if not credentials_data:
        return {"ok": False, "error": "Not connected to YouTube"}, 401

    data = request.get_json() or {}
    stream_id = data.get("stream_id")

    if not stream_id:
        return {"ok": False, "error": "No stream ID supplied"}, 400

    credentials = Credentials(
        token=credentials_data["token"],
        refresh_token=credentials_data["refresh_token"],
        token_uri=credentials_data["token_uri"],
        client_id=credentials_data["client_id"],
        client_secret=credentials_data["client_secret"],
        scopes=credentials_data["scopes"],
    )

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
    )

    broadcast = (
        youtube.liveBroadcasts()
        .bind(
            id=broadcast_id,
            streamId=stream_id,
            part="id,snippet,status,contentDetails",
        )
        .execute()
    )

    return {
        "ok": True,
        "broadcast": broadcast,
    }


@youtube_bp.get("/broadcasts/<broadcast_id>")
def get_broadcast(broadcast_id):
    credentials_data = session.get("youtube_credentials")

    if not credentials_data:
        return {"ok": False, "error": "Not connected to YouTube"}, 401

    credentials = Credentials(
        token=credentials_data["token"],
        refresh_token=credentials_data["refresh_token"],
        token_uri=credentials_data["token_uri"],
        client_id=credentials_data["client_id"],
        client_secret=credentials_data["client_secret"],
        scopes=credentials_data["scopes"],
    )

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
    )

    response = (
        youtube.liveBroadcasts()
        .list(
            part="id,snippet,status,contentDetails",
            id=broadcast_id,
        )
        .execute()
    )

    items = response.get("items", [])

    if not items:
        return {"ok": False, "error": "Broadcast not found"}, 404

    broadcast = items[0]

    return {
        "ok": True,
        "broadcast": {
            "id": broadcast["id"],
            "title": broadcast["snippet"]["title"],
            "life_cycle_status": broadcast["status"]["lifeCycleStatus"],
            "privacy_status": broadcast["status"]["privacyStatus"],
            "bound_stream_id": broadcast.get("contentDetails", {}).get("boundStreamId"),
        },
    }


@youtube_bp.post("/broadcasts/<broadcast_id>/live")
def go_live(broadcast_id):
    credentials_data = session.get("youtube_credentials")

    if not credentials_data:
        return {"ok": False, "error": "Not connected to YouTube"}, 401

    credentials = Credentials(
        token=credentials_data["token"],
        refresh_token=credentials_data["refresh_token"],
        token_uri=credentials_data["token_uri"],
        client_id=credentials_data["client_id"],
        client_secret=credentials_data["client_secret"],
        scopes=credentials_data["scopes"],
    )

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
    )

    broadcast = (
        youtube.liveBroadcasts()
        .transition(
            broadcastStatus="live",
            id=broadcast_id,
            part="id,snippet,status,contentDetails",
        )
        .execute()
    )

    return {
        "ok": True,
        "broadcast": broadcast,
    }


@youtube_bp.post("/broadcasts/<broadcast_id>/end")
def end_broadcast(broadcast_id):
    credentials_data = session.get("youtube_credentials")

    if not credentials_data:
        return {"ok": False, "error": "Not connected to YouTube"}, 401

    credentials = Credentials(
        token=credentials_data["token"],
        refresh_token=credentials_data["refresh_token"],
        token_uri=credentials_data["token_uri"],
        client_id=credentials_data["client_id"],
        client_secret=credentials_data["client_secret"],
        scopes=credentials_data["scopes"],
    )

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
    )

    broadcast = (
        youtube.liveBroadcasts()
        .transition(
            broadcastStatus="complete",
            id=broadcast_id,
            part="id,snippet,status,contentDetails",
        )
        .execute()
    )

    return {
        "ok": True,
        "broadcast": broadcast,
    }
