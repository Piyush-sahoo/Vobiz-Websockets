# Vobiz Stream Application Builder — server/

**Hosted and managed by Vobiz. Customers do not run this.**

This server lets customers configure streaming calls without writing XML themselves.
They fill a form (stream URL + toggles), the API generates the Vobiz XML, uploads it to S3,
and returns an `answer_url` they paste into their Vobiz Application settings.

## Who owns what

| Thing | Owner |
|---|---|
| This server | Vobiz |
| AWS S3 bucket | Vobiz |
| The customer's WebSocket server | Customer |
| The customer's AI keys | Customer |

---

## Files

```
server/
├── server.py         FastAPI app — webhooks + XML builder API + ngrok
├── xml_builder.py    Generates XML, uploads to S3, serves locally in dev
├── index.html        Frontend UI — open in browser or host on S3
├── requirements.txt  Python dependencies (no OpenAI, no Deepgram)
└── .env.example      Environment variable template
```

---

## Setup

```bash
cd server/
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

---

## Running locally (no S3, no ngrok)

Fill in `.env` — only `HTTP_PORT` is required:

```env
HTTP_PORT=8000
S3_BUCKET_NAME=           # leave blank — XML served locally
LOCAL_BASE_URL=http://localhost:8000
```

Start the server:

```bash
python3 server.py
```

Output:
```
LOCAL MODE — no ngrok, no S3 required.
  API:      http://localhost:8000
  UI:       open index.html in your browser
  API docs: http://localhost:8000/docs
```

Open `index.html` in your browser. Set **API Server URL** to `http://localhost:8000`.
Fill in a `wss://` stream URL, click **Create Application**, copy the answer URL.

---

## Running with ngrok (for real Vobiz calls, no S3)

Add to `.env`:

```env
NGROK_AUTH_TOKEN=your_ngrok_token
LOCAL_BASE_URL=           # leave blank — server fills this from ngrok automatically
```

Start:

```bash
python3 server.py
```

The answer URL returned by the builder will use the ngrok address, which Vobiz can reach.
Set this as the Answer URL in your Vobiz Application.

---

## Running in production (S3)

Fill in `.env`:

```env
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=ap-south-1
S3_BUCKET_NAME=your-bucket-name
```

Upload the frontend to S3 (one time):

```bash
python3 -c "from xml_builder import upload_frontend_to_s3; print(upload_frontend_to_s3())"
```

Start:

```bash
python3 server.py
```

XMLs are now uploaded to S3 on every Create Application request.
The `answer_url` returned is a permanent S3 link — no server needed to serve the XML.

---

## API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/applications/stream` | Create app → generate XML → upload to S3 → return answer_url |
| `GET` | `/api/applications/{app_id}` | Get app config and answer URL |
| `GET` | `/api/applications/{app_id}/xml` | Serve raw XML (local mode answer_url) |
| `DELETE` | `/api/applications/{app_id}` | Delete app and remove XML from S3 |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Interactive API docs (Swagger UI) |

### POST /api/applications/stream — body

```json
{
  "stream_url": "wss://customer-server.com/audio",
  "recording": false,
  "transcription": false,
  "keep_call_alive": true,
  "content_type": "audio/x-mulaw;rate=8000",
  "status_callback_url": "https://customer-server.com/events"
}
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `HTTP_PORT` | Yes | Server port. Default `8000`. Avoid `5000` on Mac. |
| `NGROK_AUTH_TOKEN` | Dev only | ngrok token. Leave blank for local-only mode. |
| `LOCAL_BASE_URL` | Dev only | Base URL for local answer_urls. Default `http://localhost:8000`. |
| `AWS_ACCESS_KEY_ID` | Production | AWS credentials for S3. |
| `AWS_SECRET_ACCESS_KEY` | Production | AWS credentials for S3. |
| `AWS_REGION` | Production | AWS region. Default `ap-south-1`. |
| `S3_BUCKET_NAME` | Production | S3 bucket. Leave blank for local mode. |

---

## How the answer_url works

```
Local dev:   http://localhost:8000/api/applications/{id}/xml
             (XML served from this server — only reachable locally)

With ngrok:  https://abc123.ngrok.io/api/applications/{id}/xml
             (XML served via ngrok — Vobiz can reach it)

Production:  https://bucket.s3.ap-south-1.amazonaws.com/vobiz-xml/{id}.xml
             (XML on S3 — permanent, no server needed)
```

Vobiz fetches the XML from that URL every time a call connects.
