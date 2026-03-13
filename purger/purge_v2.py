#!/usr/bin/env python3
"""
██████╗ ██╗   ██╗██████╗  ██████╗ ███████╗██████╗
██╔══██╗██║   ██║██╔══██╗██╔════╝ ██╔════╝██╔══██╗
██████╔╝██║   ██║██████╔╝██║  ███╗█████╗  ██████╔╝
██╔═══╝ ██║   ██║██╔══██╗██║   ██║██╔══╝  ██╔══██╗
██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗██║  ██║
╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝

Log Purger v2.0.0 — Production-Grade, Self-Healing Log Management
Author   : Anouar HARROU (original) / Enhanced Edition
License  : MIT
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import logging
import os
import platform
import re
import shutil
import signal
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

# ──────────────────────────────────────────────
# Guard: Python 3.8+ required
# ──────────────────────────────────────────────
if sys.version_info < (3, 8):
    sys.exit("❌  Python 3.8 or higher is required.")

# ──────────────────────────────────────────────
# Optional dependencies — degrade gracefully
# ──────────────────────────────────────────────
try:
    import boto3
    from botocore.exceptions import (
        BotoCoreError,
        ClientError,
        EndpointConnectionError,
        NoCredentialsError,
    )
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False

try:
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
        before_sleep_log,
    )
    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)
    COLORAMA_AVAILABLE = True
except ImportError:
    COLORAMA_AVAILABLE = False

try:
    import jsonschema
    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False


# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────
__APP_NAME   = "purger"
__VERSION    = "2.0.0"
__SCRIPT_DIR = Path(__file__).resolve().parent
__NOW        = datetime.now()
__BACKUP_DATE = __NOW.strftime("%d%m%Y_%H%M")
__HOSTNAME   = platform.node()

# ──────────────────────────────────────────────
# Config JSON schema for validation
# ──────────────────────────────────────────────
CONFIG_SCHEMA = {
    "type": "object",
    "required": ["config", "services"],
    "properties": {
        "config": {
            "type": "object",
            "required": ["bucket", "project"],
            "properties": {
                "bucket":  {"type": "string", "minLength": 1},
                "project": {"type": "string", "minLength": 1},
                "key":     {"type": "string"},
                "secret":  {"type": "string"},
                "region":  {"type": "string"},
                "server":  {"type": "string"},
                "max_workers":        {"type": "integer", "minimum": 1, "maximum": 32},
                "retry_attempts":     {"type": "integer", "minimum": 1, "maximum": 10},
                "retry_wait_min":     {"type": "number",  "minimum": 1},
                "retry_wait_max":     {"type": "number",  "minimum": 1},
                "storage_class":      {"type": "string"},
                "dead_letter_path":   {"type": "string"},
                "min_free_mb":        {"type": "number",  "minimum": 0},
                "dry_run":            {"type": "boolean"},
                "verify_upload":      {"type": "boolean"},
                "log_level":          {"type": "string", "enum": ["DEBUG","INFO","WARNING","ERROR","CRITICAL"]},
            },
        },
        "services": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["service"],
                "properties": {
                    "service":            {"type": "string", "minLength": 1},
                    "log_path":           {"type": "string"},
                    "pattern":            {"type": "string"},
                    "compress":           {"type": ["boolean", "string"]},
                    "RemoveOnTransfer":   {"type": ["boolean", "string"]},
                    "enabled":            {"type": "boolean"},
                    "storage_class":      {"type": "string"},
                    "extra_tags":         {"type": "object"},
                },
            },
        },
    },
}


# ──────────────────────────────────────────────
# Colour helpers
# ──────────────────────────────────────────────
def _c(text: str, colour: str) -> str:
    if not COLORAMA_AVAILABLE:
        return text
    colours = {
        "red":    Fore.RED,
        "green":  Fore.GREEN,
        "yellow": Fore.YELLOW,
        "blue":   Fore.BLUE,
        "cyan":   Fore.CYAN,
        "white":  Fore.WHITE,
        "grey":   Fore.WHITE + Style.DIM,
    }
    return f"{colours.get(colour, '')}{text}{Style.RESET_ALL}"


# ──────────────────────────────────────────────
# Logging setup
# ──────────────────────────────────────────────
def setup_logger(log_path: Path, level: str = "INFO") -> logging.Logger:
    """
    Configure a logger that writes to both stdout (coloured) and a rotating
    file handler (plain text, max 10 MB × 5 rotations).
    """
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / "purge.log"

    logger = logging.getLogger("purger")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt_plain = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
    )

    # Rotating file handler — never lets the log grow unbounded
    fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
    fh.setFormatter(fmt_plain)
    logger.addHandler(fh)

    # Console handler (coloured if available)
    class ColouredFormatter(logging.Formatter):
        LEVEL_COLOURS = {
            logging.DEBUG:    "grey",
            logging.INFO:     "green",
            logging.WARNING:  "yellow",
            logging.ERROR:    "red",
            logging.CRITICAL: "red",
        }
        def format(self, record: logging.LogRecord) -> str:
            msg = super().format(record)
            return _c(msg, self.LEVEL_COLOURS.get(record.levelno, "white"))

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(ColouredFormatter(
        "%(asctime)s [%(levelname)-8s] %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
    ))
    logger.addHandler(ch)
    return logger


log = logging.getLogger("purger")  # module-level handle; initialised in main()


# ──────────────────────────────────────────────
# Dataclasses — typed, immutable-ish value objects
# ──────────────────────────────────────────────
@dataclass
class GlobalConfig:
    bucket:           str
    project:          str
    key:              Optional[str]   = None
    secret:           Optional[str]   = None
    region:           str             = "us-east-1"
    server:           Optional[str]   = None
    max_workers:      int             = 4
    retry_attempts:   int             = 3
    retry_wait_min:   float           = 2.0
    retry_wait_max:   float           = 30.0
    storage_class:    str             = "STANDARD_IA"
    dead_letter_path: Optional[str]   = None
    min_free_mb:      float           = 200.0
    dry_run:          bool            = False
    verify_upload:    bool            = True
    log_level:        str             = "INFO"


@dataclass
class ServiceConfig:
    service:           str
    log_path:          str
    pattern:           str            = r"^.+\.log.*"
    compress:          bool           = True
    remove_on_transfer: bool          = True
    enabled:           bool           = True
    storage_class:     Optional[str]  = None
    extra_tags:        dict           = field(default_factory=dict)


@dataclass
class FileStats:
    file_path:   str
    file_name:   str
    bytes_size:  int
    mb_size:     float
    uid:         Optional[int]
    gid:         Optional[int]
    md5:         str


@dataclass
class UploadResult:
    service:   str
    file:      str
    success:   bool
    skipped:   bool  = False
    reason:    str   = ""
    duration_s: float = 0.0


# ──────────────────────────────────────────────
# Config loading & validation
# ──────────────────────────────────────────────

def _coerce_bool(val: Any, default: bool = True) -> bool:
    """Accept bool, 'true'/'false' strings, 1/0 integers."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes")
    if isinstance(val, int):
        return bool(val)
    return default


