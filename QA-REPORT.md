# ShelfWise deployment preparation QA

This report records checks actually performed for the GitHub/Render preparation. No GitHub push or Render deployment was performed.

## Project and data inspection

| Check | Result |
| --- | --- |
| Actual running project root | `/Users/mohak/Documents/ShelfWise` (confirmed from process working directory) |
| Existing server | PID 12300, `python server.py`, listening at `127.0.0.1:8765`; left running |
| Updated-code smoke server | Started separately on `127.0.0.1:18765` with a temporary SQLite file and stopped gracefully after checks |
| Framework/dependencies | Python standard-library HTTP server; no third-party Python dependencies found in imports |
| Frontend assets | Root/app HTML and JS; browser libraries/fonts are loaded from external CDNs |
| Database | SQLite; schema includes users, shops, sessions, inventory, sales, purchases, payments, expenses, competitor prices, seasonal events, price history, and profile-photo BLOBs |
| Upload persistence | Profile photos are stored as BLOBs in SQLite; there is no separate upload directory |
| Environment secrets | No `.env` file found in the project; no backend API credentials identified in inspected source |
| Git before preparation | No Git repository existed |

## Backup verification

A private copy was created outside the project directory at `/Users/mohak/Documents/ShelfWise-pre-render-20261120.sqlite3`. It is mode `0600`, 339,968 bytes, and was checked with SQLite `PRAGMA integrity_check` (`ok`). Its SHA-256 is `6a158021ae4194495cd2e5e89b1272283ad9906e7ebc61be13bacf895cc6375f`. Source and backup table names/counts matched at creation. The backup was not added to the project or Git.

## Deployment changes prepared

| Change | Result |
| --- | --- |
| Configurable SQLite path | Implemented using `SHELFWISE_DB_PATH`; parent directory is created when opening the database |
| Port and bind address | Default port is 10000; local default bind remains `127.0.0.1`; production binds `0.0.0.0` via `APP_ENV`/`SHELFWISE_HOST` |
| Session cookies | Production `Secure`, `HttpOnly`, `SameSite=Lax`; local development remains compatible with HTTP |
| Security headers | Added content-type, frame, referrer, camera permission headers; HSTS only when secure-cookie mode is enabled |
| Health endpoint | Added `/healthz`, which runs `SELECT 1` and returns 503 on SQLite failure |
| Render configuration | `render.yaml` sets one Python web service, health path, environment, Singapore region, disabled auto-deploy, and 1 GB `/var/data` disk |
| Git exclusions | `.gitignore` excludes secrets, databases, backups, uploads, caches, and local logs |
| Python packages | `requirements.txt` documents that no third-party Python packages are required |
| Data transfer | Added `migrate_sqlite.py` with integrity, schema, row-count checks and safe target overwrite rules |

## Tests performed

| Test | Result |
| --- | --- |
| Existing project database integrity | Passed (`ok`) |
| Private online backup integrity | Passed (`ok`) |
| Source/backup table and row-count comparison | Passed |
| Existing server identity and listening port | Passed; left untouched |
| Updated server syntax | Passed: `python3 -m py_compile server.py migrate_sqlite.py` |
| Dependency installation step | Passed: `python3 -m pip install -r requirements.txt` (no external packages required) |
| Render Blueprint syntax/key fields | Passed: parsed `render.yaml` as YAML and checked service count, health path, and disk mount |
| SQLite migration helper | Passed: copied the verified backup into a temporary target, validated integrity/table counts, and safely replaced a seeded demo-only target; test copies were temporary |
| Route and static-file HTTP smoke test | Passed: 22 GET routes/assets returned 200, including homepage, login/register, 15 demo app paths, and `/healthz` |
| Demo workspace API | Passed: returned 8 products and 270 historical sales from isolated test database |
| Protected route and private database exposure | Passed: unauthenticated `/app` redirected; direct SQLite path returned 404 |
| Registration/session/logout | Passed through HTTP API on isolated database; response cookie had `Secure`, `HttpOnly`, and `SameSite=Lax` in production mode |
| Stock receipt and sale | Passed through HTTP API on disposable account: received 10 units, sold 2, then confirmed 8 units and one sale/receipt record |
| SQLite persistence after server restart | Passed on the isolated temporary database: the test shop still had 8 units, one sale, and one receipt after restarting the updated server |
| Competitor and seasonal-event endpoints | Passed through HTTP API on disposable account |
| Full browser route/visual test suite | Not performed in this deployment-preparation pass |
| Login with incorrect credentials and duplicate account UI states | Not performed |
| Customer payment and profile image upload persistence after server restart | Not performed |
| Render build, health check, mounted-disk persistence | Not performed; no Render service exists yet |
| GitHub push | Not performed; awaiting owner approval |

## Remaining checks before public use

- Run `python3 -m py_compile server.py migrate_sqlite.py` and test `/healthz` after the final local changes.
- Test all public and application routes and transaction flows on a disposable environment.
- Test database and photo persistence against the Render mounted disk after deployment.
- The existing backend remains a Python standard-library `http.server` application and has not received an independent production security review. Do not treat this preparation as a security certification or as proof that it is ready for real financial/customer data.
