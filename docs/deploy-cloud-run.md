# Deploying to Cloud Run

Project `the-pocket-patient`, region `us-east1`. One image runs the API, the Celery
worker and migrations.

## What's deployed

| Resource | Name | Notes |
|---|---|---|
| API | Cloud Run service `pocket-patient-api` | public, min 0 / max 3 instances, VPC connector `pp-connector` |
| Worker + beat | Cloud Run worker pool `pocket-patient-worker` | exactly **1** instance (beat must not run twice), Direct VPC egress |
| Migrations | Cloud Run job `pp-migrate` | `alembic upgrade head` |
| Database | Cloud SQL `pocket-patient-db` (Postgres 16, db-f1-micro), db `pocket_patient` | ~25 max connections, so pools are capped (API 3+2 per instance, worker 2+1) |
| Redis | Memorystore `pocket-patient-redis` 10.224.88.251 | no AUTH/TLS |
| Uploads | GCS `the-pocket-patient-uploads` | |
| Runtime identity | `pocket-patient-run@the-pocket-patient.iam.gserviceaccount.com` | cloudsql.client, secret accessor (5 secrets), bucket objectAdmin, firebaseauth.admin, firebasecloudmessaging.admin |
| Secrets | `gemini-api-key` (Vertex express key, so `GEMINI_VERTEXAI=true`), `db-password`, `jwt-private-key`, `jwt-public-key`, `firebase-sa` | |

The API reads Firebase credentials from the `firebase-sa` secret mounted as a file.
The worker pool can't mount secret files yet, so it uses the runtime service account
through ADC (`FIREBASE_PROJECT_ID` only).

## Releasing an update

```bash
P=the-pocket-patient; C=$(git rev-parse --short HEAD)
IMG="us-east1-docker.pkg.dev/${P}/pocket-patient/api:${C}"   # zsh: keep the braces (${P}:u... is a modifier)

# 1. Build (Cloud Build, ~75s)
gcloud builds submit --project=$P --region=us-east1 --tag "$IMG" .

# 2. Migrate (only if alembic/versions changed). Migrations must stay
#    backward-compatible: old and new revisions overlap during rollout.
gcloud run jobs update pp-migrate --project=$P --region=us-east1 --image="$IMG"
gcloud run jobs execute pp-migrate --project=$P --region=us-east1 --wait

# 3. Roll out API and worker on the same image
gcloud run deploy pocket-patient-api --project=$P --region=us-east1 --image="$IMG"
gcloud run worker-pools deploy pocket-patient-worker --project=$P --region=us-east1 --image="$IMG"
```

`deploy` with only `--image` keeps every other setting (env, secrets, networking).

**Rollback:** `gcloud run services update-traffic pocket-patient-api --region=us-east1 --to-revisions=<previous-revision>=100`

**Startup safety:** in production the app refuses to start on missing or malformed
config (see `Settings.validate_for_runtime`), so a bad revision never receives traffic.

## Env vars (non-secret)

`APP_ENV=production`, `DATABASE_URL=postgresql+asyncpg://postgres@/pocket_patient?host=/cloudsql/the-pocket-patient:us-east1:pocket-patient-db`,
`REDIS_URL=redis://10.224.88.251:6379/0`, `FIREBASE_PROJECT_ID=the-pocket-patient`,
`FIREBASE_CREDENTIALS_PATH=/secrets/firebase/service-account.json` (API only),
`GCS_UPLOAD_BUCKET=the-pocket-patient-uploads`, `GEMINI_VERTEXAI=true`,
`TRUSTED_PROXY_COUNT=1` (verified: distinct client IPs get distinct rate-limit buckets),
`DB_POOL_SIZE` / `DB_MAX_OVERFLOW`.

Review window only: `ALLOW_NON_RUTGERS_ACCOUNTS=true` via
`gcloud run services update pocket-patient-api --region=us-east1 --update-env-vars=ALLOW_NON_RUTGERS_ACCOUNTS=true`.
Remove it after approval with `--remove-env-vars=ALLOW_NON_RUTGERS_ACCOUNTS`.

## Admin tasks (operator CLI)

The `pp-admin` Cloud Run job runs `python -m app.admin_cli` with the API image and DB access.
The account must have signed in to the app once.

```bash
P=the-pocket-patient
# make an admin (one-time bootstrap; afterwards admins can use the /admin API)
gcloud run jobs execute pp-admin --project=$P --region=us-east1 --wait --args="-m,app.admin_cli,make-admin,you@rutgers.edu"
# approve a professor (or: POST /api/v1/admin/users/{id}/verify-professor)
gcloud run jobs execute pp-admin --project=$P --region=us-east1 --wait --args="-m,app.admin_cli,verify-professor,prof@rutgers.edu"
gcloud run jobs execute pp-admin --project=$P --region=us-east1 --wait --args="-m,app.admin_cli,list-pending-professors"
# output:
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="pp-admin"' --project=$P --freshness=10m --format="value(textPayload)"
```

After each release, point the job at the new image too: `gcloud run jobs update pp-admin --project=$P --region=us-east1 --image="$IMG"`.
