# LXC deployment checklist

This is a checklist, not an unattended installer. Do not deploy until the
the `cutpilot-watcher.service`, its `/srv/cutpilot` path, and the
permissions of the intended service account have been checked on the target
LXC.

1. Install Python 3 from the LXC distribution packages. No third-party Python
   packages are required for the first release.
2. Copy the repository to `/opt/cutpilot` and verify that `python3 -m app.server`
   imports successfully from that directory.
3. Create an unprivileged `cutpilot` user. Grant it read access to
   `/srv/cutpilot/AI_Cut` and write access to `/srv/cutpilot` (the exact group
   depends on the existing worker; verify this rather than broadening access).
4. Create `/etc/cutpilot/cutpilot.env` from `.env.example`. Put the provider key
   in this file only after the user explicitly supplies/authorizes it; never put
   it in Git, the unit file, or command arguments. Set `root:root` ownership and
   mode `0600`.
5. Install `cutpilot-watcher.service` and `cutpilot.service` to `/etc/systemd/system/`, run
   `systemctl daemon-reload`, and enable/start it only after the above checks.
6. Keep `CUTPILOT_HOST=127.0.0.1` unless a separately reviewed reverse proxy is
   configured. Verify `GET /api/files`, a fake-provider/local smoke test, and a
   real confirmed hand-off while watching the CutPilot worker.

The first release sends only the selected basename, byte size, and user task to
the configured provider. It does not upload video bytes and does not expose a
shell or FFmpeg endpoint.
