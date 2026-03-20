"""
xml_builder.py — Auto-Generated Stream XML for Vobiz Applications
==================================================================
Generates Vobiz-compatible XML based on customer streaming config,
uploads it to AWS S3, and returns a public URL to use as answer_url.

Flow:
  Customer fills form (stream URL + toggles)
      → POST /api/applications/stream
      → XML generated from template
      → Uploaded to S3 as vobiz-xml/{app_id}.xml
      → Public S3 URL returned (set as Vobiz application answer_url)
      → Call comes in → Vobiz fetches XML from S3 → stream connects
"""

import os
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AWS_ACCESS_KEY_ID     = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION            = os.getenv("AWS_REGION", "ap-south-1")
S3_BUCKET_NAME        = os.getenv("S3_BUCKET_NAME")

# When S3 is not configured, the API falls back to serving XML locally.
# The local answer_url will look like: http://localhost:5000/api/applications/{app_id}/xml
# This is useful for local development and testing without AWS credentials.
LOCAL_MODE = not S3_BUCKET_NAME

logger = logging.getLogger("xml_builder")

# Base URL used to build local XML URLs (overridden at runtime by server.py)
# Set via LOCAL_BASE_URL env var, or defaults to localhost:5000
LOCAL_BASE_URL = os.getenv("LOCAL_BASE_URL", "http://localhost:5000")

# ---------------------------------------------------------------------------
# In-memory application registry
# Tracks created app configs for GET / DELETE endpoints.
# Resets on server restart — production should use a database.
# ---------------------------------------------------------------------------
_app_registry: Dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class StreamAppRequest(BaseModel):
    """Input config for creating a new stream application."""

    stream_url: str
    """The customer's WebSocket endpoint that will receive the audio stream.
    Must be a valid ws:// or wss:// URL."""

    recording: bool = False
    """Whether to record the call."""

    transcription: bool = False
    """Whether to transcribe the call. Only meaningful when recording=True."""

    content_type: str = "audio/x-mulaw;rate=8000"
    """Audio content type sent to the stream. Defaults to mulaw 8kHz."""

    keep_call_alive: bool = True
    """Keep the call alive while streaming even if the stream disconnects."""

    status_callback_url: Optional[str] = None
    """Optional URL for Vobiz to POST stream lifecycle events (start/stop)."""

    status_callback_method: str = "POST"
    """HTTP method for the status callback. Defaults to POST."""

    @field_validator("stream_url")
    @classmethod
    def validate_stream_url(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("wss://") or v.startswith("ws://")):
            raise ValueError(
                "stream_url must be a valid WebSocket URL starting with wss:// or ws://"
            )
        return v

    @field_validator("status_callback_url")
    @classmethod
    def validate_callback_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not (v.startswith("https://") or v.startswith("http://")):
            raise ValueError(
                "status_callback_url must be a valid HTTP/HTTPS URL"
            )
        return v


class StreamAppResponse(BaseModel):
    """Response returned when a stream application is created."""

    app_id: str
    """Unique identifier for this application config."""

    answer_url: str
    """Public S3 URL to use as the Vobiz application's answer_url."""

    xml_preview: str
    """The generated XML content (for inspection / debugging)."""

    stream_url: str
    recording: bool
    transcription: bool
    created_at: str


class AppConfigResponse(BaseModel):
    """Response for retrieving an existing application config."""

    app_id: str
    answer_url: str
    xml_preview: str
    stream_url: str
    recording: bool
    transcription: bool
    created_at: str


# ---------------------------------------------------------------------------
# XML Generation
# ---------------------------------------------------------------------------

