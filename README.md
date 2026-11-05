# aw-watcher-anydesk

A [watcher](https://docs.activitywatch.net/en/latest/watchers.html) for [ActivityWatch](https://activitywatch.net) that records your **AnyDesk sessions** as events, next to the rest of your activity data. It is the AnyDesk counterpart of `aw-watcher-teamviewer`.

No AnyDesk account, API or licence is needed. It reads AnyDesk's own log files and has no dependencies beyond the Python standard library.

## Where the sessions come from

AnyDesk keeps no connection history for **outgoing** sessions, so they are rebuilt from the trace log `ad.trace` (timestamps in **UTC**):

| Trace line | Meaning |
|------------|---------|
| `app.session - 1: Connecting to "<id>"` | remote ID being called |
| `app.frontend_session - Session started.` | session granted: event start |
| `app.frontend_session - Session stopped.` | session ended: event end |

Calls that were never accepted (no `Session started.`) are ignored.

Files read: `ad*.trace*` in `%APPDATA%\AnyDesk` and `%ProgramData%\AnyDesk`.

**Incoming** sessions are read from AnyDesk's `connection_trace.txt` (same two folders) when it exists. That file only has the start time, so those events have duration 0. This part is **not verified**: the development machine had no incoming sessions and no such file, so the format is based on AnyDesk's documented layout. Check it with `--dry-run`.

> **Privacy:** the trace also records the Windows user name typed on the remote login screen (`Connecting to terminal user '...'`), which can be a mistyped password. The watcher never reads that line and stores only the fields listed below.

> **Note:** the trace is only written while AnyDesk runs and AnyDesk trims it over time, so the watcher keeps what it has already imported. A session still running is shown with `ongoing: true` and is updated until it ends.

## Event format

Bucket: `aw-watcher-anydesk_<hostname>` (bucket type `currentwindow`, so the Timeline tooltip shows **App** and **Title**).

```json
{
  "timestamp": "2026-11-05T14:38:46+00:00",
  "duration": 301.0,
  "data": {
    "direction": "outgoing",
    "device_id": "123456789",
    "device_name": "",
    "session_type": "RemoteControl",
    "guid": "123456789@2026-11-05T14:38:46Z",
    "app": "AnyDesk",
    "title": "123456789 - outgoing",
    "ongoing": false,
    "remote_os": "Windows",
    "remote_version": "7.1.6",
    "connection": "direct"
  }
}
```

- `device_id` is the AnyDesk ID (or alias) of the remote device.
- `guid` is `<id>@<start time>`; sessions are de-duplicated by it, so restarting never creates duplicates.
- `remote_os`, `remote_version` and `connection` (`direct` or `relay`) appear when the trace has them.

## Installation

Requirements: Windows, ActivityWatch (`aw-server` running), AnyDesk, Python 3.8+.

```bash
pip install ./aw-watcher-anydesk
where aw-watcher-anydesk
```

If `where` prints nothing, add the Python `Scripts` folder to your user `PATH`; see the README of `aw-watcher-teamviewer` for the exact steps.

Then use **one** of:

- **aw-qt:** add `"aw-watcher-anydesk"` to `autostart_modules` in `%LOCALAPPDATA%\activitywatch\activitywatch\aw-qt\aw-qt.toml` (keep the other modules), then restart ActivityWatch from the tray icon. Needs a new logon after changing `PATH`.
- **Startup folder** (works when aw-qt cannot see the user `PATH`):

  ```powershell
  powershell -File install-startup.ps1            # hidden launcher in shell:startup
  powershell -File install-startup.ps1 -Remove    # removes it
  ```

Verify in the web UI (<http://localhost:5600>) under **Buckets**, or in **Timeline** with a long enough range (e.g. 48h).

### Run manually

```bash
aw-watcher-anydesk --dry-run       # list sessions found, change nothing
aw-watcher-anydesk --once          # sync once and exit
aw-watcher-anydesk                 # run continuously, polling every 60 s
aw-watcher-anydesk --interval 30 --port 5600 --host 127.0.0.1
```

## Troubleshooting

- `ActivityWatch unreachable`: `aw-server` is not running or uses another port (`--port`).
- No events: run `--dry-run`. If it prints nothing, check that `ad.trace` exists under `%APPDATA%\AnyDesk` or `%ProgramData%\AnyDesk`. The service-only `ad_svc.trace` has no frontend lines, so it cannot give session start and end.
- If AnyDesk changes its log wording in a new version, the three trace lines above are the ones to adjust in `parse_trace`.

## License

MIT
