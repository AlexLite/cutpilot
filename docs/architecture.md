# Architecture

```text
Browser -> CutPilot API -> AI provider (text + file metadata only)
                    |-> command validator -> human confirmation
                    |-> atomic hand-off -> CutPilot watcher -> FFmpeg workers
```

The AI output is a JSON plan, not an FFmpeg command. The backend validates it against the command grammar before a confirmed job is transferred to CutPilot.

The first implementation uses Python's standard library HTTP server. `/api/plan` sends only the selected basename, byte size, media metadata, and user task to the configured OpenRouter-compatible adapter. The server validates the returned command token array, constructs the CutPilot filename itself, and stores the validated plan in SQLite for a short TTL. `/api/upload` accepts a video into `AI_Cut` using a unique temporary file, fsync, and no-clobber publication; it never accepts arbitrary paths or shell commands. `/api/jobs` requires explicit confirmation and verifies the source size, modification time, filesystem change time, and local content fingerprint have not changed; it copies a snapshot to a random `.part` file in the watcher directory, fsyncs it, and publishes it without overwriting an existing queue file. It never deletes the source.

The service defaults to `127.0.0.1`; place a separately configured reverse proxy in front of it only if LAN access is required. The systemd unit runs as an unprivileged `cutpilot` user with an environment file outside Git. Before LXC deployment, create the user, grant only the required directory permissions, install Python 3, copy `deploy/cutpilot.service`, and verify the CutPilot watcher path and service account access.

Semantic editing (for example, "remove the line where the presenter misspeaks") is a separate future stage. It requires local transcription and optionally local scene analysis; it must not silently upload source video to an external provider.
