#!/bin/sh
# Start the API (internal only) and the UI (public). If either exits, stop the container
# so the platform restarts it instead of serving half an app.
set -e
uvicorn api.main:app --host 127.0.0.1 --port 8000 &
API_PID=$!
streamlit run ui/streamlit_app.py --server.port 7860 &
UI_PID=$!
trap 'kill $API_PID $UI_PID 2>/dev/null' TERM INT
while kill -0 $API_PID 2>/dev/null && kill -0 $UI_PID 2>/dev/null; do sleep 5; done
echo "a process exited; stopping container" >&2
kill $API_PID $UI_PID 2>/dev/null || true
exit 1
