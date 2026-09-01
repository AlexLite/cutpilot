# Architecture

```text
Browser -> CutPilot API -> AI provider (text + file metadata only)
                    |-> command validator -> human confirmation
                    |-> atomic hand-off -> AutoLogo watcher -> FFmpeg workers
```

The AI output is a JSON plan, not an FFmpeg command. The backend validates it against the command grammar before a confirmed job is transferred to AutoLogo.

Semantic editing (for example, "remove the line where the presenter misspeaks") is a separate future stage. It requires local transcription and optionally local scene analysis; it must not silently upload source video to an external provider.