def load_and_validate_config(config_file: Path) -> tuple[GlobalConfig, list[ServiceConfig]]:
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    with config_file.open("r", encoding="utf-8") as fh:
        try:
            raw = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed JSON in config: {exc}") from exc

    if JSONSCHEMA_AVAILABLE:
        try:
            jsonschema.validate(instance=raw, schema=CONFIG_SCHEMA)
        except jsonschema.ValidationError as exc:
            raise ValueError(f"Config schema error: {exc.message}") from exc
    else:
        # Minimal manual validation
        for key in ("config", "services"):
            if key not in raw:
                raise ValueError(f"Missing required top-level key: '{key}'")

    cfg_raw = raw["config"]

    # Credentials: prefer environment variables over config file
    aws_key    = os.environ.get("AWS_ACCESS_KEY_ID",     cfg_raw.get("key"))
    aws_secret = os.environ.get("AWS_SECRET_ACCESS_KEY", cfg_raw.get("secret"))
    aws_region = os.environ.get("AWS_DEFAULT_REGION",    cfg_raw.get("region", "us-east-1"))

    global_cfg = GlobalConfig(
        bucket           = cfg_raw["bucket"],
        project          = cfg_raw["project"],
        key              = aws_key,
        secret           = aws_secret,
        region           = aws_region,
        server           = cfg_raw.get("server"),
        max_workers      = int(cfg_raw.get("max_workers",    4)),
        retry_attempts   = int(cfg_raw.get("retry_attempts", 3)),
        retry_wait_min   = float(cfg_raw.get("retry_wait_min", 2.0)),
        retry_wait_max   = float(cfg_raw.get("retry_wait_max", 30.0)),
        storage_class    = cfg_raw.get("storage_class",    "STANDARD_IA"),
        dead_letter_path = cfg_raw.get("dead_letter_path"),
        min_free_mb      = float(cfg_raw.get("min_free_mb",  200.0)),
        dry_run          = _coerce_bool(cfg_raw.get("dry_run",         False)),
        verify_upload    = _coerce_bool(cfg_raw.get("verify_upload",   True)),
        log_level        = cfg_raw.get("log_level", "INFO").upper(),
    )

    services: list[ServiceConfig] = []
    for svc in raw["services"]:
        # Accept both 'RemoveOnTransfer' (correct) and legacy 'RemoveOnTransfert' (typo)
        rot = svc.get("RemoveOnTransfer", svc.get("RemoveOnTransfert", True))
        services.append(ServiceConfig(
            service            = svc["service"],
            log_path           = svc.get("log_path", str(__SCRIPT_DIR / "logs")),
            pattern            = svc.get("pattern",  r"^.+\.log.*"),
            compress           = _coerce_bool(svc.get("compress", True)),
            remove_on_transfer = _coerce_bool(rot, True),
            enabled            = _coerce_bool(svc.get("enabled", True)),
            storage_class      = svc.get("storage_class"),
            extra_tags         = svc.get("extra_tags", {}),
        ))

    return global_cfg, services


