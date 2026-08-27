#!/usr/bin/env bash
# Probe: can this (Ubuntu/WSL) machine reach a dsh web on localhost:10010?
set -u
URL="${1:-http://127.0.0.1:10010}"
echo "python: $(python3 --version)"
echo "probing dsh at $URL ..."
BODY='{"type":"client-request","rpcId":"wsl-probe-1","method":"host.describe","payload":{}}'
if command -v curl >/dev/null 2>&1; then
  curl -s -m 5 -X POST "$URL/api/host.describe" -H 'content-type: application/json' -d "$BODY" | head -c 400
else
  python3 -c "
import json, urllib.request, sys
body = json.dumps({'type':'client-request','rpcId':'wsl-probe-1','method':'host.describe','payload':{}}).encode()
req = urllib.request.Request(sys.argv[1] + '/api/host.describe', data=body, headers={'content-type':'application/json'}, method='POST')
try:
    with urllib.request.urlopen(req, timeout=5) as r:
        print(r.read().decode()[:400])
except Exception as e:
    print('ERROR:', e)
" "$URL"
fi
echo
echo "done"
