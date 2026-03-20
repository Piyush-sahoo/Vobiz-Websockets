# Vobiz AI Voice Agent — agent/

**This is what your customer runs on their own machine.**

A full AI voice agent that handles real phone calls end-to-end.
The customer brings their own API keys — Vobiz never sees them.

## What it does

```
Vobiz call → Deepgram STT → GPT-4o-mini → OpenAI TTS → caller hears AI
```

The customer's machine handles everything. Their keys, their cost, their logic.

## Who owns what

| Thing | Owner |
|---|---|
| This code | Customer runs it |
| OpenAI key | Customer |
| Deepgram key | Customer |
| ngrok tunnel | Customer |
| Vobiz account | Customer |
| Vobiz platform | Vobiz |

---

## Files

```
agent/
├── agent.py          AI pipeline — Deepgram STT + GPT LLM + OpenAI TTS
├── server.py         FastAPI gateway — Vobiz webhooks + ngrok + WS proxy
├── make_call.py      CLI tool to trigger outbound calls
├── requirements.txt  Python dependencies
└── .env.example      Environment variable template (fill in your keys)
```

---

## Setup

```bash
cd agent/
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Open .env and fill in all your keys
```

---

## Required keys

Open `.env` and fill in:

```env
# Your ngrok token — get it at https://dashboard.ngrok.com/authtokens
NGROK_AUTH_TOKEN=your_ngrok_auth_token

# Your OpenAI key — https://platform.openai.com/api-keys
OPENAI_API_KEY=sk-your-openai-key

# Your Deepgram key — https://console.deepgram.com
DEEPGRAM_API_KEY=your_deepgram_key

# Your Vobiz credentials — from your Vobiz dashboard
VOBIZ_AUTH_ID=your_vobiz_auth_id
VOBIZ_AUTH_TOKEN=your_vobiz_auth_token

# Numbers for outbound calls
FROM_NUMBER=+91XXXXXXXXXX
TO_NUMBER=+91XXXXXXXXXX
```

---

## Running

### Terminal 1 — start the agent

```bash
python3 server.py
```

When it starts you will see:

```
Agent WebSocket server starting on port 5001...
ngrok tunnel established!

  Answer URL: https://abc123.ngrok.io/answer  <- set this in Vobiz
  Hangup URL: https://abc123.ngrok.io/hangup

HTTP server starting on port 8001...
```

### Set the Answer URL in Vobiz

Copy the **Answer URL** from the logs.

In your Vobiz dashboard:
```
Applications → your application → Answer URL → paste → Save
Hangup URL → paste the Hangup URL → Save
```

### Terminal 2 — trigger an outbound call

```bash
python3 make_call.py
```

`make_call.py` auto-discovers the ngrok URL from the running server — no copy-pasting.

Or override everything:

```bash
python3 make_call.py --to +91XXXXXXXXXX --from +91XXXXXXXXXX
```

---

## How the AI pipeline works

```
[Caller speaks]
      ↓
Vobiz streams audio (mulaw 8kHz, 20ms chunks) via WebSocket
      ↓
server.py proxies WebSocket → agent.py (port 5001)
      ↓
agent.py forwards audio to Deepgram (speech-to-text)
      ↓
Deepgram returns live transcript
      ↓
After 1.2s silence → GPT-4o-mini generates response
      ↓
OpenAI TTS converts response to audio (PCM 24kHz)
      ↓
agent.py resamples 24kHz → 8kHz, encodes to mulaw
      ↓
Sends audio chunks back to Vobiz → caller hears AI
```

**Barge-in:** If the caller speaks while the AI is talking, the AI immediately stops
and starts processing the new input.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `HTTP_PORT` | Yes | FastAPI port. Default `8001`. Avoid `5000` on Mac (AirPlay). |
| `AGENT_WS_PORT` | Yes | Internal agent WebSocket port. Default `5001`. Local only. |
| `NGROK_AUTH_TOKEN` | Yes | ngrok token. Required — Vobiz must reach your machine. |
| `OPENAI_API_KEY` | Yes | Your OpenAI key for GPT + TTS. |
| `OPENAI_TTS_VOICE` | No | TTS voice. Default `alloy`. Options: `alloy echo fable onyx nova shimmer`. |
| `DEEPGRAM_API_KEY` | Yes | Your Deepgram key for speech-to-text. |
| `AGENT_SYSTEM_PROMPT` | No | System prompt for the AI agent's personality. |
| `VOBIZ_AUTH_ID` | make_call.py | Your Vobiz Auth ID. |
| `VOBIZ_AUTH_TOKEN` | make_call.py | Your Vobiz Auth Token. |
| `FROM_NUMBER` | make_call.py | Your Vobiz number for outbound calls. |
| `TO_NUMBER` | make_call.py | Destination number for outbound calls. |

---

## Ports

| Port | What | Exposed? |
|---|---|---|
| `8001` | FastAPI (webhooks + WS proxy) | Yes — via ngrok |
| `5001` | agent.py WebSocket server | No — local only |

---

## Customising the agent

**Change the AI personality** — edit `AGENT_SYSTEM_PROMPT` in `.env`:
```env
AGENT_SYSTEM_PROMPT=You are a sales agent for Acme Corp. Be friendly and professional. Ask the caller for their name first.
```

**Change the voice** — edit `OPENAI_TTS_VOICE`:
```env
OPENAI_TTS_VOICE=nova
```

**Change the greeting** — edit `agent.py` line:
```python
greeting = "Hello! This is the Vobiz AI assistant. How can I help you today?"
```

**Change response length** — edit `agent.py`:
```python
max_tokens=150   # increase for longer responses
```

**Change silence detection** — edit `agent.py`:
```python
await asyncio.sleep(1.2)   # seconds to wait after speech ends before responding
```

---

## Troubleshooting

**Server exits with "Missing required env var"**
All three keys are required: `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `NGROK_AUTH_TOKEN`.
Check your `.env` file.

**"Address already in use" on port 8001**
Something else is using the port. Change `HTTP_PORT` in `.env` to another port (e.g. `8002`).

**Call connects but caller hears nothing**
- Check terminal for OpenAI TTS errors
- Verify `OPENAI_API_KEY` is valid and has credits
- Check that the Answer URL in your Vobiz Application matches the ngrok URL in the logs

**AI is slow to respond**
The main delay sources are:
1. Deepgram transcription (usually <300ms)
2. GPT response (usually 500ms–1s with gpt-4o-mini)
3. TTS generation (usually 500ms–1s)

To reduce silence wait time edit `agent.py`:
```python
await asyncio.sleep(0.8)   # reduce from 1.2s to 0.8s
```

**ngrok URL changes every restart**
This is normal on a free ngrok plan. Either:
- Update the Answer URL in Vobiz after each restart
- Or upgrade to a paid ngrok plan for a fixed subdomain