# ──────────────────────────────────────────────
# S3 client factory (boto3 — no CLI shell calls)
# ──────────────────────────────────────────────

def build_s3_client(cfg: GlobalConfig):
    """
    Build a boto3 S3 client.
    Priority for credentials:
      1. IAM Instance Role / ECS Task Role (no key/secret needed)
      2. Environment variables (already merged into GlobalConfig)
      3. Explicit key/secret from config file (least preferred)
    """
    if not BOTO3_AVAILABLE:
        raise RuntimeError(
            "boto3 is not installed. Run: pip install boto3"
        )

    kwargs: dict[str, Any] = {"region_name": cfg.region}

    if cfg.server:
        kwargs["endpoint_url"] = (
            cfg.server
            if cfg.server.startswith("http")
            else f"https://{cfg.server}"
        )

    if cfg.key and cfg.secret:
        kwargs["aws_access_key_id"]     = cfg.key
        kwargs["aws_secret_access_key"] = cfg.secret

    return boto3.client("s3", **kwargs)


# ──────────────────────────────────────────────
# File utilities
# ──────────────────────────────────────────────

def md5_of_file(path: Path, chunk_size: int = io.DEFAULT_BUFFER_SIZE) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def etag_of_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    """
    Reproduce the S3 ETag algorithm for single-part uploads.
    For files smaller than chunk_size this equals the plain MD5.
    """
    size = path.stat().st_size
    if size <= chunk_size:
        return md5_of_file(path)
    # Multi-part: MD5 of concatenated part MD5s
    part_md5s = []
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            part_md5s.append(hashlib.md5(chunk).digest())
    combined = b"".join(part_md5s)
    return f"{hashlib.md5(combined).hexdigest()}-{len(part_md5s)}"


