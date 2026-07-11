#!/bin/bash
# ADB pre-flight: connect device, forward port, ensure nc screencap listener is up
# Run by systemd as ExecStartPre before reader.py

DEVICE="100.92.29.31:5555"
SNAP="/data/local/tmp/snap.sh"

echo "[$0] Connecting ADB to $DEVICE..."
adb connect "$DEVICE"

echo "[$0] Forwarding tcp:28888..."
adb -s "$DEVICE" forward tcp:28888 tcp:28888

echo "[$0] Writing screencap wrapper to device..."
adb -s "$DEVICE" shell "printf '#!/system/bin/sh\n/system/bin/screencap -p\n' > $SNAP && chmod 755 $SNAP"

echo "[$0] Killing old nc listeners and starting fresh..."
adb -s "$DEVICE" shell "pkill -f 'nc -p 28888' 2>/dev/null; setsid nc -p 28888 -L $SNAP >/dev/null 2>&1 &"

sleep 1
echo "[$0] ADB pre-flight complete."
