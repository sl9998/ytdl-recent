**ytdl_recent.py** — Download recent YouTube uploads (last 24h) from a list of channels.
---

Dependencies: Python 3.9+ stdlib + yt-dlp on PATH (or via --ytdlp-path).

## Configuration

- channels.txt : plain text file, one channel URL or handle per line
                 (e.g. https://www.youtube.com/@SomeChannel or @SomeChannel).
                 Blank lines and lines starting with '#' are ignored.
- State        : JSON file storing seen video IDs (default: ./state/seen_ids.json).
                 Also stores the last successful run timestamp for info.
- Output dir   : where yt-dlp saves files (default: ./downloads).

## Behavior

- For each channel, invokes yt-dlp with --dateafter to fetch only uploads
  from the last 24h, then re-checks video IDs against the state file so a
  video is never downloaded twice even if it appears in multiple runs.
- Skips channels with no recent uploads (logs at INFO).
- Non-zero yt-dlp exit codes are logged as warnings but do not abort the run;
  the script exits 0 if at least one channel was processed without a fatal
  error, otherwise 1.

## CLI

  --config PATH      channels file (default: ./channels.txt)
  --state PATH       state JSON file (default: ./state/seen_ids.json)
  --output PATH      download directory (default: ./downloads)
  --ytdlp-path CMD   yt-dlp executable (default: "yt-dlp")
  --dry-run          list what would be downloaded, don't download
  --log-file PATH    also write logs to this file (in addition to stderr)

## Examples

  ./ytdl_recent.py                       # manual run, defaults
  ./ytdl_recent.py --dry-run             # preview
  ./ytdl_recent.py --config /etc/ytdl/channels.txt --output /media/yt

## Scheduling

### Crontab (daily at 03:00)

  0 3 * * * /usr/local/bin/ytdl_recent.py --log-file /var/log/ytdl_recent.log

### systemd

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

## Assumptions

- yt-dlp is installed and on PATH (or passed via --ytdlp-path).
- The state file directory is created automatically.
- "Last 24 hours" is computed from the current wall-clock time; a slightly
  larger window (26h) is used for --dateafter to avoid clock/tz edge cases,
  with seen-ID tracking preventing duplicates.
- Filenames are produced by yt-dlp's default template (change via a yt-dlp
  config file if desired; this script does not pass -o).