def get_file_stats(path: Path) -> FileStats:
    try:
        st = path.stat()
        return FileStats(
            file_path  = str(path.parent),
            file_name  = path.name,
            bytes_size = st.st_size,
            mb_size    = round(st.st_size / 1_048_576, 2),
            uid        = st.st_uid,
            gid        = st.st_gid,
            md5        = md5_of_file(path),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not stat %s: %s", path, exc)
        return FileStats(str(path.parent), path.name, 0, 0.0, None, None, "")


def free_mb_on_disk(path: str) -> float:
    try:
        st = shutil.disk_usage(path)
        return st.free / 1_048_576
    except Exception:
        return float("inf")


def discover_files(log_path: Path, pattern: str) -> list[Path]:
    """
    Return a sorted list of files in log_path that match the regex pattern.
    Silently skips sub-directories.
    """
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        log.error("Invalid regex pattern '%s': %s", pattern, exc)
        return []

    matches: list[Path] = []
    try:
        for entry in log_path.iterdir():
            if entry.is_file() and compiled.fullmatch(entry.name):
                matches.append(entry)
    except PermissionError as exc:
        log.error("Cannot read directory %s: %s", log_path, exc)
    return sorted(matches)


# ──────────────────────────────────────────────
# Compression (pure Python — no shell calls)
# ──────────────────────────────────────────────

def compress_file(src: Path) -> tuple[bool, Path]:
    """
    Gzip-compress src to src.gz using the Python standard library.
    Returns (success, output_path).
    """
    if src.suffix == ".gz":
        log.debug("%s is already compressed — skipping.", src.name)
        return True, src

    dst = src.with_suffix(src.suffix + ".gz")
    try:
        with src.open("rb") as f_in, gzip.open(dst, "wb", compresslevel=9) as f_out:
            shutil.copyfileobj(f_in, f_out)
        log.info("✅  Compressed: %s → %s (%.2f MB → %.2f MB)",
                 src.name, dst.name,
                 src.stat().st_size / 1_048_576,
                 dst.stat().st_size / 1_048_576)
        return True, dst
    except (OSError, gzip.BadGzipFile) as exc:
        log.error("❌  Compression failed for %s: %s", src.name, exc)
        if dst.exists():
            dst.unlink(missing_ok=True)
        return False, src


# ──────────────────────────────────────────────
# Dead-letter queue — resilience for failed uploads
# ──────────────────────────────────────────────

class DeadLetterQueue:
    """
    Persists failed upload metadata to a JSON-lines file so they can be
    retried on the next run, preventing silent data loss.
    """

    def __init__(self, dlq_path: Path):
        self.path = dlq_path
        self.path.mkdir(parents=True, exist_ok=True)
        self.file = self.path / "dead_letters.jsonl"

    def push(self, record: dict) -> None:
        record["queued_at"] = datetime.utcnow().isoformat()
        try:
            with self.file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            log.warning("📮  DLQ: queued failed upload → %s", self.file)
        except OSError as exc:
            log.error("Could not write to DLQ: %s", exc)

    def drain(self) -> list[dict]:
        """Load and clear all pending records."""
        if not self.file.exists():
            return []
        records: list[dict] = []
        try:
            with self.file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            self.file.unlink(missing_ok=True)
        except OSError as exc:
            log.error("Could not drain DLQ: %s", exc)
        return records


# ──────────────────────────────────────────────
# Upload logic with retry
# ──────────────────────────────────────────────

def _upload_with_retry(
    s3_client,
    local_path: Path,
    bucket: str,
    s3_key: str,
    storage_class: str,
    extra_tags: dict,
    attempts: int,
    wait_min: float,
    wait_max: float,
    verify: bool,
    dry_run: bool,
) -> bool:
    """
    Upload a single file to S3 with exponential-backoff retry.
    Returns True on success, False on permanent failure.
    """
    if dry_run:
        log.info("🔵  [DRY-RUN] Would upload: %s → s3://%s/%s", local_path.name, bucket, s3_key)
        return True

    stats = get_file_stats(local_path)
    tag_string = "&".join(f"{k}={v}" for k, v in extra_tags.items()) if extra_tags else ""

    def _do_upload() -> None:
        extra_args: dict[str, Any] = {"StorageClass": storage_class}
        if tag_string:
            extra_args["Tagging"] = tag_string
        # Server-side encryption — best practice
        extra_args["ServerSideEncryption"] = "AES256"
        s3_client.upload_file(
            Filename    = str(local_path),
            Bucket      = bucket,
            Key         = s3_key,
            ExtraArgs   = extra_args,
        )

    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            _do_upload()
            break
        except (EndpointConnectionError, BotoCoreError, ClientError) as exc:
            last_exc = exc
            if attempt == attempts:
                log.error("❌  Upload failed after %d attempts: %s → %s", attempts, local_path.name, exc)
                return False
            wait = min(wait_min * (2 ** (attempt - 1)), wait_max)
            log.warning(
                "⚠️   Upload attempt %d/%d failed (%s). Retrying in %.1fs…",
                attempt, attempts, exc, wait,
            )
            time.sleep(wait)

    if not verify:
        return True

    # ── Post-upload integrity check ──────────────────────────────────────
    try:
        head = s3_client.head_object(Bucket=bucket, Key=s3_key)
        remote_etag = head.get("ETag", "").strip('"')
        local_etag  = etag_of_file(local_path)
        if remote_etag == local_etag:
            log.info(
                "✅  Verified: %s → s3://%s/%s (%.2f MB, etag=%s)",
                local_path.name, bucket, s3_key, stats.mb_size, remote_etag[:8],
            )
        else:
            log.warning(
                "⚠️   ETag mismatch for %s (local=%s, remote=%s) — "
                "likely multipart; treating as success.",
                local_path.name, local_etag[:8], remote_etag[:8],
            )
    except ClientError as exc:
        log.warning("Could not verify upload for %s: %s", local_path.name, exc)

    return True


# ──────────────────────────────────────────────
# Per-file pipeline
# ──────────────────────────────────────────────

def process_file(
    file_path: Path,
    svc: ServiceConfig,
    cfg: GlobalConfig,
    s3_client,
    dlq: Optional[DeadLetterQueue],
) -> UploadResult:
    """
    Full pipeline for a single log file:
      1. Disk-space pre-check
      2. Optional compression
      3. S3 upload with retry
      4. Optional local deletion (only on confirmed success)
      5. DLQ write on permanent failure
    """
    t_start = time.monotonic()
    result  = UploadResult(service=svc.service, file=file_path.name, success=False)

    # ── 1. Disk space guard ──────────────────────────────────────────────
    free = free_mb_on_disk(str(file_path.parent))
    if free < cfg.min_free_mb:
        log.error(
            "🚨  [%s] Only %.1f MB free on %s — minimum %.1f MB required. "
            "Skipping %s to avoid OOM.",
            svc.service, free, file_path.parent, cfg.min_free_mb, file_path.name,
        )
        result.skipped = True
        result.reason  = f"Insufficient disk space ({free:.1f} MB free)"
        return result

    # ── 2. Compression ───────────────────────────────────────────────────
    upload_path = file_path
    if svc.compress:
        ok, upload_path = compress_file(file_path)
        if not ok:
            result.reason = "Compression failed"
            if dlq:
                dlq.push({
                    "service":    svc.service,
                    "local_file": str(file_path),
                    "reason":     result.reason,
                })
            return result

    # ── 3. Build S3 key ──────────────────────────────────────────────────
    s3_key = f"{cfg.project}/{svc.service}/{__BACKUP_DATE}/{__HOSTNAME}/{upload_path.name}"

    storage_class = svc.storage_class or cfg.storage_class
    tags = {
        "service":  svc.service,
        "hostname": __HOSTNAME,
        "date":     __BACKUP_DATE,
        **svc.extra_tags,
    }

    # ── 4. Upload ────────────────────────────────────────────────────────
    log.info(
        "📤  [%s] Uploading %s → s3://%s/%s (%.2f MB, class=%s)",
        svc.service, upload_path.name, cfg.bucket, s3_key,
        upload_path.stat().st_size / 1_048_576, storage_class,
    )

    upload_ok = _upload_with_retry(
        s3_client   = s3_client,
        local_path  = upload_path,
        bucket      = cfg.bucket,
        s3_key      = s3_key,
        storage_class = storage_class,
        extra_tags  = tags,
        attempts    = cfg.retry_attempts,
        wait_min    = cfg.retry_wait_min,
        wait_max    = cfg.retry_wait_max,
        verify      = cfg.verify_upload,
        dry_run     = cfg.dry_run,
    )

    if not upload_ok:
        result.reason = "Upload failed after all retries"
        if dlq:
            dlq.push({
                "service":    svc.service,
                "local_file": str(upload_path),
                "s3_key":     s3_key,
                "bucket":     cfg.bucket,
                "reason":     result.reason,
            })
        # On failure, remove the .gz artefact but keep the original
        if svc.compress and upload_path != file_path and upload_path.exists():
            upload_path.unlink(missing_ok=True)
            log.debug("Removed failed .gz artefact: %s", upload_path.name)
        return result

    # ── 5. Remove original & compressed files ────────────────────────────
    if svc.remove_on_transfer and not cfg.dry_run:
        # Remove compressed file first, then original
        if svc.compress and upload_path != file_path and upload_path.exists():
            try:
                upload_path.unlink()
                log.debug("🗑️   Removed compressed: %s", upload_path.name)
            except OSError as exc:
                log.warning("Could not remove compressed file %s: %s", upload_path.name, exc)

        # Remove original only after the compressed version was removed
        try:
            file_path.unlink()
            log.info("🗑️   RemoveOnTransfer: removed original %s", file_path.name)
        except OSError as exc:
            log.warning("Could not remove original file %s: %s", file_path.name, exc)

    result.success    = True
    result.duration_s = time.monotonic() - t_start
    return result


# ──────────────────────────────────────────────
# DLQ retry pass
# ──────────────────────────────────────────────

def replay_dead_letters(
    dlq: DeadLetterQueue,
    cfg: GlobalConfig,
    s3_client,
) -> tuple[int, int]:
    """
    Re-attempt all entries in the DLQ.
    Returns (success_count, fail_count).
    """
    records = dlq.drain()
    if not records:
        return 0, 0

    log.info("📬  Replaying %d DLQ entries…", len(records))
    ok_count = fail_count = 0

    for rec in records:
        local = Path(rec.get("local_file", ""))
        if not local.exists():
            log.warning("DLQ: file no longer exists: %s — discarding.", local)
            fail_count += 1
            continue

        s3_key = rec.get("s3_key", f"{cfg.project}/dlq/{local.name}")
        success = _upload_with_retry(
            s3_client     = s3_client,
            local_path    = local,
            bucket        = cfg.bucket,
            s3_key        = s3_key,
            storage_class = cfg.storage_class,
            extra_tags    = {"source": "dlq_replay"},
            attempts      = cfg.retry_attempts,
            wait_min      = cfg.retry_wait_min,
            wait_max      = cfg.retry_wait_max,
            verify        = cfg.verify_upload,
            dry_run       = cfg.dry_run,
        )
        if success:
            ok_count   += 1
            log.info("✅  DLQ replay success: %s", local.name)
        else:
            fail_count += 1
            dlq.push(rec)   # put it back for next run
            log.error("❌  DLQ replay still failing: %s", local.name)

    return ok_count, fail_count


# ──────────────────────────────────────────────
# Graceful shutdown via signals
# ──────────────────────────────────────────────
_SHUTDOWN = False

def _handle_signal(signum, _frame):
    global _SHUTDOWN
    log.warning("⚡  Signal %s received — finishing current file then stopping.", signum)
    _SHUTDOWN = True


# ──────────────────────────────────────────────
# Run summary
# ──────────────────────────────────────────────

def print_summary(results: list[UploadResult]) -> None:
    total    = len(results)
    success  = sum(1 for r in results if r.success)
    skipped  = sum(1 for r in results if r.skipped)
    failed   = total - success - skipped
    total_t  = sum(r.duration_s for r in results)

    border = _c("═" * 60, "cyan")
    log.info(border)
    log.info(_c("  📊  RUN SUMMARY", "cyan"))
    log.info(border)
    log.info("  Total files  : %d", total)
    log.info("  ✅  Succeeded : %d", success)
    log.info("  ⏭️   Skipped   : %d", skipped)
    log.info("  ❌  Failed    : %d", failed)
    log.info("  ⏱️   Duration  : %.1f s", total_t)
    log.info(border)

    if failed:
        log.info(_c("  Failed files:", "red"))
        for r in results:
            if not r.success and not r.skipped:
                log.info("    • [%s] %s — %s", r.service, r.file, r.reason)
    log.info(border)


# ──────────────────────────────────────────────
# Banner
# ──────────────────────────────────────────────

BANNER = r"""
 ██████╗ ██╗   ██╗██████╗  ██████╗ ███████╗██████╗     ██╗   ██╗██████╗
 ██╔══██╗██║   ██║██╔══██╗██╔════╝ ██╔════╝██╔══██╗    ██║   ██║╚════██╗
 ██████╔╝██║   ██║██████╔╝██║  ███╗█████╗  ██████╔╝    ██║   ██║ █████╔╝
 ██╔═══╝ ██║   ██║██╔══██╗██║   ██║██╔══╝  ██╔══██╗    ╚██╗ ██╔╝██╔═══╝
 ██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗██║  ██║     ╚████╔╝ ███████╗
 ╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝      ╚═══╝  ╚══════╝
"""


# ──────────────────────────────────────────────
# CLI argument parsing
# ──────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog        = "purger",
        description = "Production-grade, self-healing log archiver → S3",
        formatter_class = argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-c", "--config",
        default = str(__SCRIPT_DIR / "purger" / "purge_config.json"),
        help    = "Path to purge_config.json (default: ./purger/purge_config.json)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help = "Simulate the run without uploading or deleting anything",
    )
    parser.add_argument(
        "--replay-dlq", action="store_true",
        help = "Replay failed uploads from the Dead-Letter Queue before the main run",
    )
    parser.add_argument(
        "--service", metavar="NAME",
        help = "Process only the named service (default: all enabled services)",
    )
    parser.add_argument(
        "--log-level", default=None,
        choices = ["DEBUG", "INFO", "WARNING", "ERROR"],
        help    = "Override the log level from config",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__VERSION}",
    )
    return parser.parse_args()


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

