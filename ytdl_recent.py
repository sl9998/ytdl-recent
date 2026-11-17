#!/usr/bin/env python3
"""
ytdl_recent.py — Download recent YouTube uploads (last 24h) from a list of channels.

Dependencies: Python 3.9+ stdlib + yt-dlp on PATH (or via --ytdlp-path).

Configuration
-------------
- channels.txt : plain text file, one channel URL or handle per line
                 (e.g. https://www.youtube.com/@SomeChannel or @SomeChannel).
                 Blank lines and lines starting with '#' are ignored.
- State        : JSON file storing seen video IDs (default: ./state/seen_ids.json).
                 Also stores the last successful run timestamp for info.
- Output dir   : where yt-dlp saves files (default: ./downloads).

Behavior
--------
- For each channel, invokes yt-dlp with --dateafter to fetch only uploads
  from the last 24h, then re-checks video IDs against the state file so a
  video is never downloaded twice even if it appears in multiple runs.
- Skips channels with no recent uploads (logs at INFO).
- Non-zero yt-dlp exit codes are logged as warnings but do not abort the run;
  the script exits 0 if at least one channel was processed without a fatal
  error, otherwise 1.

CLI
---
  --config PATH      channels file (default: ./channels.txt)
  --state PATH       state JSON file (default: ./state/seen_ids.json)
  --output PATH      download directory (default: ./downloads)
  --ytdlp-path CMD   yt-dlp executable (default: "yt-dlp")
  --dry-run          list what would be downloaded, don't download
  --log-file PATH    also write logs to this file (in addition to stderr)

Examples
--------
  ./ytdl_recent.py                       # manual run, defaults
  ./ytdl_recent.py --dry-run             # preview
  ./ytdl_recent.py --config /etc/ytdl/channels.txt --output /media/yt

Crontab (daily at 03:00)
------------------------
  0 3 * * * /usr/local/bin/ytdl_recent.py --log-file /var/log/ytdl_recent.log

systemd
-------
  # /etc/systemd/system/ytdl-recent.service
  [Unit]
  Description=Download recent YouTube uploads
  After=network-online.target
  Wants=network-online.target

  [Service]
  Type=oneshot
  ExecStart=/usr/local/bin/ytdl_recent.py \
      --config /etc/ytdl/channels.txt \
      --state  /var/lib/ytdl/seen_ids.json \
      --output /media/youtube \
      --log-file /var/log/ytdl_recent.log

  # /etc/systemd/system/ytdl-recent.timer
  [Unit]
  Description=Daily run of ytdl-recent

  [Timer]
  OnCalendar=daily
  Persistent=true

  [Install]
  WantedBy=timers.target

  # enable:
  #   systemctl daemon-reload
  #   systemctl enable --now ytdl-recent.timer

Assumptions
-----------
- yt-dlp is installed and on PATH (or passed via --ytdlp-path).
- The state file directory is created automatically.
- "Last 24 hours" is computed from the current wall-clock time; a slightly
  larger window (26h) is used for --dateafter to avoid clock/tz edge cases,
  with seen-ID tracking preventing duplicates.
- Filenames are produced by yt-dlp's default template (change via a yt-dlp
  config file if desired; this script does not pass -o).
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG = logging.getLogger("ytdl_recent")

# A little slack on the yt-dlp --dateafter window; seen-ID tracking handles
# the resulting overlap so nothing is downloaded twice.
DATE_WINDOW_HOURS = 26


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download recent YouTube uploads.")
    p.add_argument("--config", default="channels.txt", type=Path,
                   help="channels file (default: ./channels.txt)")
    p.add_argument("--state", default=Path("state/seen_ids.json"), type=Path,
                   help="state JSON file (default: ./state/seen_ids.json)")
    p.add_argument("--output", default=Path("downloads"), type=Path,
                   help="download directory (default: ./downloads)")
    p.add_argument("--ytdlp-path", default="yt-dlp",
                   help="yt-dlp executable (default: yt-dlp)")
    p.add_argument("--dry-run", action="store_true",
                   help="list what would be downloaded, do not download")
    p.add_argument("--log-file", type=Path, default=None,
                   help="also log to this file")
    return p.parse_args(argv)


def setup_logging(log_file: Path | None) -> None:
    fmt = "%(asctime)s %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


def read_channels(path: Path) -> list[str]:
    if not path.exists():
        LOG.error("channels file not found: %s", path)
        return []
    channels: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        channels.append(line)
    return channels


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"seen_ids": [], "last_run": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        LOG.warning("could not read state file %s (%s); starting fresh", path, exc)
        return {"seen_ids": [], "last_run": None}
    data.setdefault("seen_ids", [])
    data.setdefault("last_run", None)
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_channel_video_ids(ytdlp: str, channel: str) -> list[tuple[str, str]]:
    """
    Return [(video_id, title_or_url), ...] for uploads within DATE_WINDOW_HOURS.
    Uses yt-dlp --flat-playlist to avoid downloading anything here.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=DATE_WINDOW_HOURS)
    dateafter = cutoff.strftime("%Y%m%d")

    cmd = [
        ytdlp,
        "--flat-playlist",
        "--print", "%(id)s\t%(title)s",
        "--dateafter", dateafter,
        "--no-warnings",
        channel,
    ]
    LOG.info("listing recent uploads for %s (since %s)", channel, dateafter)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        LOG.error("yt-dlp executable not found: %s", ytdlp)
        return []

    if proc.returncode != 0:
        LOG.warning("yt-dlp listing failed for %s (exit %d): %s",
                    channel, proc.returncode, proc.stderr.strip()[:500])
        return []

    results: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        vid, title = line.split("\t", 1)
        vid = vid.strip()
        if vid:
            results.append((vid, title.strip()))
    return results


