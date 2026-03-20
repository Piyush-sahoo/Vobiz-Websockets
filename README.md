# Vobiz Python WebSockets

Two independent projects. Pick the one you need.

---

## server/ — Vobiz Internal

Hosted and managed by Vobiz. Customers do not run this.

Generates Vobiz XML from a simple form (stream URL + toggles), uploads it to S3,
and returns an `answer_url` the customer pastes into their Vobiz Application.

```
server/
├── server.py        API server + XML builder + ngrok
├── xml_builder.py   XML generation + S3 upload
├── index.html       Builder UI
├── requirements.txt No OpenAI. No Deepgram. Just FastAPI + boto3.
└── .env.example     AWS + ngrok config
```

See [server/README.md](server/README.md) for setup and usage.

---

## agent/ — Customer Copy

The customer runs this on their own machine with their own API keys.

Full AI voice agent: Deepgram STT → GPT-4o-mini → OpenAI TTS — over a real phone call.

```
agent/
├── agent.py         AI pipeline (Deepgram + GPT + TTS)
├── server.py        Vobiz webhooks + ngrok + WebSocket proxy
├── make_call.py     Trigger outbound calls
├── requirements.txt OpenAI + Deepgram + websockets + pyngrok
└── .env.example     Customer keys (OpenAI, Deepgram, ngrok, Vobiz)
```

See [agent/README.md](agent/README.md) for setup and usage.

---

## Key difference

| | server/ | agent/ |
|---|---|---|
| Who runs it | Vobiz | Customer |
| OpenAI key | Not needed | Customer's own |
| Deepgram key | Not needed | Customer's own |
| AWS S3 | Yes (Vobiz bucket) | Not needed |
| What it does | Generates XML config | Handles live AI calls |
