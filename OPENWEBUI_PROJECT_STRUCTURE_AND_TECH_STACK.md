# Open WebUI — Project Structure & Tech Stack

This document describes the project structure and technology stack of the Open WebUI codebase (cloned in the `rag` folder) for customization reference.

---

## Tech Stack Overview

### Frontend

| Technology | Version / Notes |
|------------|-----------------|
| **Svelte** | 5.x |
| **SvelteKit** | 2.5.x (app framework, file-based routing) |
| **TypeScript** | 5.5.x |
| **Vite** | 5.4.x (build tool & dev server) |
| **Tailwind CSS** | 4.x (with typography, container-queries plugins) |
| **Adapter** | `@sveltejs/adapter-static` (static export for backend serving) |
| **Node** | >= 18.13.0, <= 22.x |

**Notable frontend libraries:**
- **Editor / rich text:** TipTap (ProseMirror), CodeMirror 6
- **Charts / viz:** Chart.js, Vega/Vega-Lite, Mermaid
- **UI:** Bits UI, Floating UI, Tippy.js, Svelte Sonner
- **i18n:** i18next, i18next-browser-languagedetector
- **Real-time / collab:** Socket.io-client, Yjs, y-prosemirror
- **Terminal:** xterm.js
- **Other:** marked (Markdown), KaTeX (math), Pyodide (Python in browser), DOMPurify, dayjs, Fuse.js, etc.

### Backend

| Technology | Version / Notes |
|------------|-----------------|
| **Python** | >= 3.11, < 3.13 |
| **FastAPI** | 0.128.x |
| **Uvicorn** | 0.40.x (ASGI server) |
| **Pydantic** | 2.12.x |
| **Package manager** | uv (pyproject.toml, uv.lock) / Hatch (build) |

**Backend domains:**
- **Auth:** python-jose, PyJWT, bcrypt, argon2-cffi, Authlib, StarSessions (Redis)
- **HTTP client:** httpx, aiohttp, requests
- **Database:** SQLAlchemy 2.x, Alembic, Peewee, Peewee-migrate
- **Real-time:** python-socketio
- **Caching:** aiocache, Redis
- **LLM / AI:** openai, anthropic, google-genai, LangChain, sentence-transformers, tiktoken, MCP
- **RAG / vector:** ChromaDB, OpenSearch, optional: PGVector, Qdrant, Weaviate, Milvus, Pinecone, etc.
- **Documents:** PyPDF, pypandoc, unstructured, docx2txt, python-pptx, Azure Document Intelligence
- **Media:** Pillow, OpenCV, faster-whisper, soundfile, rapidocr-onnxruntime
- **Cloud:** boto3, google-cloud-storage, azure-storage-blob, azure-identity
- **Other:** Loguru, APScheduler, RestrictedPython, LDAP3

### DevOps / Run

- **Containers:** Docker, Docker Compose (multiple compose files for API, GPU, Playwright, etc.)
- **Frontend build in Docker:** Node 22 Alpine → `npm run build`
- **Backend in Docker:** Python 3.11 slim
- **Testing:** Cypress (e2e), Vitest (frontend), pytest (backend), Playwright (optional)

---

## Project Structure (High Level)

```
rag/
├── backend/                    # Python backend (Open WebUI API & logic)
│   └── open_webui/
│       ├── main.py             # FastAPI app entry, middleware, static files
│       ├── config.py, env.py, constants.py, functions.py
│       ├── internal/           # DB layer, migrations (Peewee-style)
│       ├── migrations/         # Alembic/SQLAlchemy migrations
│       ├── models/             # Data models
│       ├── routers/            # API route modules (see below)
│       ├── retrieval/          # RAG: loaders, vector DBs, web ingestion
│       ├── socket/             # WebSocket / Socket.IO
│       ├── storage/            # File/blob storage
│       ├── tools/              # Tool/function execution
│       ├── utils/              # Access control, images, MCP, telemetry, audit
│       └── static/             # Built frontend assets, swagger-ui, fonts
│
├── src/                        # Frontend (SvelteKit) source
│   ├── app.html, app.css, app.d.ts
│   ├── routes/                 # SvelteKit file-based routes
│   │   └── (app)/
│   │       ├── +layout.svelte, +page.svelte   # Main app shell & home
│   │       ├── admin/          # Admin: analytics, evaluations, functions, settings, users
│   │       ├── c/[id]/         # Chat by id
│   │       ├── channels/[id]/   # Channel view
│   │       ├── home/
│   │       ├── notes/          # Notes (list, new, [id])
│   │       ├── playground/    # Completions, images
│   │       ├── workspace/     # Functions, knowledge, models, prompts, skills
│   │       └── ...
│   └── lib/
│       ├── apis/               # API client modules (auth, chats, models, retrieval, etc.)
│       └── components/        # Svelte components (admin, channel, chat, common, …)
│
├── static/                     # Static assets (non-built)
├── scripts/                    # Build/utility scripts (e.g. prepare-pyodide.js)
├── cypress/                    # E2E tests
├── test/                       # Backend tests
├── docs/
├── litellm/                    # LiteLLM-related integration (if used)
├── local/                      # Local/dev data or overrides
│
├── package.json                # Frontend deps & scripts (npm run dev, build, lint, …)
├── package-lock.json
├── pyproject.toml              # Backend deps, scripts (open-webui entry), build config
├── uv.lock
├── vite.config.ts
├── svelte.config.js            # SvelteKit config, adapter-static
├── tailwind.config.js
├── tsconfig.json
├── Dockerfile                  # Multi-stage: Node build → Python runtime
├── docker-compose.yaml         # Main compose
├── docker-compose.*.yaml       # Variants (api, gpu, playwright, etc.)
├── Makefile
├── run.sh, run-compose.sh
└── README.md, CHANGELOG.md, LICENSE, etc.
```

