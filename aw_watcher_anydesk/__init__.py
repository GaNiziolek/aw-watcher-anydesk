"""ActivityWatch watcher for AnyDesk sessions.

AnyDesk has no session history file for outgoing connections, so sessions are
rebuilt from its trace log (`ad.trace`, timestamps in UTC):
  * `app.session - N: Connecting to "<id>"`   -> remote ID being called
  * `app.frontend_session - Session started.` -> session granted (event start)
  * `app.frontend_session - Session stopped.` -> session ended (event end)

Trace files are read from %APPDATA%\\AnyDesk and %ProgramData%\\AnyDesk
(`ad*.trace*`). Incoming sessions are read from AnyDesk's own
`connection_trace.txt` when present (start time only, see README).

Only a whitelist of fields is stored. The trace also records the Windows user
name typed on the remote login screen (`Connecting to terminal user '...'`);
that line is never read, since it can contain a mistyped password.

Events go to the bucket `aw-watcher-anydesk_<hostname>`. Standard library only.

Usage:  aw-watcher-anydesk [--once] [--interval 60] [--port 5600]
"""
import argparse
import glob
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

CLIENT = "aw-watcher-anydesk"
# "currentwindow" makes the aw-webui Timeline tooltip show App / Title rows.
EVENT_TYPE = "currentwindow"
ONGOING_WINDOW = timedelta(minutes=10)  # trace updated this recently => still running

LINE = re.compile(
    r"^\s*(?:info|warning|error|debug)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+"
    r"\s+\w+\s+(\d+)\s+\d+\s+(?:\d+\s+)?([\w.]+) - (.*)$")
CONNECTING = re.compile(r'^\d+: Connecting to "([^"]+)"$')
CONN_TRACE = re.compile(
    r"^(Incoming|Outgoing)\s+(\d{4}-\d{2}-\d{2}),\s*(\d{2}:\d{2})(?::(\d{2}))?\s+(\S+)\s+(\S+)")
REJECTED = {"rejected", "reject", "timeout", "closed", "denied"}


def trace_files():
    """Trace logs found, oldest first."""
    dirs = [os.path.join(os.environ.get("APPDATA", ""), "AnyDesk"),
            os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "AnyDesk")]
    found = {}
    for d in dirs:
        for p in glob.glob(os.path.join(d, "ad*.trace*")):
            found[os.path.normcase(p)] = p
    return sorted(found.values(), key=lambda p: os.path.getmtime(p))


def conn_trace_files():
    dirs = [os.path.join(os.environ.get("APPDATA", ""), "AnyDesk"),
            os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "AnyDesk")]
    return [p for d in dirs for p in [os.path.join(d, "connection_trace.txt")] if os.path.isfile(p)]


def read_lines(path):
    # AnyDesk writes some logs as UTF-16; trace logs are ASCII/UTF-8.
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace").splitlines()
    return raw.decode("utf-8-sig", errors="replace").splitlines()


def make_data(direction, remote_id, start, **extra):
    data = {
        "direction": direction,
        "device_id": remote_id,
        "device_name": "",
        "session_type": "RemoteControl",
        "guid": f"{remote_id}@{start.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "app": "AnyDesk",
        "title": f"{remote_id} - {direction}",
    }
    data.update(extra)
    return data


def parse_trace(path, now):
    """Outgoing sessions from one trace file."""
    sessions, open_by_pid = [], {}
    pending_id, pending_extra, last_ts = None, {}, None
    try:
        lines = read_lines(path)
    except OSError:
        return sessions
    for line in lines:
        m = LINE.match(line)
        if not m:
            continue
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        pid, comp, msg = m.group(2), m.group(3), m.group(4)
        last_ts = ts
        if comp == "app.session":
            c = CONNECTING.match(msg)
            if c:
                pending_id, pending_extra = c.group(1), {}
        elif comp == "app.frontend_session":
            if msg.startswith("Remote OS: "):
                pending_extra["remote_os"] = msg[11:].strip()
            elif msg.startswith("Remote version: "):
                pending_extra["remote_version"] = msg[16:].strip()
            elif msg.startswith("Connection flags: "):
                flags = msg[18:].split()
                pending_extra["connection"] = "direct" if "direct" in flags else "relay"
            elif msg == "Session started." and pending_id:
                open_by_pid[pid] = (pending_id, ts, dict(pending_extra))
                pending_id, pending_extra = None, {}
            elif msg == "Session stopped." and pid in open_by_pid:
                rid, start, extra = open_by_pid.pop(pid)
                sessions.append({"start": start, "end": ts,
                                 "data": make_data("outgoing", rid, start, ongoing=False, **extra)})
    # Sessions with no "stopped" line: still running, or AnyDesk was killed.
    for rid, start, extra in open_by_pid.values():
        end = last_ts or start
        ongoing = now - end < ONGOING_WINDOW
        sessions.append({"start": start, "end": end,
                         "data": make_data("outgoing", rid, start, ongoing=ongoing, **extra)})
    return sessions


