# Production Deployment

## Required Environment

Copy `.env.production.example` to your production environment manager and set real values.
Do not commit production secrets.

Required security settings:

- `APP_ENV=production`
- `REQUIRE_API_KEY=true`
- `PO_EXTRACTOR_API_KEY=<long-random-secret>`
- `ENABLE_DEBUG_RESPONSES=false`
- `ALLOWED_ORIGINS=<only trusted frontend origins>`

## Windows API Server

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:APP_ENV="production"
$env:PO_EXTRACTOR_API_KEY="replace-with-a-long-random-secret"
python deploy_waitress.py
```

## Linux API Server

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export APP_ENV=production
export PO_EXTRACTOR_API_KEY="replace-with-a-long-random-secret"
gunicorn "wsgi:app" --bind 127.0.0.1:5000 --workers 2 --threads 4 --timeout 180
```

## Frontend Server

Start the static frontend with the same API key so browser requests include `X-API-Key`.

```powershell
cd frontend/js
$env:PO_EXTRACTOR_API_KEY="replace-with-a-long-random-secret"
$env:BACKEND_URL="http://127.0.0.1:5000"
npm run dev
```

For public access, put both services behind a reverse proxy with HTTPS and do not expose
the Flask API directly to the internet without real user authentication.
