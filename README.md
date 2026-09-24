# FitLog

Fitness and food tracking website, for one person. No social features, no photos, no accounts beyond your own.

- **Food:** log what you eat against a daily calorie target. Protein, carbs and fat are tracked
  when the catalog has them. Quick-add covers anything that is not in the catalog.
- **Exercise:** each day gets a random set of exercises spread across types, with step-by-step
  instructions. The plan fits in a daily time limit (60 minutes by default). Nothing from your
  previous workout is picked again, unless too few exercises are checked to fill the day.
  Settings has a checkbox per exercise for what you can do right now.
- **Catalog in git:** foods and exercises are YAML files in [`data/`](data). Push a new file and
  the server pulls it in on its next sync.

## Catalog

One file per entry. The filename (without `.yaml`) is its id and must be unique. Subfolders are
only for organizing; use whatever folders you like.

### Foods: `data/foods/**/<id>.yaml`

```yaml
name: Banana                 # required
serving: 1 medium (118 g)    # required
calories: 105                # required, per serving
protein: 1.3                 # optional, grams
carbs: 27                    # optional
fat: 0.4                     # optional
tags: [fruit]                # optional, helps search
```

### Exercises: `data/exercises/**/<id>.yaml`

```yaml
name: Goblet Squat                       # required
type: strength                           # required: strength, cardio, mobility, core, balance
prescription: 3 sets of 10-12 reps       # optional
minutes: 6                               # optional, estimated time; defaults to 10
muscles: [quads, glutes, core]           # optional
equipment: [dumbbell or kettlebell]      # optional
steps:                                   # required, in order
  - Hold one dumbbell vertically against your chest.
  - Push your hips back and bend your knees to lower.
  - Drive through your whole foot to stand back up.
tips:                                    # optional
  - Keep your chest tall.
```

Check your edits before pushing (CI runs this too):

```bash
fitlog check data
```

Invalid files are skipped rather than crashing the app. They are listed on the Settings page.

The daily picker round-robins across exercise types, picking only what still fits in the time
left. A full game session counts against the time limit like anything else.

## Running locally

```bash
python3 -m venv .venv
.venv/bin/pip install --require-hashes --no-deps -r requirements-dev.txt
.venv/bin/pip install --no-deps --no-build-isolation -e .
export FITLOG_SECURE_COOKIES=0          # plain http on localhost
.venv/bin/fitlog user create me         # prompts for a password, shows a TOTP QR code
.venv/bin/fitlog serve                  # http://127.0.0.1:8000
.venv/bin/pytest
```

## Login and security

There is one user and no sign-up page. Accounts are managed from the command line only:

```bash
fitlog user create <name>           # once
fitlog user reset-password <name>   # signs out every session
fitlog user reset-totp <name>       # new authenticator secret; signs out every session
```

- Passwords are hashed with argon2id and must be at least 12 characters.
- Every login also needs a TOTP code from an authenticator app. A code cannot be reused.
- After 5 failed logins from one IP, or 20 from all IPs, logins are refused for 15 minutes.
- Sessions are random tokens, stored hashed on the server. The cookie is `HttpOnly`, `Secure`
  and `SameSite=Strict`, and the session lasts 14 days.
- Every state-changing request needs a per-session CSRF token and must come from the same
  origin.
- The CSP allows only same-origin scripts and styles. htmx is served from the app, not a CDN.

## Configuration

| Variable | Default | |
|---|---|---|
| `FITLOG_DB` | `var/fitlog.db` | SQLite database |
| `FITLOG_CATALOG_DIR` | `data` | Folder holding `foods/` and `exercises/` |
| `FITLOG_CATALOG_REPO` | unset | Git working tree to `pull --ff-only` before each reload |
| `FITLOG_CATALOG_URL` | unset | Clone the catalog from here into `FITLOG_CATALOG_REPO` on first start |
| `FITLOG_CATALOG_BRANCH` | `main` | Branch to clone |
| `FITLOG_PULL_MINUTES` | `10` | Sync interval; `0` disables it (Settings has a Sync now button) |
| `FITLOG_TIMEZONE` | `America/Los_Angeles` | Decides when "today" rolls over |
| `FITLOG_SECURE_COOKIES` | `1` | Set `0` only for plain-http local use |
| `FITLOG_TRUST_PROXY` | `0` | Use nginx's `X-Real-IP` for login rate limiting |
| `FITLOG_SESSION_DAYS` | `14` | Session lifetime |

## Deploying

[`compose.yaml`](compose.yaml) runs the app behind a reverse proxy on `127.0.0.1:8000`, with two
named volumes: `state` holds the database, and `catalog` is a clone of this repo. The app clones
the catalog on first start and pulls it on a timer. The image creates both mount points owned by
its user, so the app can write them even when the Docker daemon remaps container uids.

```bash
docker compose up -d --build
docker compose exec fitlog fitlog user create me
```

Code changes need a rebuild. Catalog changes only need a push.

## Dependencies

The app's dependencies are listed in `requirements.in`, which `pyproject.toml` reads.
`requirements-dev.in` adds the test tools. Each compiles to a lock file that pins every package with
hashes:

- **`requirements.txt`:** the app. The Docker image installs it with `--require-hashes` and runs
  the app from source.
- **`requirements-dev.txt`:** the app plus the test tools. CI installs it the same way.

The image's base is pinned by digest. Dependabot updates the locks, the base image and the pinned
GitHub Actions weekly, waiting 7 days after an upstream release before proposing it.

After editing either `.in` file, regenerate both locks:

```bash
pip install pip-tools
pip-compile --generate-hashes --allow-unsafe --strip-extras -o requirements.txt requirements.in
pip-compile --generate-hashes --allow-unsafe --strip-extras -o requirements-dev.txt requirements-dev.in
```

## CI

The workflows in `.github/workflows` call the shared ones in
[NearlyTRex/Workflows](https://github.com/NearlyTRex/Workflows), pinned to a release. Dependabot
moves the pin forward when that library releases.

- **ci:** installs from `requirements-dev.txt` and runs ruff and `fitlog check data`. Then it runs
  the tests, which fail below 100% line and branch coverage. Shellcheck and a JSON check also run.
- **security:** runs on pushes and PRs, and weekly:
  - zizmor over the workflows
  - gitleaks over the git history
  - pip-audit over both locks
  - CodeQL for Python and the workflows
  - a Trivy scan of the Docker image
  - dependency review on PRs

## Releasing

The version in `pyproject.toml` is the only place a version is written. Never tag by hand.

1. On GitHub, open **Actions → prepare release → Run workflow**. Enter `patch`, `minor`, `major`,
   or an exact version like `1.4.0`. It bumps `pyproject.toml` on a `release/vX.Y.Z` branch and
   opens a PR.
2. Merge the PR. **release** notices that the version on `main` has no tag, runs the checks,
   then tags `vX.Y.Z` and publishes a GitHub Release with generated notes.

A push to `main` that doesn't change the version finds the tag already there and releases
nothing.

PRs opened by the workflow don't trigger `ci` (GitHub doesn't run workflows for events made with
the workflow token). The checks run inside **release** before anything is tagged.