def generate_stream_xml(config: StreamAppRequest) -> str:
    """
    Build a Vobiz-compatible XML response string from a StreamAppRequest.

    Examples
    --------
    Streaming only:
        <Response>
            <Stream bidirectional="true" keepCallAlive="true"
                    contentType="audio/x-mulaw;rate=8000">
                wss://customer.com/audio
            </Stream>
        </Response>

    Streaming + Recording:
        <Response>
            <Record action="" method="POST" />
            <Stream ...>wss://...</Stream>
        </Response>

    Streaming + Recording + Transcription:
        <Response>
            <Record action="" method="POST" transcriptionEnabled="true" />
            <Stream ...>wss://...</Stream>
        </Response>
    """
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<Response>"]

    # --- <Record> element (optional) ---
    if config.recording:
        transcription_attr = ""
        if config.transcription:
            transcription_attr = ' transcriptionEnabled="true"'
        lines.append(
            f'    <Record action="" method="POST"{transcription_attr} />'
        )

    # --- <Stream> element ---
    keep_alive_attr = 'true' if config.keep_call_alive else 'false'

    stream_attrs = (
        f'bidirectional="true" '
        f'keepCallAlive="{keep_alive_attr}" '
        f'contentType="{config.content_type}"'
    )

    if config.status_callback_url:
        stream_attrs += (
            f' statusCallbackUrl="{config.status_callback_url}"'
            f' statusCallbackMethod="{config.status_callback_method}"'
        )

    lines.append(f"    <Stream {stream_attrs}>")
    lines.append(f"        {config.stream_url}")
    lines.append("    </Stream>")

    lines.append("</Response>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# S3 Upload
# ---------------------------------------------------------------------------

def _get_s3_client():
    """Create and return a boto3 S3 client using env credentials."""
    return boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


def upload_to_s3(xml_content: str, app_id: str) -> str:
    """
    Upload XML content to S3 at vobiz-xml/{app_id}.xml.
    Returns the public HTTPS URL of the uploaded object.

    LOCAL MODE (no S3_BUCKET_NAME set):
    Skips S3 entirely and returns a local URL pointing to
    GET /api/applications/{app_id}/xml on this server.
    """
    # ── Local mode fallback ──────────────────────────────────────────────────
    if LOCAL_MODE:
        local_url = f"{LOCAL_BASE_URL}/api/applications/{app_id}/xml"
        logger.info(f"[LOCAL MODE] XML stored in memory. answer_url = {local_url}")
        return local_url

    # ── S3 upload ────────────────────────────────────────────────────────────
    s3_key = f"vobiz-xml/{app_id}.xml"

    try:
        s3 = _get_s3_client()
        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
            Body=xml_content.encode("utf-8"),
            ContentType="application/xml",
        )
        logger.info(f"Uploaded XML to s3://{S3_BUCKET_NAME}/{s3_key}")

    except NoCredentialsError:
        logger.error("AWS credentials not found.")
        raise HTTPException(
            status_code=500,
            detail=(
                "AWS credentials are not configured. "
                "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in your .env file."
            ),
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg  = e.response["Error"]["Message"]
        logger.error(f"S3 ClientError [{error_code}]: {error_msg}")
        raise HTTPException(
            status_code=500,
            detail=f"S3 upload failed: {error_code} — {error_msg}",
        )
    except Exception as e:
        logger.error(f"Unexpected S3 error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error uploading to S3: {str(e)}",
        )

    public_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"
    return public_url


def delete_from_s3(app_id: str) -> None:
    """
    Delete the XML object for a given app_id from S3.

    Raises HTTPException on errors.
    """
    if not S3_BUCKET_NAME:
        raise HTTPException(
            status_code=500,
            detail="S3_BUCKET_NAME is not configured.",
        )

    s3_key = f"vobiz-xml/{app_id}.xml"

    try:
        s3 = _get_s3_client()
        s3.delete_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
        logger.info(f"Deleted s3://{S3_BUCKET_NAME}/{s3_key}")

    except NoCredentialsError:
        raise HTTPException(
            status_code=500,
            detail="AWS credentials are not configured.",
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg  = e.response["Error"]["Message"]
        logger.error(f"S3 delete error [{error_code}]: {error_msg}")
        raise HTTPException(
            status_code=500,
            detail=f"S3 delete failed: {error_code} — {error_msg}",
        )


def upload_frontend_to_s3(html_path: str = "index.html") -> str:
    """
    Upload the static frontend (index.html) to the root of the S3 bucket.

    This makes the frontend accessible at:
      https://{bucket}.s3.{region}.amazonaws.com/index.html

    Usage:
        from xml_builder import upload_frontend_to_s3
        url = upload_frontend_to_s3()
        print(f"Frontend live at: {url}")

    Or run directly:
        python -c "from xml_builder import upload_frontend_to_s3; print(upload_frontend_to_s3())"
    """
    import os as _os

    if not S3_BUCKET_NAME:
        raise RuntimeError("S3_BUCKET_NAME is not configured in your .env file.")

    if not _os.path.exists(html_path):
        raise FileNotFoundError(f"Could not find {html_path}. Run this from the project root directory.")

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    try:
        s3 = _get_s3_client()
        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key="index.html",
            Body=html_content.encode("utf-8"),
            ContentType="text/html; charset=utf-8",
        )
        public_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/index.html"
        logger.info(f"Frontend uploaded → {public_url}")
        return public_url

    except NoCredentialsError:
        raise RuntimeError(
            "AWS credentials are not configured. "
            "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in your .env file."
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg  = e.response["Error"]["Message"]
        raise RuntimeError(f"S3 upload failed: {error_code} — {error_msg}")


# ---------------------------------------------------------------------------
# FastAPI Router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/applications", tags=["applications"])


@router.post("/stream", response_model=StreamAppResponse, status_code=201)
async def create_stream_application(config: StreamAppRequest):
    """
    Create a new stream application config.

    Generates a Vobiz-compatible XML from the provided streaming config,
    uploads it to S3, and returns the public URL to use as the
    Vobiz application's answer_url.

    Example request:
        POST /api/applications/stream
        {
            "stream_url": "wss://my-server.com/audio",
            "recording": true,
            "transcription": false
        }

    Example response:
        {
            "app_id": "3f8a1c2d-...",
            "answer_url": "https://my-bucket.s3.ap-south-1.amazonaws.com/vobiz-xml/3f8a1c2d-....xml",
            "xml_preview": "<?xml ...>...</Response>",
            "stream_url": "wss://my-server.com/audio",
            "recording": true,
            "transcription": false,
            "created_at": "2026-03-19T10:00:00+00:00"
        }
    """
    app_id     = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    # 1. Generate XML
    xml_content = generate_stream_xml(config)
    logger.info(f"Generated XML for app_id={app_id}, stream_url={config.stream_url}")

    # 2. Upload to S3
    answer_url = upload_to_s3(xml_content, app_id)
    logger.info(f"App {app_id} answer_url = {answer_url}")

    # 3. Store in registry
    _app_registry[app_id] = {
        "app_id":        app_id,
        "answer_url":    answer_url,
        "xml_preview":   xml_content,
        "stream_url":    config.stream_url,
        "recording":     config.recording,
        "transcription": config.transcription,
        "created_at":    created_at,
    }

    return StreamAppResponse(
        app_id=app_id,
        answer_url=answer_url,
        xml_preview=xml_content,
        stream_url=config.stream_url,
        recording=config.recording,
        transcription=config.transcription,
        created_at=created_at,
    )


@router.get("/{app_id}", response_model=AppConfigResponse)
async def get_application(app_id: str):
    """
    Retrieve an existing application config by app_id.

    Returns the answer_url (S3 or local URL), XML preview, and config details.
    Note: the registry is in-memory and resets on server restart.
    """
    entry = _app_registry.get(app_id)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail=f"Application '{app_id}' not found. "
                   "The registry resets on server restart — "
                   "re-create the application if needed.",
        )
    return AppConfigResponse(**entry)


@router.get("/{app_id}/xml")
async def serve_xml(app_id: str):
    """
    Serve the generated XML directly from this server.

    Used as the answer_url in LOCAL MODE (when S3_BUCKET_NAME is not set).
    Vobiz will GET this URL when a call connects to fetch the streaming config.
    """
    from fastapi.responses import Response as FastAPIResponse
    entry = _app_registry.get(app_id)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail=f"Application '{app_id}' not found.",
        )
    return FastAPIResponse(
        content=entry["xml_preview"],
        media_type="application/xml",
    )


@router.delete("/{app_id}", status_code=204)
async def delete_application(app_id: str):
    """
    Delete an application config.

    In S3 mode: also removes the XML from S3.
    In local mode: removes from in-memory registry only.

    After deletion the answer_url will stop working and any
    calls using this application will fail.
    """
    if app_id not in _app_registry:
        raise HTTPException(
            status_code=404,
            detail=f"Application '{app_id}' not found.",
        )

    # Remove from S3 (skipped in local mode)
    if not LOCAL_MODE:
        delete_from_s3(app_id)

    # Remove from registry
    del _app_registry[app_id]
    logger.info(f"Deleted application {app_id}")

    # 204 No Content — no response body
    return None