def parse_connection_trace(path):
    """Incoming sessions from AnyDesk's connection_trace.txt (start time only).

    Times in this file are local time. Lines look like:
      Incoming    2026-01-15, 14:30    User    123456789    123456789
    """
    sessions = []
    try:
        lines = read_lines(path)
    except OSError:
        return sessions
    for line in lines:
        m = CONN_TRACE.match(line.strip())
        if not m or m.group(5).lower() in REJECTED:
            continue
        direction = m.group(1).lower()
        stamp = f"{m.group(2)} {m.group(3)}:{m.group(4) or '00'}"
        try:
            local = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").astimezone()
        except ValueError:
            continue
        start = local.astimezone(timezone.utc)
        rid = m.group(6)
        sessions.append({"start": start, "end": start,
                         "data": make_data(direction, rid, start, ongoing=False,
                                           source="connection_trace.txt")})
    return sessions


def collect_sessions():
    now = datetime.now(timezone.utc)
    out, seen = [], set()
    for path in trace_files():
        for s in parse_trace(path, now):
            if s["data"]["guid"] not in seen:
                seen.add(s["data"]["guid"])
                out.append(s)
    # connection_trace.txt only fills gaps: skip rows already covered by a
    # trace session with the same remote ID within 2 minutes.
    for path in conn_trace_files():
        for s in parse_connection_trace(path):
            if any(o["data"]["device_id"] == s["data"]["device_id"]
                   and abs((o["start"] - s["start"]).total_seconds()) < 120 for o in out):
                continue
            if s["data"]["guid"] not in seen:
                seen.add(s["data"]["guid"])
                out.append(s)
    return out


# --- ActivityWatch ---------------------------------------------------------

def api(base, method, path, body=None):
    req = urllib.request.Request(
        base + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def ensure_bucket(base, bucket_id, hostname):
    try:
        api(base, "GET", f"/buckets/{bucket_id}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        api(base, "POST", f"/buckets/{bucket_id}",
            {"client": CLIENT, "type": EVENT_TYPE, "hostname": hostname})


def sync(base, bucket_id):
    existing = api(base, "GET", f"/buckets/{bucket_id}/events?limit=-1") or []
    known = {e["data"].get("guid"): e for e in existing}
    new, updated = [], []
    for s in collect_sessions():
        duration = max((s["end"] - s["start"]).total_seconds(), 0.0)
        guid = s["data"]["guid"]
        old = known.get(guid)
        if old is None:
            new.append({"timestamp": s["start"].isoformat(), "duration": duration,
                        "data": s["data"]})
        elif old["data"].get("ongoing") and (abs(old["duration"] - duration) > 1
                                             or not s["data"]["ongoing"]):
            old["duration"], old["data"] = duration, s["data"]
            updated.append(old)  # events carrying an `id` replace the stored ones
    batch = new + updated
    for i in range(0, len(batch), 500):
        api(base, "POST", f"/buckets/{bucket_id}/events", batch[i:i + 500])
    return len(new), len(updated)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="sync once and exit")
    p.add_argument("--interval", type=int, default=60, help="poll seconds")
    p.add_argument("--port", type=int, default=5600)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--dry-run", action="store_true",
                   help="print the sessions found and exit (no ActivityWatch needed)")
    a = p.parse_args()
    if a.dry_run:
        for s in sorted(collect_sessions(), key=lambda s: s["start"]):
            d = s["data"]
            print(f"{s['start']:%Y-%m-%d %H:%M:%S}Z  {(s['end'] - s['start']).total_seconds():8.0f}s  "
                  f"{d['direction']:8} {d['device_id']}"
                  f"{'  (ongoing)' if d.get('ongoing') else ''}")
        return

    base = f"http://{a.host}:{a.port}/api/0"
    hostname = socket.gethostname()
    bucket_id = f"{CLIENT}_{hostname}"
    while True:
        try:
            ensure_bucket(base, bucket_id, hostname)
            n_new, n_upd = sync(base, bucket_id)
            if n_new or n_upd:
                print(f"{datetime.now():%H:%M:%S} imported {n_new}, updated {n_upd}", flush=True)
        except (urllib.error.URLError, OSError) as e:
            print(f"ActivityWatch unreachable: {e}", flush=True)
            if a.once:
                raise SystemExit(1)
        if a.once:
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
