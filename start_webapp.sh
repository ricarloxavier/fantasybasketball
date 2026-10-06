#!/bin/bash
# Start the Putilla Awards web server

echo "🏆 Starting Putilla Awards Web Server..."
echo ""

cd "$(dirname "$0")"
source .venv/bin/activate

python web_app.py
