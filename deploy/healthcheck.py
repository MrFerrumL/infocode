import os
import urllib.request

request = urllib.request.Request('http://127.0.0.1:8000/health/', headers={
    'Host': os.environ['APP_DOMAIN'], 'X-Forwarded-Proto': 'https',
})
with urllib.request.urlopen(request, timeout=8) as response:
    if response.status != 200:
        raise SystemExit(1)
