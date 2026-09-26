"""Container healthcheck: robots.txt answers 200, asked the way Caddy asks.

Django only accepts its own host name and redirects plain HTTP, so the request carries the first
ALLOWED_HOSTS entry and X-Forwarded-Proto: https.
"""

import os
import sys
import urllib.request

host = os.environ.get("ALLOWED_HOSTS", "").split(",")[0].strip()
request = urllib.request.Request(
    "http://127.0.0.1:8000/robots.txt",
    headers={"Host": host, "X-Forwarded-Proto": "https"},
)
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception as error:  # any failure means unhealthy
    print(error, file=sys.stderr)
    sys.exit(1)
