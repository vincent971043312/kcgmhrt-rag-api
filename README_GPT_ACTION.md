RAG + ChatGPT (Custom GPT) via Actions
=====================================

This repo includes your local RAG (`rag.py`) and a small HTTP API (`server.py`) so a custom GPT can call it through Actions.

Quick start
-----------

1) Prereqs
- Python 3.10+
- Set `OPENAI_API_KEY` (used by the RAG backend, not exposed to GPT):
  - Put it in `.env` or export in your shell
  - Optionally set `AUTH_TOKEN` to protect the API with a Bearer token

2) Install

```
pip install -r requirements.txt
```

3) Run API

```
uvicorn server:app --host 0.0.0.0 --port 8000
```

Visit `http://localhost:8000/docs` and `http://localhost:8000/openapi.json`.

4) Make it public (for GPT Actions)

Use any hosting/tunnel you like (Render, Fly.io, Railway, AWS, or a tunnel):

- Ngrok (example):
  - Install ngrok, then run `ngrok http 8000`
  - Note the https URL shown, e.g. `https://abcd1234.ngrok.io`

If you set `AUTH_TOKEN=your-secret`, remember this value for the GPT’s Action auth.

5) Add Action to your custom GPT

- Open ChatGPT → Create a GPT → Configure → Actions
- Add a new Action → Import from URL → paste your API spec URL:
  - `https://<your-domain-or-ngrok>/openapi.json`
  - OR upload `gpt-action.yaml` (replace `servers[0].url` with your URL first)
- Authentication: choose “API Key” (Bearer) and set the key to the same value as `AUTH_TOKEN`.

That’s it — your GPT can now call:

- `GET /files` — discover available docs (under `docs/`)
- `POST /query` — ask a question against a specific file
- `POST /reload` — force rebuild an index after you update a file

Usage examples
--------------

List files:

```
curl -H "Authorization: Bearer $AUTH_TOKEN" \
  https://<your-domain>/files
```

Ask a question:

```
curl -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $AUTH_TOKEN" \
  -d '{"file":"example.pdf","question":"重點摘要"}' \
  https://<your-domain>/query
```

Force rebuild for a file after edits:

```
curl -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $AUTH_TOKEN" \
  -d '{"file":"example.pdf"}' \
  https://<your-domain>/reload
```

Notes and tips
--------------

- Place your content under `docs/` (`.pdf`, `.txt`, `.md`).
- Indexes persist under `db/` per-file; the server reuses them across requests.
- The backend uses `gpt-4o` for answers and `text-embedding-3-small` for embeddings. Adjust in `rag.py`.
- If you just need simple retrieval inside ChatGPT (no server), you can upload docs to the GPT’s “Knowledge”. The Action route gives you full control and scale.
- For production, host behind HTTPS with a stable domain and set `AUTH_TOKEN`.

