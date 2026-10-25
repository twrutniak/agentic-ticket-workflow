#!/usr/bin/env bash
# Sends the example tickets to a running service, approves the T-2 refund and prints the results.
set -euo pipefail
API=${API:-http://localhost:8000}
TICKETS=$(dirname "$0")/../app/data/tickets.json

python3 -c "import json,sys; [print(json.dumps(t)) for t in json.load(open(sys.argv[1]))]" "$TICKETS" |
while read -r ticket; do
  curl -s -X POST "$API/tickets" -H 'Content-Type: application/json' -d "$ticket"; echo
done

echo "--- duplicate T-1:"
curl -s -X POST "$API/tickets" -H 'Content-Type: application/json' \
  -d '{"ticket_id": "T-1", "customer_id": "C-200", "subject": "Can'"'"'t log in", "body": "again"}'; echo

echo "--- waiting for T-2 to need approval"
for _ in $(seq 30); do
  step=$(curl -s "$API/tickets/T-2" | python3 -c "import json,sys; print(json.load(sys.stdin)['state']['step'])")
  [ "$step" = "awaiting_approval" ] && break
  sleep 1
done
curl -s -X POST "$API/tickets/T-2/approval" -H 'Content-Type: application/json' \
  -d '{"approved": true, "reviewer": "anna", "comment": "duplicate charge confirmed"}' >/dev/null

sleep 5
echo "--- results"
for id in T-1 T-2 T-3 T-4 T-5 T-6 T-7; do
  curl -s "$API/tickets/$id" | python3 -c "
import json,sys
v = json.load(sys.stdin); s = v['state']; d = s['decision'] or {}
print(f\"{v['ticket_id']}: {v['status']:<9} step={s['step']:<17} action={d.get('action')} \
category={d.get('category')} priority={d.get('priority')} ref={(s['result'] or {}).get('helpdesk_reference')}\")"
done
