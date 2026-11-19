# ShelfWise source review

ShelfWise is a single-origin shop management web app for small retailers. The public homepage is at `/`; the shop workspace is at `/app`.

## Run locally

Requires Python 3.10+ and a modern browser. The server uses Python's standard library.

```bash
PORT=8765 python3 server.py
```

Then open `http://localhost:8765/`. The demo workspace is available at `http://localhost:8765/app?demo=1`.

The SQLite database is generated at runtime and is intentionally not included in the source repository. Demo records are seeded by the server. See `DEPLOYMENT.md` for GitHub/Render setup and persistent-disk data migration instructions.

## Main files

- `index.html` — public marketing homepage
- `server.py` — local web server, SQLite schema, APIs, authentication, and demo seeding
- `store.js` — shared client-side workspace and API service
- `auth.js`, `auth-pages.js`, `auth.css` — login and registration flow
- `app/app.js`, `app/index.html` — shop application and UI
- `login/index.html`, `register/index.html` — authentication routes