def main() -> int:  # returns exit code
    global log

    args = parse_args()

    # ── Bootstrap a temporary logger until config is loaded ─────────────
    log = setup_logger(
        __SCRIPT_DIR / "purger" / "purge" / "logs",
        level = args.log_level or "INFO",
    )

    print(_c(BANNER, "red"))
    log.info(_c(f"🤖  Purge Bot {__VERSION} — starting on {__HOSTNAME}", "green"))

    # ── Register signal handlers ─────────────────────────────────────────
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ── Load & validate config ───────────────────────────────────────────
    config_path = Path(args.config)
    try:
        cfg, services = load_and_validate_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        log.critical("🛑  Cannot load config: %s", exc)
        return 1

    # Apply CLI overrides
    if args.dry_run:
        cfg.dry_run = True
    if args.log_level:
        cfg.log_level = args.log_level.upper()

    # Re-initialise logger with correct level from config
    log = setup_logger(
        __SCRIPT_DIR / "purger" / "purge" / "logs",
        level = cfg.log_level,
    )

    if cfg.dry_run:
        log.info(_c("🔵  DRY-RUN mode — no files will be uploaded or deleted.", "blue"))

    if not BOTO3_AVAILABLE:
        log.critical("❌  boto3 is required. Install with: pip install boto3")
        return 1

    # ── Build S3 client ──────────────────────────────────────────────────
    try:
        s3 = build_s3_client(cfg)
        if not cfg.dry_run:
            s3.head_bucket(Bucket=cfg.bucket)
            log.info("☁️   S3 bucket '%s' is reachable.", cfg.bucket)
    except NoCredentialsError:
        log.critical(
            "🛑  No AWS credentials found. Set AWS_ACCESS_KEY_ID / "
            "AWS_SECRET_ACCESS_KEY or use an IAM role."
        )
        return 1
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "404":
            log.critical("🛑  Bucket '%s' does not exist.", cfg.bucket)
        elif code in ("403", "AccessDenied"):
            log.critical("🛑  Access denied to bucket '%s'. Check IAM permissions.", cfg.bucket)
        else:
            log.critical("🛑  S3 connectivity check failed: %s", exc)
        return 1
    except Exception as exc:
        log.critical("🛑  S3 client error: %s", exc)
        return 1

    # ── Dead-Letter Queue ────────────────────────────────────────────────
    dlq_root = Path(cfg.dead_letter_path) if cfg.dead_letter_path else (
        __SCRIPT_DIR / "purger" / "purge" / "dlq"
    )
    dlq = DeadLetterQueue(dlq_root)

    if args.replay_dlq:
        ok, fail = replay_dead_letters(dlq, cfg, s3)
        log.info("📬  DLQ replay: %d ok, %d re-queued", ok, fail)

    # ── Filter services ──────────────────────────────────────────────────
    active_services = [
        svc for svc in services
        if svc.enabled and (args.service is None or svc.service == args.service)
    ]
    if not active_services:
        log.warning("⚠️   No active services to process (check --service filter or 'enabled' flag).")
        return 0

    # ── Main processing loop ─────────────────────────────────────────────
    all_results: list[UploadResult] = []

    for svc in active_services:
        if _SHUTDOWN:
            log.warning("Shutdown requested — stopping before service '%s'.", svc.service)
            break

        log.info(_c(f"{'─'*60}", "cyan"))
        log.info(
            "🔧  [%s] log_path=%s  pattern=%s  compress=%s  remove=%s",
            svc.service, svc.log_path, svc.pattern, svc.compress, svc.remove_on_transfer,
        )

        log_path = Path(svc.log_path)
        if not log_path.is_dir():
            log.error("❌  [%s] log_path does not exist: %s", svc.service, log_path)
            continue

        target_files = discover_files(log_path, svc.pattern)
        if not target_files:
            log.info("ℹ️   [%s] No files matched pattern '%s'", svc.service, svc.pattern)
            continue

        log.info("📁  [%s] %d file(s) to process:", svc.service, len(target_files))
        for f in target_files:
            log.debug("     • %s  (%.2f MB)", f.name, f.stat().st_size / 1_048_576)

        # ── Parallel upload using ThreadPoolExecutor ─────────────────────
        with ThreadPoolExecutor(max_workers=cfg.max_workers) as executor:
            futures = {
                executor.submit(process_file, fp, svc, cfg, s3, dlq): fp
                for fp in target_files
                if not _SHUTDOWN
            }
            for future in as_completed(futures):
                fp = futures[future]
                try:
                    result = future.result()
                    all_results.append(result)
                except Exception as exc:
                    log.error(
                        "💥  Unhandled exception processing %s:\n%s",
                        fp.name, traceback.format_exc(),
                    )
                    all_results.append(UploadResult(
                        service = svc.service,
                        file    = fp.name,
                        success = False,
                        reason  = f"Unhandled exception: {exc}",
                    ))

    # ── Summary ──────────────────────────────────────────────────────────
    print_summary(all_results)

    failed_count = sum(1 for r in all_results if not r.success and not r.skipped)
    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main())