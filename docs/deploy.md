# Deploying UATPilot

One Docker image serves the API, the static dashboard, and a headless Chromium for UI tests.

## Render (Blueprint)

1. Push the repo to GitHub.
2. Render dashboard → **New → Blueprint** → pick the repo. `render.yaml` creates one web service
   with a 1 GB disk mounted at `/data` (the SQLite run history and failure screenshots live there).
   A persistent disk needs a paid instance; on the free plan, drop the `disk:` block and accept
   that history resets on every deploy (the two seed suites are re-imported on start).
3. In the service's **Environment** tab set:
   - `UATPILOT_WRITE_TOKEN`: a long random string. With it set, visitors see a read-only
     dashboard; you paste the token into the dashboard's token box to run suites.
   - `ANTHROPIC_API_KEY` (optional): enables generation and Claude triage.
4. After the first deploy, open the dashboard, paste the token, and press **Run suite** on each
   seed suite so the trend chart has data.

## Anywhere else

```bash
docker build -t uatpilot .
docker run -p 8000:8000 -v uatpilot-data:/data -e UATPILOT_WRITE_TOKEN=change-me uatpilot
```

## Safety notes

- The runner only talks to hosts in `UATPILOT_ALLOWED_HOSTS` (the two public practice sandboxes
  and localhost by default). This blocks SSRF from a deployed instance and stops the tool being
  pointed at someone else's production system.
- Requests to a sandbox are spaced at least `UATPILOT_MIN_REQUEST_INTERVAL` seconds apart, and
  only one suite runs at a time.
- The per-IP write limit sees the load balancer's IP behind Render's proxy, so treat it as a global
  limit there; the write token is the real gate.
