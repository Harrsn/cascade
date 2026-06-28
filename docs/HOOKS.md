# Completion hooks

Cascade sorts and (optionally) cleans up a download when it finishes. Your
torrent client triggers this by running `python -m cascade.hook` on completion.
The hook figures out *what* finished from environment variables the client sets.

In Docker, the bundled Transmission is wired for you. For bare-metal or your own
client, set it up as below.

## Transmission

Transmission exports `TR_TORRENT_DIR`, `TR_TORRENT_NAME`, and `TR_TORRENT_ID`
automatically. In `settings.json` (stop the daemon first — it rewrites on exit):

```json
"script-torrent-done-enabled": true,
"script-torrent-done-filename": "/path/to/cascade-hook.sh"
```

Where `cascade-hook.sh` is:

```bash
#!/usr/bin/env bash
set -a; . /path/to/cascade/.env; set +a
exec python3 -m cascade.hook
```

## qBittorrent

Options → Downloads → **Run external program on torrent completion**:

```
/path/to/cascade-hook.sh "%F" "%I"
```

`%F` is the content path, `%I` is the hash. Map them in the wrapper:

```bash
#!/usr/bin/env bash
set -a; . /path/to/cascade/.env; set +a
export CASCADE_PATH="$1" CASCADE_ID="$2"
exec python3 -m cascade.hook
```

## Deluge

Install the **Execute** plugin, add a "Torrent Complete" command pointing at a
wrapper. Deluge passes `torrentid`, `torrentname`, `torrentpath` as arguments:

```bash
#!/usr/bin/env bash
set -a; . /path/to/cascade/.env; set +a
export CASCADE_ID="$1" CASCADE_NAME="$2" CASCADE_PATH="$3/$2"
exec python3 -m cascade.hook
```

## What the hook does

1. Runs the sorter (`cascade/sort.py`) on the completed path — renames and files
   it under `LIBRARY_ROOT/{movies,tvshows}`.
2. Appends an event to `EVENTS_FILE` (shown in the UI's Events tab).
3. Fires notifications per `NOTIFY_ON`.
4. If `REMOVE_ON_COMPLETE=1`, removes the torrent from the client (stops seeding).

If the sort fails, the torrent is **not** removed — you never lose a file that
didn't make it to the library.
