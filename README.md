# CutPilot

CutPilot is a private, local-network web application for preparing safe video-processing jobs.

It lets a user choose a video from `AI_Cut`, describe the required edit in natural language, receive an AI-generated plan, review it, and only then submit an allowed FFmpeg job to AutoLogo.

## Principles

- The AI never receives shell or filesystem access.
- The backend accepts only an allowlisted set of AutoLogo commands.
- Every job requires a human confirmation before it reaches the worker.
- Source videos stay inside the local infrastructure; the first release sends only the user's text and file metadata to an AI provider.

## Planned components

- `app/` — web UI and API.
- `app/ai/` — provider adapters, beginning with OpenRouter.
- `app/commands/` — parser and validator for the existing AutoLogo command language.
- `app/jobs/` — confirmed-job hand-off to the AutoLogo worker.
- `deploy/` — systemd, reverse proxy, and environment examples.

## First release

1. Browse video files in `AI_Cut`.
2. Describe the desired conversion, logo operation, or timecode edit.
3. Generate a typed command plan.
4. Review and confirm the final filename and commands.
5. Submit the file atomically to the existing AutoLogo worker.

## Security

No API keys are committed. Configuration is stored only in a root-readable environment file on the LXC.
