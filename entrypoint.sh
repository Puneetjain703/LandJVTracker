#!/bin/bash
set -e

# Default mode is "both" if not specified
MODE="${MODE:-both}"

echo "Starting container in MODE: $MODE..."

if [ "$MODE" = "backend" ]; then
    echo "Starting FastAPI Backend..."
    exec uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}

elif [ "$MODE" = "frontend" ]; then
    echo "Starting Streamlit Frontend..."
    exec streamlit run frontend/streamlit_app.py --server.port ${PORT:-8501} --server.address 0.0.0.0

else
    echo "Starting BOTH Backend and Frontend..."
    
    # Start Backend in background
    uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 &
    BACKEND_PID=$!
    
    # Wait for backend to start up
    sleep 3
    
    # Start Frontend in foreground
    streamlit run frontend/streamlit_app.py --server.port ${PORT:-8501} --server.address 0.0.0.0 &
    FRONTEND_PID=$!
    
    # Trap termination signals and forward to child processes
    trap "kill $BACKEND_PID $FRONTEND_PID" SIGINT SIGTERM
    
    # Wait for both processes
    wait $BACKEND_PID $FRONTEND_PID
fi
