#!/bin/bash
# One-command launcher: kills any old instance, starts the server, opens the dashboard.
cd "$(dirname "$0")"
pkill -f "python3 server.py" 2>/dev/null
pip3 install -q -r requirements.txt
nohup python3 server.py > /tmp/sector-server.log 2>&1 &
echo "Starting… waiting for first snapshot"
until grep -q "Serving on" /tmp/sector-server.log 2>/dev/null; do sleep 2; done
open http://localhost:5050
echo "Dashboard running at http://localhost:5050 (logs: /tmp/sector-server.log)"
