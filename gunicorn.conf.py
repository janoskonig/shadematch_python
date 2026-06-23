"""Gunicorn config for the shadestudy web service.

Auto-loaded by gunicorn from the working directory, so `gunicorn run:app`
picks this up without any change to the start command (handy because the live
Render service's start command is managed in the dashboard).

Tuned for Render's free tier (512 MB RAM): a single worker avoids multiplying
the app's memory footprint, threads provide request concurrency, and
max_requests recycles the worker periodically so any leaked memory (e.g. from
the heavy /stat matplotlib/statsmodels work) is reclaimed before it can OOM.
"""
import os

# One process keeps the heavy scientific stack loaded only once. Override with
# WEB_CONCURRENCY only after confirming there's memory headroom.
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
worker_class = "gthread"

# Recycle the worker after N requests (+ jitter) to release leaked memory.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "200"))
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "50"))

# Kill requests that hang (e.g. a stuck /stat render) instead of leaking them.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))

# Don't preload: keep import-time memory in the (recyclable) worker, not the
# long-lived master, so worker recycling actually frees it.
preload_app = False