def download_video(ytdlp: str, video_id: str, outdir: Path, dry_run: bool) -> bool:
    url = f"https://www.youtube.com/watch?v={video_id}"
    if dry_run:
        LOG.info("[dry-run] would download %s", url)
        return True
    cmd = [ytdlp, "-P", str(outdir), url]
    LOG.info("downloading %s", url)
    try:
        proc = subprocess.run(cmd, check=False)
    except FileNotFoundError:
        LOG.error("yt-dlp executable not found: %s", ytdlp)
        return False
    if proc.returncode != 0:
        LOG.warning("download failed for %s (exit %d)", url, proc.returncode)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_file)

    channels = read_channels(args.config)
    if not channels:
        LOG.error("no channels to process (config: %s)", args.config)
        return 1

    state = load_state(args.state)
    seen: set[str] = set(state["seen_ids"])

    args.output.mkdir(parents=True, exist_ok=True)

    total_new = 0
    channel_errors = 0

    for channel in channels:
        try:
            videos = list_channel_video_ids(args.ytdlp_path, channel)
        except Exception as exc:  # defensive: keep the loop going
            LOG.warning("unexpected error listing %s: %s", channel, exc)
            channel_errors += 1
            continue

        if not videos:
            LOG.info("no recent uploads for %s", channel)
            continue

        new_for_channel = 0
        for vid, title in videos:
            if vid in seen:
                LOG.debug("already seen %s (%s)", vid, title)
                continue
            ok = download_video(args.ytdlp_path, vid, args.output, args.dry_run)
            if ok:
                seen.add(vid)
                new_for_channel += 1
                total_new += 1
                if not args.dry_run:
                    # persist incrementally so a crash doesn't re-download
                    state["seen_ids"] = sorted(seen)
                    save_state(args.state, state)
            else:
                channel_errors += 1

        LOG.info("%s: %d new video(s)", channel, new_for_channel)

    state["seen_ids"] = sorted(seen)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    if not args.dry_run:
        save_state(args.state, state)

    LOG.info("done: %d new video(s) total", total_new)
    if args.dry_run:
        LOG.info("(dry-run: state not modified)")

    # Sensible exit code: fail only if every channel errored and nothing ran.
    if channel_errors and total_new == 0 and len(channels) == channel_errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())