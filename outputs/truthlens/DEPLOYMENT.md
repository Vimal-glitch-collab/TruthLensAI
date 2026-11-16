# Vercel frontend and Render backend

The GitHub repository keeps the application in `outputs/truthlens`. Set that directory as the Vercel project root. The root Render Blueprint also points to it. Local standalone checkouts can use this directory's `render.yaml`.

## Architecture

Vercel serves only the files in `web/`. `node tools/build_frontend.mjs` produces Vercel Build Output API v3 assets and an external `/api/*` rewrite to `TRUTHLENS_BACKEND_ORIGIN`. API responses are never cached. Uploads and report downloads go through the external rewrite, not a size-limited serverless function. Cookies remain first-party, HttpOnly, Secure and SameSite=Strict.

Render runs the Python server and isolated analysis workers. Set `TRUTHLENS_PUBLIC_ORIGIN` to the exact HTTPS Vercel production origin, without a path. Render's own `RENDER_EXTERNAL_URL` is also accepted as a host. Other browser origins remain rejected. Arbitrary Vercel preview domains are deliberately not trusted.

## Render

The Blueprint proposes a Singapore `1c-2g` service (2 GB RAM), one analysis worker, and a 1 GB persistent disk mounted at `/var/data`. This is paid infrastructure; review the plan and current Render prices before applying it. Free ephemeral storage is unsuitable for durable account/report history.

Build: `pip install -r requirements.txt -r requirements-models.txt && python setup_models.py`

Start: `python server.py`. Render supplies `PORT`; bind to `0.0.0.0`. `/healthz` is a lightweight platform health check. `/api/health` checks actual detector availability. Both pinned model files are downloaded/exported and verified during builds; no uploaded media is committed.

The Blueprint enables public account registration. Every account keeps a separate report history. To operate an invitation-only installation, set `TRUTHLENS_ALLOW_REGISTRATION=0` and run `python create_user.py` from the Render shell. Optional online claim lookup is disabled per analysis unless the user opts in.

## Vercel

1. Import the GitHub repository and set Root Directory to `outputs/truthlens` and Framework to Other.
2. Set `TRUTHLENS_BACKEND_ORIGIN` to the actual Render HTTPS service origin.
3. Deploy. No frontend packages or runtime serverless functions are needed.
4. Set the Render `TRUTHLENS_PUBLIC_ORIGIN` to the actual Vercel production origin.

Without a backend origin the frontend still builds, but `/api/*` explicitly returns 503. That is a frontend-only deployment, not a working analysis service.

## Release checks

Run `python -m unittest discover -s tests -v`, `node --test tests/test_frontend_build.mjs`, and `node --check web/app.js`. Test login, image submission, polling, report exports, batch cancellation and account isolation before declaring the hosted application operational. Deployment does not establish the classifier's accuracy; stored benchmarks are accepted only when their model/configuration/pipeline checksums match.
