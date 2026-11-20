# Gunicorn settings for the web container (see docs/deploy.md for the memory budget).

bind = "0.0.0.0:8000"
workers = 2
worker_class = "sync"
# Load Django once in the master: workers share its memory pages until they write to them.
preload_app = True
# Recycle workers now and then so memory stays flat. An open display (табло) syncs every second,
# about 3600 requests an hour each: the limit keeps a club evening with two displays (about 29000
# requests over two workers) to at most one restart per worker, and the jitter (each worker draws
# its own extra) keeps the two from restarting at the same moment.
max_requests = 20000
max_requests_jitter = 2000
timeout = 30
graceful_timeout = 20
# Heartbeat files in RAM, not on the container's overlay filesystem.
worker_tmp_dir = "/dev/shm"

accesslog = "-"
errorlog = "-"
# The client address comes from Caddy; the socket peer is always the proxy.
access_log_format = '%({x-forwarded-for}i)s "%(r)s" %(s)s %(b)s %(M)sms "%(f)s" "%(a)s"'
