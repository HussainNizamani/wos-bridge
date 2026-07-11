#!/bin/bash
# wos-bridge startup script
# Usage: ./start.sh [stop|status|logs]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/env/bin/python3"
READER="$SCRIPT_DIR/reader.py"
LOG="$SCRIPT_DIR/reader.log"
SCREEN_NAME="wos-bridge"
DEVICE="100.92.29.31:5555"
SNAP_SCRIPT="/data/local/tmp/snap.sh"

setup_adb() {
    echo "[setup] Connecting ADB to $DEVICE..."
    adb connect "$DEVICE" 2>&1
    adb -s "$DEVICE" forward tcp:28888 tcp:28888
    echo "[setup] Setting up screencap wrapper on device..."
    adb -s "$DEVICE" shell "printf '#!/system/bin/sh\n/system/bin/screencap -p\n' > $SNAP_SCRIPT && chmod 755 $SNAP_SCRIPT"
    echo "[setup] Starting nc listener on device (port 28888)..."
    adb -s "$DEVICE" shell "pkill -f 'nc -p 28888' 2>/dev/null; setsid nc -p 28888 -L $SNAP_SCRIPT >/dev/null 2>&1 &"
    sleep 1
    echo "[setup] ADB setup complete."
}

case "${1:-start}" in
    start)
        # Kill existing screen session if any
        screen -S "$SCREEN_NAME" -X quit 2>/dev/null || true
        sleep 1
        setup_adb
        echo "[start] Launching reader.py in screen session '$SCREEN_NAME'..."
        screen -dmS "$SCREEN_NAME" -L -Logfile "$LOG" bash -c "cd $SCRIPT_DIR && $VENV -u $READER"
        sleep 2
        if screen -list | grep -q "$SCREEN_NAME"; then
            echo "[start] ✓ wos-bridge is running! PID: $(screen -list | grep $SCREEN_NAME | awk '{print $1}' | cut -d. -f1)"
            echo "        Attach with: screen -r $SCREEN_NAME"
            echo "        Logs: tail -f $LOG"
        else
            echo "[start] ✗ Failed to start. Check $LOG for errors."
        fi
        ;;
    stop)
        screen -S "$SCREEN_NAME" -X quit 2>/dev/null && echo "Stopped." || echo "Not running."
        ;;
    status)
        if screen -list | grep -q "$SCREEN_NAME"; then
            echo "✓ Running"
            echo "Last 10 log lines:"
            tail -10 "$LOG" 2>/dev/null || echo "(no log yet)"
        else
            echo "✗ Not running"
        fi
        ;;
    logs)
        tail -f "$LOG"
        ;;
    restart)
        "$0" stop && sleep 2 && "$0" start
        ;;
    *)
        echo "Usage: $0 [start|stop|status|logs|restart]"
        ;;
esac
