<div align="center">

```
 ██████╗ ██╗   ██╗██████╗  ██████╗ ███████╗██████╗     ██╗   ██╗██████╗
 ██╔══██╗██║   ██║██╔══██╗██╔════╝ ██╔════╝██╔══██╗    ██║   ██║╚════██╗
 ██████╔╝██║   ██║██████╔╝██║  ███╗█████╗  ██████╔╝    ██║   ██║ █████╔╝
 ██╔═══╝ ██║   ██║██╔══██╗██║   ██║██╔══╝  ██╔══██╗    ╚██╗ ██╔╝██╔═══╝
 ██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗██║  ██║     ╚████╔╝ ███████╗
 ╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝      ╚═══╝  ╚══════╝
```

**Production-grade, self-healing log archiver for Linux servers**

Compress → Upload to S3 → Delete locally — automatically, reliably, with zero silent data loss.

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-2.0.0-orange)]()
[![Shell](https://img.shields.io/badge/shell-bash%205%2B-lightgrey?logo=gnu-bash)]()

</div>

---

## What is Log Purger?

Log Purger solves a specific, painful production problem: **servers that fill up with log files**.

When a server's disk hits a threshold (default 75%), Log Purger wakes up, compresses every log file it finds, ships them to S3, and deletes the originals — reclaiming disk space automatically without any manual intervention.

It is designed to be **set and forgotten**. Once deployed and scheduled, it runs silently every day, emails you only when something needs attention, and automatically retries any upload that failed during the previous run.

```
BEFORE Log Purger                     AFTER Log Purger
──────────────────────────────        ──────────────────────────────────────
/var/log/nginx/                       /var/log/nginx/        S3 Bucket
├── access.log          1.20 GB        └── (empty)            ├── project/
├── access.log.1         0.98 GB   →                           │   └── nginx/
├── access.log.2         0.87 GB        Disk: 23% used         │       └── 13012025_0200/
├── access.log.3         0.91 GB        Free: 77 GB            │           ├── web-01/
└── ...                               ✅ Disk alert cleared  │           │   ├── access.log.gz
Disk: 82% used  ⚠️                                           │           │   └── ...
                                                              └── ...
```

---

## Table of Contents

- [How It Works](#how-it-works)
- [Repo Structure](#repo-structure)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration Guide](#configuration-guide)
  - [purge\_config.json — all options](#purge_configjson--all-options)
  - [Credentials — the right way](#credentials--the-right-way)
  - [.purge\_secrets — SMTP setup](#purge_secrets--smtp-setup)
- [Running the Script](#running-the-script)
  - [Manual run](#manual-run)
  - [CLI reference](#cli-reference)
  - [Dry run — always do this first](#dry-run--always-do-this-first)
- [Scheduling with Cron](#scheduling-with-cron)
  - [Cron wrapper behaviour](#cron-wrapper-behaviour)
  - [Cron environment variables](#cron-environment-variables)
  - [Email notifications](#email-notifications)
  - [Gmail SMTP setup](#gmail-smtp-setup)
- [Self-Healing System](#self-healing-system)
  - [Dead-Letter Queue](#dead-letter-queue)
  - [Retry with exponential backoff](#retry-with-exponential-backoff)
  - [All failure modes](#all-failure-modes)
- [Understanding the Logs](#understanding-the-logs)
- [S3 Object Layout](#s3-object-layout)
- [Exit Codes](#exit-codes)
- [Security](#security)
- [Upgrading from v1](#upgrading-from-v1)
- [Troubleshooting](#troubleshooting)
- [Contributors](#contributors)
- [License](#license)

---

## How It Works

The system has two layers that work together:

```
┌────────────────────────────────────────────────────────────────────────────┐
│                        LAYER 1 — CRON WRAPPER                             │
│                         purge_crontab.sh                                  │
│                                                                            │
│  Every scheduled run:                                                      │
│  1. Rotate old cron logs (keep last LOG_KEEP_DAYS days)                   │
│  2. Check all dependencies (python3, curl, jq, timeout)                   │
│  3. Acquire lock file → abort if another run is already active            │
│  4. Read disk usage via df -P (POSIX, arithmetic-safe)                    │
│  5. If usage < CRITICAL_USAGE (75%) → exit 0, nothing to do              │
│  6. Call: python3 purge_v2.py --config ... --replay-dlq                   │
│  7. Re-check disk after purge → escalate if still ≥ 95%                  │
│  8. Send email: ✅ success / ⚠️ partial / ❌ fatal / 🚨 timeout           │
│  9. Release lock file (trap EXIT — always runs, even on crash)            │
└────────────────────────────┬───────────────────────────────────────────────┘
                             │ python3 purge_v2.py --replay-dlq
                             ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                        LAYER 2 — PYTHON ENGINE                            │
│                          purge_v2.py                                      │
│                                                                            │
│  Startup:                                                                  │
│  1. Load & validate purge_config.json (JSON Schema)                       │
│  2. Build boto3 S3 client → HeadBucket connectivity check                │
│  3. Drain DLQ → retry all previously failed uploads first                 │
│                                                                            │
│  For each enabled service (parallel, up to max_workers threads):          │
│  ┌──────────────────────────────────────────────────────────────────┐     │
│  │  For each file matching the pattern in log_path:                 │     │
│  │                                                                  │     │
│  │  ① Disk pre-check    → skip if free space < min_free_mb         │     │
│  │  ② Compress          → gzip level 9, pure Python stdlib         │     │
│  │  ③ Upload to S3      → boto3, AES256 SSE, object tags           │     │
│  │     └─ on failure    → retry (exponential backoff)              │     │
│  │     └─ all retries   → write to Dead-Letter Queue               │     │
│  │  ④ Verify integrity  → HeadObject ETag check                    │     │
│  │  ⑤ Delete local      → ONLY after confirmed upload + ETag match │     │
│  └──────────────────────────────────────────────────────────────────┘     │
│                                                                            │
│  Print run summary: total / succeeded / skipped / failed / duration       │
└────────────────────────────────────────────────────────────────────────────┘
```

### The Golden Rule

> **A local file is never deleted unless S3 confirms receipt and the ETag matches the local file.**

This was the most critical bug in v1 — files were deleted regardless of whether the upload succeeded. v2 makes silent data loss physically impossible.

---

## Repo Structure

After cloning this repository, you will find the following structure ready to use:

```
log-purger/                        ← repo root  (git clone lands here)
│
├── purge_crontab.sh               ← bash cron wrapper     MUST be at root
├── requirements.txt               ← Python dependencies
├── .purge_secrets.example         ← credentials template  (safe to commit)
├── .gitignore                     ← excludes runtime dirs and .purge_secrets
├── README.md
│
└── purger/                        ← subdirectory  (purge_crontab.sh expects this name)
    ├── purge_v2.py                ← main Python engine
    └── purge_config.json          ← your configuration   EDIT THIS
```

At runtime, the scripts auto-create these directories. Do **not** create them manually or commit them:

```
log-purger/
└── purger/
    └── purge/                      ← auto-created on first run
        ├── logs/
        │   ├── purge.log           ← rotating Python log (10 MB × 5 files)
        │   ├── purge.log.1
        │   └── purge.log.2
        ├── dlq/
        │   └── dead_letters.jsonl  ← failed uploads, auto-replayed next run
        └── data/
    └── cron_logs/                  ← auto-created by purge_crontab.sh
        ├── cron_log_2025-01-13.log
        ├── cron_log_2025-01-14.log
        └── ...                     ← kept for LOG_KEEP_DAYS (default 14), then deleted
```

### Why purge_crontab.sh lives at the root

The wrapper resolves all paths relative to itself at runtime:

```bash
SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
PURGE_SCRIPT="${SCRIPT_DIR}/purger/purge_v2.py"
CONFIG_FILE="${SCRIPT_DIR}/purger/purge_config.json"
```

This means you can deploy the repo anywhere (`/opt/log-purger`, `/home/ubuntu/log-purger`, etc.) and every path resolves correctly — no editing required.

---

## Requirements

### Python — for purge_v2.py

| Package | Required | Purpose |
|---------|----------|---------|
| `boto3` | **Yes** | S3 uploads via AWS SDK — replaces the `aws` CLI |
| `jsonschema` | Recommended | Validates `purge_config.json` on startup; skipped gracefully if absent |
| `colorama` | Optional | Coloured terminal output; skipped gracefully if absent |

Requires **Python 3.8 or higher**.

### System tools — for purge_crontab.sh

| Tool | Required | Purpose |
|------|----------|---------|
| `curl` | **Yes** | Sends SMTP email notifications |
| `jq` | **Yes** | Reads service list from config for the email summary |
| `timeout` | **Yes** | Part of GNU coreutils — kills a hung purge run |
| `df` | **Yes** | Disk usage check |

```bash
# Debian / Ubuntu
apt-get install -y curl jq coreutils
```

---

## Installation

### 1 — Clone the repository

```bash
git clone https://github.com/anouarharrou/log-purger.git
cd log-purger
```

### 2 — Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3 — Edit the config file

```bash
nano purger/purge_config.json
```

At minimum, set `bucket`, `project`, and add your services. See the [full config reference](#purge_configjson--all-options) below.

### 4 — Set AWS credentials

**IAM role (recommended on EC2 / ECS / Fargate):**
Nothing to do. boto3 picks up the instance/task role automatically.

**Environment variables (recommended everywhere else):**
```bash
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_DEFAULT_REGION="eu-west-1"
```

**Last resort — in purge_config.json:**
```json
"key": "AKIA...",
"secret": "..."
```

### 5 — Configure SMTP (optional but recommended)

```bash
cp .purge_secrets.example .purge_secrets
chmod 600 .purge_secrets
nano .purge_secrets       # fill in SMTP_USER, SMTP_PASS, MAIL_TO
```

### 6 — Always dry-run first

```bash
python3 purger/purge_v2.py --dry-run --log-level DEBUG
```

Nothing is uploaded or deleted. All decisions are printed with a `[DRY-RUN]` prefix. Fix any issues before the first real run.

### 7 — First real run

```bash
python3 purger/purge_v2.py
```

### 8 — Schedule with cron

```bash
chmod +x purge_crontab.sh
crontab -e
```

```cron
# Daily at 02:00 AM
0 2 * * * root /opt/log-purger/purge_crontab.sh
```

---

## Configuration Guide

### purge_config.json — all options

```jsonc
{
  "config": {

    // ── S3 destination ───────────────────────────────────────────────────
    "bucket":  "my-logs-bucket",    // REQUIRED. S3 bucket name.
    "project": "my-app",            // REQUIRED. First prefix segment in every S3 key.
                                    // Full path: {project}/{service}/{date}/{host}/file.gz

    // ── AWS credentials ──────────────────────────────────────────────────
    // Priority: IAM role > environment variables > these fields.
    // Leave empty when using IAM roles or env vars (strongly recommended).
    "key":    "",                   // AWS_ACCESS_KEY_ID
    "secret": "",                   // AWS_SECRET_ACCESS_KEY
    "region": "eu-west-1",         // AWS_DEFAULT_REGION  |  Default: "us-east-1"

    // ── Custom S3-compatible endpoint ────────────────────────────────────
    // DEFAULT: None (empty) → boto3 uses https://s3.{region}.amazonaws.com
    // Set only for non-AWS targets: MinIO, Ceph, Cloudflare R2, etc.
    // https:// is added automatically if the value does not start with http.
    "server": "",                   // e.g. "https://minio.internal:9000"

    // ── Parallelism ──────────────────────────────────────────────────────
    "max_workers": 4,               // Threads for parallel uploads per service.
                                    // Increase for many small files. Default: 4

    // ── Retry behaviour ──────────────────────────────────────────────────
    "retry_attempts": 3,            // Total attempts per file before writing to DLQ.
    "retry_wait_min": 2,            // Seconds before first retry.
    "retry_wait_max": 30,           // Maximum wait between retries (exponential cap).
                                    // Wait formula: min(wait_min * 2^attempt, wait_max)
                                    // Attempt 1 → 2s, Attempt 2 → 4s, Attempt 3 → 8s

    // ── S3 storage class ─────────────────────────────────────────────────
    // Global default. Can be overridden per service.
    // STANDARD | STANDARD_IA | ONEZONE_IA | INTELLIGENT_TIERING |
    // GLACIER_IR | GLACIER | DEEP_ARCHIVE
    "storage_class": "STANDARD_IA",

    // ── Dead-Letter Queue path ───────────────────────────────────────────
    // Where failed upload records are written.
    // Default (empty): purger/purge/dlq/dead_letters.jsonl
    "dead_letter_path": "",

    // ── Disk safety guard ────────────────────────────────────────────────
    // A file is skipped if free space on its partition drops below this value.
    // Prevents the compression step from triggering an out-of-disk condition.
    "min_free_mb": 200,             // Default: 200 MB

    // ── Upload integrity ─────────────────────────────────────────────────
    // After each upload, call HeadObject and compare S3 ETag to local file.
    "verify_upload": true,          // Default: true

    // ── Dry run ──────────────────────────────────────────────────────────
    // Same effect as --dry-run CLI flag. Useful for testing config changes.
    "dry_run": false,

    // ── Logging ──────────────────────────────────────────────────────────
    "log_level": "INFO"             // DEBUG | INFO | WARNING | ERROR
  },

  // ── Services ────────────────────────────────────────────────────────────
  // One entry per application. All services run sequentially;
  // files within each service upload in parallel (max_workers threads).
  "services": [
    {
      "service":  "nginx",               // REQUIRED. Name used in S3 path, logs, email.

      "log_path": "/var/log/nginx",      // Directory to scan.
                                         // Default: purger/logs/ (relative to script)

      "pattern":  "^access\\.log.*",     // Python regex matched against filename only.
                                         // Default: ^.+\.log.*
                                         // See pattern examples below.

      "compress":         true,          // gzip level 9 before upload. Default: true
      "RemoveOnTransfer": true,          // Delete local file after confirmed upload.
      "enabled":          true,          // false = skip this service without removing it.

      "storage_class": "STANDARD_IA",   // Overrides global storage_class for this service.

      "extra_tags": {                    // Additional S3 object tags on every uploaded file.
        "team":        "platform",       // Useful for cost allocation and lifecycle rules.
        "environment": "production"      // service, hostname, date are always added automatically.
      }
    },
    {
      "service":          "app-backend",
      "log_path":         "/var/log/myapp",
      "pattern":          "^app\\.log.*",
      "compress":         true,
      "RemoveOnTransfer": true,
      "enabled":          true,
      "storage_class":    "GLACIER_IR",
      "extra_tags": {
        "team": "backend",
        "environment": "production"
      }
    }
  ]
}
```

### Pattern examples

The regex is matched against the **filename only** (not the full path) using `re.fullmatch()`:

```
Pattern                          Matches
──────────────────────────────── ──────────────────────────────────────────
^access\.log.*                   access.log  access.log.1  access.log.2025-01-13
^app\.log\.\d+$                  app.log.1  app.log.2  app.log.99   (not app.log itself)
^.*\.log$                        anything ending in .log
^service-.+\.log(\.gz)?$         service-prod.log  service-prod.log.gz
^(access|error)\.log.*           access.log  error.log  access.log.1
```

To test your pattern before running:
```bash
python3 -c "
import re, os
pattern = r'^access\.log.*'
path    = '/var/log/nginx'
matches = [f for f in os.listdir(path) if re.fullmatch(pattern, f)]
print('\n'.join(matches) or 'No matches found')
"
```

---

### Credentials — the right way

There are 4 ways to provide AWS credentials to the purger, listed from most secure to least secure. **Pick the first one that applies to your environment and ignore the rest.**

---

#### Option 1 — IAM Role ✅ best, nothing to store anywhere

If your server runs on **EC2, ECS, or Fargate**, attach an IAM role with S3 write permissions to the instance or task. boto3 detects it automatically and fetches a short-lived rotating token. No key, no secret, no file, no risk.

You do not need to set `key` or `secret` in `purge_config.json`. Leave them empty or remove them.

---

#### Option 2 — Environment variables ✅ recommended for VMs and bare metal

```bash
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_DEFAULT_REGION="eu-west-1"
```

To make these permanent across reboots, add them to `/etc/environment` (system-wide) or your user's shell profile (`~/.bashrc`, `~/.profile`). boto3 reads them automatically — nothing goes in any file in the repo.

---

#### Option 3 — AWS CLI credentials file ✅ suitable for developer machines

```bash
aws configure
# Prompts for key, secret, region, and output format.
# Writes to ~/.aws/credentials automatically.
```

boto3 reads `~/.aws/credentials` without any configuration. Fine for local testing or developer laptops.

---

#### Option 4 — purge_config.json ⚠️ last resort only

```json
"key":    "AKIA...",
"secret": "..."
```

If you must use this option, immediately restrict the file permissions to prevent other users on the system from reading it:

```bash
chmod 600 purger/purge_config.json
```

And verify the file is not being tracked by git with real values:

```bash
git status purger/purge_config.json
```

If it was committed with credentials in it, **rotate your AWS keys immediately** via the IAM console.

---

#### What is NOT acceptable

```bash
# ❌ Credentials visible in `ps aux` and shell history
AWS_ACCESS_KEY_ID=AKIA... python3 purger/purge_v2.py

# ❌ Hardcoded directly in the script
KEY = "AKIA..."

# ❌ purge_config.json committed to git with real key/secret
git add purger/purge_config.json   # dangerous if key/secret are filled in
```

---

#### Quick decision chart

```
Are you on EC2, ECS, or Fargate?
    └─ Yes → Option 1: IAM Role — done, nothing else needed

Are you on a VM or bare metal server?
    └─ Yes → Option 2: environment variables in /etc/environment

Is this a developer machine / local test?
    └─ Yes → Option 3: aws configure

None of the above?
    └─ Option 4: purge_config.json  (chmod 600, never commit)
```

---

### .purge_secrets — SMTP setup

This file is sourced by `purge_crontab.sh` at runtime. It is already in `.gitignore` and must never be committed with real values.

```bash
cp .purge_secrets.example .purge_secrets
chmod 600 .purge_secrets
nano .purge_secrets
```

```bash
# .purge_secrets
SMTP_HOST="smtp.gmail.com"
SMTP_PORT="587"
SMTP_USER="alerts@yourcompany.com"
SMTP_PASS="xxxx xxxx xxxx xxxx"         # Gmail app password — NOT your account password
MAIL_FROM="alerts@yourcompany.com"
MAIL_TO="ops@company.com,oncall@company.com"   # comma-separated list supported
```

All values can alternatively be set as environment variables, which take precedence over this file.

---

## Running the Script

### Manual run

```bash
# From the repo root
python3 purger/purge_v2.py

# Process only one service
python3 purger/purge_v2.py --service nginx

# With a custom config path
python3 purger/purge_v2.py --config /etc/purger/prod.json
```

### CLI reference

| Flag | Default | Description |
|------|---------|-------------|
| `-c`, `--config PATH` | `./purger/purge_config.json` | Path to config file |
| `--dry-run` | off | Simulate everything — no uploads, no deletions |
| `--replay-dlq` | off | Retry all DLQ entries before processing new files |
| `--service NAME` | all enabled | Process only this service |
| `--log-level LEVEL` | from config | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `--version` | — | Print version and exit |

### Dry run — always do this first

```bash
python3 purger/purge_v2.py --dry-run --log-level DEBUG
```

Example output:
```
01/13/2025 02:00:01 [INFO   ] ☁️   S3 bucket 'my-logs-bucket' is reachable.
01/13/2025 02:00:01 [INFO   ] 🔵  [DRY-RUN] mode — no files will be uploaded or deleted.
01/13/2025 02:00:01 [INFO   ] 📁  [nginx] 3 file(s) to process:
01/13/2025 02:00:01 [DEBUG  ]      • access.log     (120.43 MB)
01/13/2025 02:00:01 [DEBUG  ]      • access.log.1   ( 98.12 MB)
01/13/2025 02:00:01 [DEBUG  ]      • access.log.2   ( 87.55 MB)
01/13/2025 02:00:01 [INFO   ] 🔵  [DRY-RUN] Would upload: access.log.gz → s3://my-logs-bucket/my-app/nginx/13012025_0200/web-01/access.log.gz
...
═══════════════════════════════════════════════════════════
  📊  RUN SUMMARY
═══════════════════════════════════════════════════════════
  Total files  : 3
  ✅  Succeeded : 3
  ⏭️   Skipped   : 0
  ❌  Failed    : 0
  ⏱️   Duration  : 0.0 s
═══════════════════════════════════════════════════════════
```

---

## Scheduling with Cron

### Cron wrapper behaviour

```
purge_crontab.sh starts
        │
        ├─ rotate_logs()          Delete cron_log_*.log files older than LOG_KEEP_DAYS
        ├─ check_dependencies()   Verify python3, curl, jq, timeout are installed
        ├─ acquire_lock()         Write PID to /tmp/purger_cron.lock
        │                         ├─ Lock exists + process alive  → exit 0 (already running)
        │                         └─ Lock exists + process dead   → remove stale lock, continue
        │
        ├─ get_disk_usage_pct()   df -P $FILESYSTEM
        │
        ├─ usage < CRITICAL_USAGE? ──► log "nothing to do" → exit 0
        │
        └─ timeout $PURGE_TIMEOUT python3 purge_v2.py --config ... --replay-dlq
                  │
                  ├─ exit 0   → get_disk_usage_pct() → send ✅ success email
                  ├─ exit 1   → send ❌ fatal error email    → exit 1
                  ├─ exit 2   → send ⚠️ partial failure email (DLQ has entries)
                  └─ exit 124 → send 🚨 timed out email      → exit 1

                  always after: if disk_after ≥ 95% → send 🚨 CRITICAL still full
                  always after: release lock (trap EXIT)
```

### Cron setup

```bash
chmod +x purge_crontab.sh
crontab -e
```

```cron
# Daily at 02:00 AM — suitable for most servers
0 2 * * * root /opt/log-purger/purge_crontab.sh

# Every 6 hours — for high-traffic servers
0 */6 * * * root /opt/log-purger/purge_crontab.sh

# Monitor a specific partition with a lower threshold
0 2 * * * root FILESYSTEM=/data CRITICAL_USAGE=60 /opt/log-purger/purge_crontab.sh
```

### Cron environment variables

All settings can be overridden per-cron-line without editing the script:

| Variable | Default | Description |
|----------|---------|-------------|
| `FILESYSTEM` | `/` | Mount point to monitor |
| `CRITICAL_USAGE` | `75` | Run purge when disk usage ≥ this % |
| `LOG_KEEP_DAYS` | `14` | Days of daily cron logs to retain |
| `PURGE_TIMEOUT` | `3600` | Kill purge_v2.py if it runs longer than N seconds |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | `587` | SMTP port (STARTTLS) |
| `SMTP_USER` | _(empty)_ | SMTP username / sender address |
| `SMTP_PASS` | _(empty)_ | SMTP password or app password |
| `MAIL_FROM` | `$SMTP_USER` | From address |
| `MAIL_TO` | _(empty)_ | Recipient(s) — comma-separated |

### Email notifications

| Trigger | Subject prefix | Severity |
|---------|---------------|----------|
| All uploads succeeded | `✅ [PURGER] Run Completed Successfully` | Info |
| Some files in DLQ | `⚠️ [PURGER] Completed with Partial Failures` | Warning |
| Fatal startup error | `❌ [PURGER] Fatal error` | Critical |
| Script timed out | `🚨 [PURGER] Execution timed out` | Critical |
| Disk still ≥ 95% after purge | `🚨 [PURGER] CRITICAL disk still full` | Critical |
| purge_v2.py not found | `🚨 [PURGER] Script missing` | Critical |

Every email includes: hostname, server IP, filesystem, disk % before and after, MB reclaimed, start/end time, and a table of all services processed.

### Gmail SMTP setup

1. Enable 2-Step Verification on your Google account (required by Google)
2. Open [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Select app: **Mail** → device: **Other** → name it "Log Purger" → **Generate**
4. Copy the 16-character password
5. Add to `.purge_secrets`:

```bash
SMTP_USER="your.address@gmail.com"
SMTP_PASS="abcd efgh ijkl mnop"     # spaces in the app password are fine
MAIL_TO="you@gmail.com"
```

---

## Self-Healing System

Log Purger is designed to recover from failures automatically across runs.

### Dead-Letter Queue

When an upload exhausts all retry attempts, its metadata is appended to:
```
purger/purge/dlq/dead_letters.jsonl
```

Each record is one JSON line:
```json
{
  "service":    "nginx",
  "local_file": "/var/log/nginx/access.log.gz",
  "s3_key":     "my-app/nginx/13012025_0200/web-01/access.log.gz",
  "bucket":     "my-logs-bucket",
  "reason":     "Upload failed after all retries",
  "queued_at":  "2025-01-13T02:00:45"
}
```

At the **start of every run** (via `--replay-dlq`), the DLQ is fully drained and every entry is re-attempted before any new files are processed. Entries that succeed are removed. Entries that still fail are written back. Entries whose local file no longer exists are discarded with a warning.

### Retry with exponential backoff

```
Upload attempt 1 fails  →  wait  2s  →  attempt 2
Upload attempt 2 fails  →  wait  4s  →  attempt 3
Upload attempt 3 fails  →  wait  8s  →  write to DLQ
                               (capped at retry_wait_max, default 30s)
```

Configurable via `retry_attempts`, `retry_wait_min`, `retry_wait_max`.

### All failure modes

```
┌──────────────────────────────────┬─────────────────────────────────────────────────────┐
│ Failure                          │ What happens                                        │
├──────────────────────────────────┼─────────────────────────────────────────────────────┤
│ S3 throttling / 503              │ Exponential backoff retry                           │
│ Network timeout                  │ Exponential backoff retry                           │
│ Permanent S3 failure             │ Written to DLQ → auto-retried next run             │
│ Compression fails                │ Written to DLQ, .gz artefact cleaned up            │
│ Disk nearly full                 │ File skipped with warning (min_free_mb guard)      │
│ Upload succeeds, delete fails    │ Warning logged, original file kept (no data loss)  │
│ ETag mismatch (multipart)        │ Warning logged (known S3 behaviour, not an error)  │
│ Config JSON invalid              │ Fatal exit 1 before anything is processed          │
│ S3 bucket not found              │ Fatal exit 1 before anything is processed          │
│ No AWS credentials               │ Fatal exit 1 before anything is processed          │
│ SIGTERM / Ctrl-C                 │ Finishes current file cleanly, then stops          │
│ Cron run hangs                   │ Killed after PURGE_TIMEOUT seconds, alert sent     │
│ Two cron runs overlap            │ Second run detects live lock file → exit 0         │
│ SMTP failure                     │ Warning logged, purge continues unaffected         │
│ Missing system tool (jq, curl)   │ Fatal before any work begins, message printed      │
└──────────────────────────────────┴─────────────────────────────────────────────────────┘
```

---

## Understanding the Logs

### Python log — purger/purge/logs/purge.log

Rotated automatically at 10 MB, retaining 5 files. Format:
```
MM/DD/YYYY HH:MM:SS [LEVEL  ] message
```

Key messages and their meaning:

```
☁️   S3 bucket 'my-bucket' is reachable.
     Startup connectivity check passed. Credentials and bucket are valid.

📁  [nginx] 3 file(s) to process
     Found 3 files matching the pattern. Processing begins.

✅  Compressed: access.log → access.log.gz (120.43 MB → 12.18 MB)
     File compressed at level 9. ~90% size reduction achieved.

📤  [nginx] Uploading access.log.gz → s3://bucket/project/nginx/...
     Upload started. ETag check will follow.

✅  Verified: access.log.gz → s3://bucket/... (etag=a1b2c3d4)
     Upload complete. S3 ETag matches local file. Integrity confirmed.

🗑️   RemoveOnTransfer: removed original access.log
     Local file deleted. Only reached this line because the upload and
     verification both passed.

⚠️   Upload attempt 1/3 failed (ConnectionError). Retrying in 2.0s…
     Transient failure. Will retry automatically.

❌  Upload failed after 3 attempts: access.log.gz
     Permanent failure. File written to DLQ for retry on next run.

📮  DLQ: queued failed upload → purger/purge/dlq/dead_letters.jsonl
     Record saved. Will be replayed automatically on next cron run.

📬  Replaying 2 DLQ entries…
     Beginning DLQ replay at the start of a new run.
```

### Cron log — purger/cron_logs/cron_log_YYYY-MM-DD.log

One file per day, retained for `LOG_KEEP_DAYS` (default 14) days. Contains the wrapper decisions and the complete Python output for that day.

```
2025-01-13 02:00:00 [INFO ] CRON JOB STARTED
2025-01-13 02:00:00 [INFO ] Host: web-01 (10.0.1.55)
2025-01-13 02:00:00 [INFO ] Disk: / → 82% used / 3420 MB free (threshold: 75%)
2025-01-13 02:00:00 [WARN ] Usage 82% ≥ 75% — triggering purge.
2025-01-13 02:00:00 [INFO ] PURGE STARTED
... (full purge_v2.py output) ...
2025-01-13 02:01:34 [INFO ] PURGE ENDED (exit: 0)
2025-01-13 02:01:34 [INFO ] Disk after: 31% used / 11840 MB free (reclaimed: ~51%)
2025-01-13 02:01:34 [INFO ] CRON JOB ENDED
```

---

## S3 Object Layout

Every uploaded file lands at this exact path:

```
s3://{bucket}/{project}/{service}/{DDMMYYYY_HHMM}/{hostname}/{filename}.gz
```

Example with `bucket=my-logs`, `project=my-app`, two services, two servers:

```
my-logs/
└── my-app/
    ├── nginx/
    │   └── 13012025_0200/
    │       ├── web-01/
    │       │   ├── access.log.gz
    │       │   └── access.log.1.gz
    │       └── web-02/
    │           └── access.log.gz
    └── app-backend/
        └── 13012025_0200/
            └── web-01/
                └── app.log.gz
```

Every object is tagged automatically with:

```
service   = nginx
hostname  = web-01
date      = 13012025_0200
+ any extra_tags defined in purge_config.json
```

Use these tags to create **S3 Lifecycle rules** per service (e.g. move `GLACIER` objects to `DEEP_ARCHIVE` after 90 days) or filter **Cost Explorer** charges by team or environment.

---

## Exit Codes

| Code | Returned by | Meaning | Recommended action |
|------|-------------|---------|-------------------|
| `0` | both | Everything succeeded, or disk below threshold | None |
| `1` | purge_v2.py | Fatal error — bad config, missing credentials, S3 unreachable | Check logs, fix config |
| `2` | purge_v2.py | Partial failure — some files are in the DLQ | Check DLQ; auto-retried next run |
| `124` | purge_crontab.sh | Execution timed out | Increase `PURGE_TIMEOUT` or investigate |

---

## Security

### Credential priority

```
1. IAM Instance/Task Role   No credentials stored anywhere. Best option.
2. Environment variables    Not visible in config files or git history.
3. ~/.aws/credentials       Managed by AWS CLI. Fine for dev machines.
4. purge_config.json        chmod 600. Risk of accidental git commit.
```

### What was removed in v2

The original script had an **auto-update** feature that downloaded `purge.py` from a GitHub URL and overwrote itself — with no hash check or signature verification. This is a supply-chain attack vector. It has been completely removed. Deploy updates via `git pull`, Ansible, or your CI/CD pipeline.

### Minimum S3 IAM permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "s3:PutObject",
      "s3:HeadObject",
      "s3:HeadBucket"
    ],
    "Resource": [
      "arn:aws:s3:::your-bucket-name",
      "arn:aws:s3:::your-bucket-name/*"
    ]
  }]
}
```

Do not grant `s3:*` or `s3:DeleteObject`. The purger only needs to write and verify.

### Encryption

`ServerSideEncryption: AES256` is applied to every uploaded object automatically. No configuration needed.

---

## Upgrading from v1

| Step | What to do |
|------|-----------|
| 1 | Place `purge_v2.py` in the `purger/` subdirectory |
| 2 | Replace `purge_crontab.sh` at the repo root |
| 3 | Run `pip install -r requirements.txt` |
| 4 | `purge_config.json` is backwards-compatible — both `RemoveOnTransfer` and the old typo `RemoveOnTransfert` are accepted |
| 5 | Move `key`/`secret` from the config into env vars or `.purge_secrets` |
| 6 | `python3 purger/purge_v2.py --dry-run` |

If your cron job still calls `purge.py`, update the cron line or create a compatibility symlink:
```bash
ln -s purge_v2.py purger/purge.py
```

---

## Troubleshooting

**No files matched pattern**
```bash
python3 -c "
import re, os
pattern = r'^access\.log.*'   # your pattern here
path    = '/var/log/nginx'     # your log_path here
matches = [f for f in os.listdir(path) if re.fullmatch(pattern, f)]
print('\n'.join(matches) or 'No matches — check your pattern')
"
```

**S3 connectivity error**
```bash
# Test credentials directly
python3 -c "import boto3; boto3.client('s3').head_bucket(Bucket='your-bucket'); print('OK')"
```

**boto3 not found**
```bash
pip install boto3
# System Python on modern Debian/Ubuntu:
pip3 install boto3 --break-system-packages
```

**Emails not arriving — check curl exit code in cron log**

| curl exit | Cause |
|-----------|-------|
| 0 | Success |
| 67 | Empty `SMTP_USER`, `SMTP_PASS`, or `MAIL_TO` |
| 67/78 | Wrong SMTP host or port |
| 67 (auth) | `SMTP_PASS` is your account password, not an app password |

**DLQ keeps growing**
```bash
cat purger/purge/dlq/dead_letters.jsonl | python3 -m json.tool
```
Look at the `reason` field. Common causes: wrong bucket name, insufficient IAM permissions, file already deleted before replay.

**Manually replay the DLQ**
```bash
python3 purger/purge_v2.py --replay-dlq --log-level DEBUG
```

**Debug a single service without touching others**
```bash
python3 purger/purge_v2.py --service nginx --dry-run --log-level DEBUG
```

---

## Contributors

- 🙋‍♂️ [Anouar HARROU](https://github.com/anouarharrou) — original author & maintainer

---

## License

[MIT License](LICENSE) — free to use, modify, and distribute.