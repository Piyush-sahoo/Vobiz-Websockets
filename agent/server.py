"""
server.py — AI Voice Agent Gateway
=====================================
Starts the full AI voice agent pipeline:
  - agent.py  WebSocket server on port 5001 (Deepgram STT → GPT → TTS)
  - ngrok     public HTTPS/WSS tunnel
  - FastAPI   HTTP server on port 8001 (Vobiz webhooks + WS proxy)

Required .env keys:
  OPENAI_API_KEY, DEEPGRAM_API_KEY, NGROK_AUTH_TOKEN
  VOBIZ_AUTH_ID, VOBIZ_AUTH_TOKEN (for make_call.py)

This server is completely independent of the server/ folder.
It does NOT use xml_builder.py or S3.
"""

import os
import sys
import asyncio
import logging
import threading
import uvicorn

from fastapi import FastAPI, Request
from fastapi.responses import Response
from dotenv import load_dotenv
from pyngrok import ngrok, conf

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HTTP_PORT        = int(os.getenv("HTTP_PORT", "8001"))
WS_PORT          = int(os.getenv("AGENT_WS_PORT", "5001"))
NGROK_AUTH_TOKEN = os.getenv("NGROK_AUTH_TOKEN", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("agent-server")

# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(title="Vobiz AI Voice Agent")

NGROK_URL = None


@app.post("/answer")
async def answer_call(request: Request):
    """
    Vobiz calls this when a call connects.
    Returns XML that tells Vobiz to open a bidirectional WebSocket
    to this server's /ws endpoint, which proxies to agent.py.
    """
    form_data   = await request.form()
    call_uuid   = form_data.get("CallUUID", "unknown")
    from_number = form_data.get("From", "unknown")
    to_number   = form_data.get("To", "unknown")
    direction   = form_data.get("Direction", "unknown")

    logger.info(f"Call connected — UUID={call_uuid}, From={from_number}, To={to_number}, Direction={direction}")

    ws_url = NGROK_URL.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{ws_url}/ws"

    xml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000" statusCallbackUrl="{NGROK_URL}/stream-status" statusCallbackMethod="POST">
        {ws_url}
    </Stream>
</Response>"""

    logger.info(f"Returning Stream XML → {ws_url}")
    return Response(content=xml_response, media_type="application/xml")


@app.post("/hangup")
async def hangup_call(request: Request):
    """Vobiz calls this when the call ends."""
    form_data    = await request.form()
    call_uuid    = form_data.get("CallUUID", "unknown")
    duration     = form_data.get("Duration", "0")
    hangup_cause = form_data.get("HangupCause", "unknown")

    logger.info(f"Call ended — UUID={call_uuid}, Duration={duration}s, Cause={hangup_cause}")
    return Response(content="OK", status_code=200)


@app.post("/stream-status")
async def stream_status(request: Request):
    """Vobiz stream lifecycle events."""
    form_data = await request.form()
    event     = form_data.get("Event", "unknown")
    stream_id = form_data.get("StreamID", "unknown")
    call_uuid = form_data.get("CallUUID", "unknown")

    logger.info(f"Stream event — Event={event}, StreamID={stream_id}, CallUUID={call_uuid}")
    return Response(content="OK", status_code=200)


@app.get("/health")
async def health_check():
    """Health check — used by make_call.py to auto-discover the ngrok URL."""
    return {"status": "healthy", "ngrok_url": NGROK_URL}


# ---------------------------------------------------------------------------
# WebSocket proxy  (Vobiz → agent.py)
# ---------------------------------------------------------------------------

from starlette.websockets import WebSocket as StarletteWebSocket
import websockets as ws_lib


@app.websocket("/ws")
async def websocket_proxy(websocket: StarletteWebSocket):
    """
    Proxy WebSocket from Vobiz → local agent.py on port 5001.
    All audio and control messages pass through here transparently.
    """
    await websocket.accept()
    logger.info("WebSocket accepted from Vobiz")

    agent_url = f"ws://127.0.0.1:{WS_PORT}"
    try:
        async with ws_lib.connect(agent_url) as agent_ws:
            logger.info(f"Connected to agent at {agent_url}")

            async def vobiz_to_agent():
                try:
                    while True:
                        data = await websocket.receive_text()
                        await agent_ws.send(data)
                except Exception:
                    pass

            async def agent_to_vobiz():
                try:
                    async for message in agent_ws:
                        await websocket.send_text(message)
                except Exception:
                    pass

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(vobiz_to_agent()),
                    asyncio.create_task(agent_to_vobiz()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except Exception as e:
        logger.error(f"WebSocket proxy error: {e}")
    finally:
        logger.info("WebSocket proxy closed")


# ---------------------------------------------------------------------------
# ngrok
# ---------------------------------------------------------------------------

def setup_ngrok():
    global NGROK_URL

    if NGROK_AUTH_TOKEN:
        conf.get_default().auth_token = NGROK_AUTH_TOKEN

    tunnel   = ngrok.connect(HTTP_PORT, "http")
    NGROK_URL = tunnel.public_url

    if NGROK_URL.startswith("http://"):
        NGROK_URL = NGROK_URL.replace("http://", "https://")

    logger.info("=" * 60)
    logger.info("ngrok tunnel established!")
    logger.info(f"")
    logger.info(f"  Answer URL: {NGROK_URL}/answer  ← set this in Vobiz")
    logger.info(f"  Hangup URL: {NGROK_URL}/hangup")
    logger.info(f"")
    logger.info("=" * 60)

    return NGROK_URL


# ---------------------------------------------------------------------------
# Agent thread
# ---------------------------------------------------------------------------

def run_agent():
    """Start agent.py's WebSocket server in a background thread."""
    from agent import start_agent_server

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_agent_server())
    loop.run_forever()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global NGROK_URL

    logger.info("Starting Vobiz AI Voice Agent...")

    # Validate required AI keys — always needed
    missing = []
    if not os.getenv("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    if not os.getenv("DEEPGRAM_API_KEY"):
        missing.append("DEEPGRAM_API_KEY")

    if missing:
        for key in missing:
            logger.error(f"Missing required env var: {key}")
        logger.error("Add the above keys to your .env file and try again.")
        sys.exit(1)

    # 1. Start agent WebSocket server
    import time
    agent_thread = threading.Thread(target=run_agent, daemon=True)
    agent_thread.start()
    logger.info(f"Agent WebSocket server starting on port {WS_PORT}...")
    time.sleep(1)

    # 2. ngrok — optional for local testing
    if NGROK_AUTH_TOKEN:
        try:
            setup_ngrok()
        except Exception as e:
            logger.error(f"ngrok failed: {e}")
            sys.exit(1)
    else:
        NGROK_URL = f"http://localhost:{HTTP_PORT}"
        logger.info("=" * 60)
        logger.info("LOCAL MODE — ngrok skipped.")
        logger.info(f"  Agent WebSocket running at: ws://localhost:{WS_PORT}")
        logger.info(f"  HTTP server running at:     http://localhost:{HTTP_PORT}")
        logger.info("")
        logger.info("  To test with the builder UI:")
        logger.info(f"  Paste  ws://localhost:{WS_PORT}  as the Stream WebSocket URL")
        logger.info("")
        logger.info("  To use with real Vobiz calls, add NGROK_AUTH_TOKEN to .env")
        logger.info("=" * 60)

    # 3. Start HTTP server
    logger.info(f"HTTP server starting on port {HTTP_PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT, log_level="warning")


if __name__ == "__main__":
    main()
