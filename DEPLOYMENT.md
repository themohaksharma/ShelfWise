# ShelfWise deployment guide — GitHub + Render

This guide prepares the current single-project ShelfWise app for Render. No GitHub push or public deployment has been performed.

## Architecture and deployment boundary

- ShelfWise is a same-origin Python HTTP server with HTML, CSS, and JavaScript frontend files. It uses Python's standard library; `requirements.txt` is intentionally dependency-free.
- Business records, login sessions, seasonal/competitor data, and profile photos are all stored in SQLite. Profile photos are SQLite BLOBs, so keeping the database on persistent storage also keeps photos persistent.
- The current backend uses SQLite-specific SQL throughout. A PostgreSQL migration would require a database-adapter and SQL rewrite, so this setup uses SQLite on a Render persistent disk instead.
- Use one paid Render web-service instance and one persistent disk. Do not scale this SQLite service to multiple instances. A Render disk is only durable under its mount path and is unavailable on free web services. This is a suitable hackathon deployment path, not a high-availability or multi-instance production architecture.
- The current server uses Python's standard-library `http.server`. It is useful for this existing project, but it has not had an independent production security review. Do not store real financial or customer data in a public launch until the server stack, abuse controls, backups, and operational security have been reviewed.
- `render.yaml` disables automatic deploys. You must deliberately trigger a deploy in Render after connecting the repository.

## Local checks before publishing

From the project root:

```bash
python3 -m py_compile server.py migrate_sqlite.py
PORT=8765 python3 server.py
```

Open `http://localhost:8765/`. The demo workspace remains at `http://localhost:8765/app?demo=1`.

Python dependencies: none beyond Python 3.10+ standard library. The browser still loads Google Fonts, QR-code generation, and ZXing barcode scanning from their configured external CDNs; those assets require network access in the visitor's browser.

## GitHub preparation

The repository should contain source and deployment files only. `.gitignore` excludes `.env` files, SQLite databases and backups, uploads, Python caches, and local logs. `.env.example` lists names without values. Never add a local database, a backup, a password, or a session secret to GitHub.

After reviewing `git status`, create a private GitHub repository (recommended while this is a prototype) and publish the prepared local `main` branch using the exact commands in the handoff. Do not push until the owner approves.

## Render Web Service configuration

The checked-in `render.yaml` describes:

| Setting | Value |
| --- | --- |
| Runtime | Python |
| Region | Singapore |
| Branch | `main` |
| Build command | `pip install -r requirements.txt` |
| Start command | `python server.py` |
| Health check | `/healthz` |
| Instances | 1 |
| Persistent disk | 1 GB mounted at `/var/data` |
| SQLite database | `/var/data/shelfwise.sqlite3` |
| Auto deploy | Off |

The service binds to `0.0.0.0` in production and reads Render's `PORT` value (falling back to `10000`). Local development defaults to `127.0.0.1`; set `PORT=8765` to retain the usual local address.

Render environment variables configured by the Blueprint:

| Name | Production value | Purpose |
| --- | --- | --- |
| `APP_ENV` | `production` | Selects production defaults |
| `SHELFWISE_HOST` | `0.0.0.0` | Render ingress binding |
| `SHELFWISE_DB_PATH` | `/var/data/shelfwise.sqlite3` | Database on persistent disk |
| `SESSION_COOKIE_SECURE` | `true` | Adds the Secure flag to session cookies |
| `PORT` | Render-provided | HTTP listen port; Render supplies this automatically |

There are no third-party backend credentials or API keys in the current source. Do not add values to `.env.example`; enter future secrets directly into Render's Environment settings.

### Connect Render after GitHub approval

1. In Render, choose **New → Blueprint** and connect the ShelfWise GitHub repository, or create a **Web Service** from the repository and copy the values above.
2. For a Blueprint, review the service and disk before confirming. The disk requires a paid web-service plan. Keep exactly one instance.
3. Confirm `APP_ENV`, `SHELFWISE_HOST`, `SHELFWISE_DB_PATH`, and `SESSION_COOKIE_SECURE` in the service environment.
4. Confirm the persistent disk is mounted at `/var/data` before treating the deployment as durable.
5. Manually trigger the first deploy. Check that `/healthz` returns `200` and `/` returns the ShelfWise homepage.
6. Test `/app?demo=1` and authentication flows using disposable accounts before announcing the URL.

## Existing SQLite data: backup and migration

The current local database was backed up with SQLite's online backup API. Its backup passed `PRAGMA integrity_check` (`ok`), and the table row counts matched the source at backup time. The private backup is outside the project directory and is excluded from Git. The source database was not reset or modified.

`migrate_sqlite.py` performs a verified SQLite backup copy and checks integrity, table names, and every table's row count. It refuses to overwrite an existing target by default. `--replace-demo-only` permits replacement only when the target has no registered users, sessions, profile photos, or non-demo shop records.

Do not transfer the private SQLite file through GitHub. To migrate existing local data before opening a newly seeded production service to users:

1. Make a fresh backup with SQLite's online backup API and record its SHA-256. Keep the backup private.
2. Create the Render service and its disk. The app seeds a demo workspace on first start. Before any real accounts are created, put the service into maintenance and ensure no one is changing its records.
3. Transfer the backup through a private, short-lived HTTPS object-storage URL. Encrypt the file before upload (for example, with OpenSSL) and never place the URL or encryption passphrase in Git, a public issue, or a shared log.
4. In the Render service shell, download and decrypt the file to a temporary location on the mounted disk. Compare its SHA-256 with the locally recorded value.
5. Run the migration helper from the checked-out service directory, targeting `/var/data/shelfwise.sqlite3`. Use `--replace-demo-only` only for the untouched initial demo database. The helper prints integrity and table-count verification and refuses unsafe overwrite cases.
6. Remove temporary encrypted/decrypted transfer files and expire the one-time object-storage URL. Bring the service out of maintenance and verify the migrated account and demo workspace.

The already-created local backup remains a private recovery copy; it has not been uploaded or migrated to Render because no Render service has been created.

## Backups and operations

- Take a verified SQLite backup before schema changes and on a regular schedule. Store backups in private, encrypted storage separate from the Render service.
- Monitor the `/healthz` check and Render logs.
- Never remove or recreate the persistent disk as a troubleshooting step.
- Render persistent disks are attached to a single instance, cannot be shared by scaled instances, and can interrupt service during deploys. Plan maintenance windows and verified backups.
- If the product needs multiple instances, high availability, or production-grade concurrent writes, migrate to managed PostgreSQL with a deliberate adapter/migration project before scaling.

## Public-release checklist

- [ ] Owner reviews and approves the source before any GitHub push.
- [ ] GitHub repository visibility and access are intentionally chosen.
- [ ] Render service is created only after approval.
- [ ] Persistent disk is attached and mount path is verified.
- [ ] Data backup and migration are verified before real user traffic.
- [ ] Login, registration, logout, sales, purchases, and stock updates are tested on the deployed origin.
- [ ] Profile-photo persistence and browser camera permission are tested over HTTPS.
- [ ] The production security limitations of the current standard-library HTTP server are reviewed before storing real customer or financial information.
