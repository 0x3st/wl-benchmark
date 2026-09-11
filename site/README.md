# WL-Benchmark platform (Cloudflare Worker)

One-time deployment. The Worker is the whole platform: it receives runs
over HTTP, stores them in KV, and serves the public benchmark site
(index + one shareable page per run). No database, no build step.

## Deploy

```bash
cd site

# 1. create the KV namespace and paste its id into wrangler.toml
npx wrangler kv namespace create RUNS

# 2. (optional) serve your own domain — uncomment the routes block in
#    wrangler.toml and make sure the zone (e.g. wulei.org) is on the
#    same Cloudflare account:
#      routes = [{ pattern = "benchmark.wulei.org", custom_domain = true }]

# 3. set the upload token (any long random string) and deploy
npx wrangler secret put UPLOAD_TOKEN
npx wrangler deploy
```

## Configure the local CLI

```bash
# either environment variables
export WL_BENCH_URL=https://benchmark.wulei.org
export WL_BENCH_TOKEN=<the same UPLOAD_TOKEN>

# or config/site.json (gitignored, interactive prompt offered on first run)
{ "url": "https://benchmark.wulei.org", "token": "..." }
```

After that, `wlb run` uploads every finished run automatically, prints
the share link (`<site>/r/<run-id>`) and deletes the local run data.
`wlb publish <run-dir>` retries a failed upload; `wlb run --no-upload`
skips the platform entirely.

## API

| method | path         | auth              | body / response                  |
|--------|--------------|-------------------|----------------------------------|
| GET    | `/`          | —                 | run index (HTML)                 |
| GET    | `/r/<id>`    | —                 | one run page (HTML)              |
| GET    | `/api/runs`  | —                 | `{ ok, runs: [meta...] }`        |
| POST   | `/api/runs`  | `Bearer <token>`  | `{ id, meta, html }` → `{ ok, url }` |

Re-uploading a run id overwrites it (idempotent retry).
