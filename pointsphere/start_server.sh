#!/usr/bin/env bash
# PointSphere – start the local Django auth server
set -e
cd "$(dirname "$0")"

echo "Starting PointSphere backend on http://127.0.0.1:8000 ..."
echo "Press Ctrl+C to stop."
echo ""
python manage.py runserver 8000
