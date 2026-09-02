# CutPilot

CutPilot is a private, local-network web application for preparing safe video-processing jobs.

It lets a user choose a video from `AI_Cut`, describe the required edit in natural language, receive an AI-generated plan, review it, and only then submit an allowed FFmpeg job to CutPilot.

## Principles

- The AI never receives shell or filesystem access.
- The backend accepts only an allowlisted set of CutPilot commands.
- Every job requires a human confirmation before it reaches the worker.
- Source videos stay inside the local infrastructure; the first release sends only the user's text and file metadata to an AI provider.

## Components

- `app/` — web UI and API.
- `app/ai.py` — server-side provider adapter, beginning with OpenRouter.
- `app/commands.py` — parser and validator for the CutPilot command language.
- `app/jobs.py` — confirmed-job hand-off to the CutPilot worker.
- `deploy/` — systemd, reverse proxy, and environment examples.
- `deploy/cutpilot-watcher` — the CutPilot folder watcher.
- `assets/` — horizontal and vertical logo assets used by the watcher.
- `docs/!!!ПРОЧИТАЙ.html` and `docs/!!!ПРОЧИТАЙ.txt` — the current user instructions served through the SMB share.

## First release (implemented locally)

1. Browse video files in `AI_Cut`.
2. Describe the desired conversion, logo operation, or timecode edit.
3. Generate a typed command plan.
4. Review and confirm the final filename and commands.
5. Submit a copy of the file atomically to the CutPilot worker. The original remains in `AI_Cut`.

Run locally from the repository:

```text
set CUTPILOT_AI_CUT_DIRECTORY=C:\path\to\cutpilot\AI_Cut
set CUTPILOT_DIRECTORY=C:\path\to\cutpilot
set OPENROUTER_API_KEY=...
set OPENROUTER_MODEL=...
python -m app.server
```

Open `http://127.0.0.1:8787`. The service binds to localhost by default. `deploy/cutpilot.service` is a systemd template for an LXC; it is intentionally not a deployment script. On LXC, copy `.env.example` to `/etc/cutpilot/cutpilot.env`, fill the key outside Git, set ownership to `root:root`, and mode `0600` before starting the service.

Run the local container with Docker Compose:

```text
copy .env.example .env
docker compose up --build
```

Put configuration only in the untracked `.env`. Compose mounts `./data/cutpilot` to `/srv/cutpilot` and publishes the service on `CUTPILOT_BIND_ADDRESS:8787` (localhost by default). The container has outbound access to the configured OpenRouter endpoint. For LAN access, set the bind address to the intended LAN interface and block WAN inbound access in the router/firewall; that network policy is deliberately outside this repository. The container contains the CutPilot API only, while the watcher remains a separate service and must see the same host directory for a real hand-off.

The API is deliberately small: `GET /api/files`, `POST /api/upload`, `POST /api/plan`, and `POST /api/jobs`. Plans are stored in SQLite for up to 30 minutes and can be consumed once only after `confirmed: true`. Uploads accept only direct-child video filenames, are size-limited, written to a unique temporary file, fsynced, and published without overwriting an existing file. No endpoint accepts shell, FFmpeg commands, or arbitrary paths.

## Security

No API keys are committed. Configuration is stored only in a root-readable environment file on the LXC.