---

## Backend API Surface (Routers)

Routers under `backend/open_webui/routers/` define the main API surface:

| Router | Purpose |
|--------|--------|
| `analytics.py` | Analytics endpoints |
| `audio.py` | Audio (e.g. STT/TTS) |
| `auths.py` | Auth (login, OAuth, sessions) |
| `channels.py` | Channels |
| `chats.py` | Chat CRUD & history |
| `configs.py` | App/config |
| `evaluations.py` | Model evaluations / leaderboard |
| `files.py` | File uploads & management |
| `folders.py` | Folder structure |
| `functions.py` | Custom functions/tools |
| `groups.py` | User groups |
| `images.py` | Image generation/editing |
| `knowledge.py` | Knowledge base / RAG documents |
| `memories.py` | Memory store |
| `models.py` | Model registry & config |
| `notes.py` | Notes |
| `ollama.py` | Ollama proxy / models |
| `openai.py` | OpenAI-compatible API proxy |
| `pipelines.py` | Pipelines plugin |
| `prompts.py` | Prompts |
| `retrieval.py` | RAG retrieval |
| `scim.py` | SCIM 2.0 (provisioning) |
| `skills.py` | Skills |
| `tasks.py` | Background tasks |
| `terminals.py` | Terminal sessions |
| `tools.py` | Tools |
| `users.py` | User management |
| `utils.py` | Shared API utilities |

---

## Frontend Entry Points & Routing

- **App shell:** `src/routes/(app)/+layout.svelte` and `+page.svelte`
- **Single-page style:** Adapter is **static**; the backend serves the built SPA (fallback `index.html`).
- **API usage:** Frontend calls the backend via `src/lib/apis/*` (e.g. chats, openai, retrieval, auths).

Key route groups:
- `(app)/` — main app (chat, home, workspace, notes, playground)
- `(app)/admin/` — admin UI (settings, users, functions, analytics, evaluations)
- `(app)/c/[id]` — chat by ID
- `(app)/channels/[id]` — channel
- `(app)/workspace/` — knowledge, models, prompts, skills, functions

---

## Where to Customize

| Goal | Where to look |
|------|----------------|
| **UI / theme / layout** | `src/` (Svelte components, `app.css`, Tailwind), `src/routes/(app)/+layout.svelte` |
| **New frontend pages** | `src/routes/(app)/...` (add or extend routes) |
| **Backend API** | `backend/open_webui/routers/*.py`, `backend/open_webui/main.py` |
| **RAG / retrieval** | `backend/open_webui/retrieval/` (loaders, vector dbs) |
| **Auth / users** | `backend/open_webui/routers/auths.py`, `users.py`, internal models |
| **Models / Ollama** | `backend/open_webui/routers/ollama.py`, `models.py` |
| **Environment** | `.env` / `.env.example` (backend and/or frontend build args) |
| **Docker** | `Dockerfile`, `docker-compose.yaml` and `docker-compose.*.yaml` |

---

## Quick Commands (from repo root `rag/`)

- **Frontend dev:** `npm run dev` (Vite + SvelteKit, with pyodide fetch)
- **Frontend build:** `npm run build`
- **Backend (local):** Typically run via `open-webui` CLI (after `pip install -e .` or uv) or Docker
- **Lint:** `npm run lint` (frontend + types + backend pylint)
- **Format:** `npm run format` (Prettier), `npm run format:backend` (Black)

This should give you a clear map of the project structure and tech stack for your customizations.


backend

cd rag
uv sync   # or: pip install -e ".[postgres]" if you need PostgreSQL
uv run open-webui serve

http://localhost:8080

frontend 

cd rag
npm install
npm run dev

http://localhost:5173