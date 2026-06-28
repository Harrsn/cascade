# First-run setup

Cascade needs two things before it can do anything: an **indexer API key** and a
reachable **download client**. The `/api/config` endpoint reports whether these
are present (`configured: true/false`), and `/health` checks live reachability.

1. **Indexer** — open Jackett (`:9117` in the default compose), add indexers,
   copy the API key into `JACKETT_API_KEY`.
2. **Client** — the bundled Transmission works out of the box with the
   `CLIENT_USER`/`CLIENT_PASS` from your `.env`. Using your own? Set
   `DOWNLOAD_CLIENT`, `CLIENT_URL`, and creds.
3. Restart, hit `/health`, confirm both read `reachable`.

The UI surfaces this state in its stats bar and will show a banner when the app
isn't fully configured yet.
