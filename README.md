# Whiteout Survival to Discord Chat Bridge (`wos-bridge`)

An OCR-based bridge that captures the in-game chat of **Whiteout Survival (WoS)** and forwards it to a **Discord Webhook**.

## Features

- **High-Accuracy OCR**: Powered by `RapidOCR` using the `PP-OCRv5` model running on ONNX Runtime for stable, resource-efficient extraction (compatible with ARM64/x86).
- **Reply Bubble Detection & Filtering**: Intelligent reply-bubble filtering using both visual background color checks (grey-scale detection) and name-pattern checks to discard quoted text and avoid duplicating messages.
- **System Message Detection**: Automatically extracts alliance system events (join/leave notifications, promotions, etc.) and formats them under a `[System]` sender.
- **Noise Filtering**: Automatically ignores automated spam notifications (like "Alliance Gift received" alerts).
- **Formatting Preservation**: Preserves original line breaks (`\n`) in chat messages and uses zero-width spaces (`\u200b`) to bypass Discord's message-trimming behaviors, keeping messages cleanly separated.
- **Background Service**: Can run continuously in a screen session or as a systemd user service.

---

## Important Notes & How it Works

- **Active Chat Window Required**: For the bridge to scrape messages, the target Android device must have the **Alliance Chat** or **World/State Chat** window open at all times.
- **Secure Remote Access**: The script connects to the target Android device/game box exclusively through **ADB (Android Debug Bridge)** routed over a secure **Tailscale VPN network (Tailnet)**, keeping your debug interfaces fully protected from the public internet.
- **Non-Interactive & Safe**: This is a passive reader. It **does not touch or interact with the Android device** to perform automated gameplay or actions. The only interaction it makes is clicking the chat's green scroll-down bubble when a new message alert appears to keep the chat scrolled.

---

## File Structure

- `reader.py`: The core Python scraper and OCR processing script.
- `start.sh`: Shell helper script to manage the screen session, ADB port forwarding, and background execution.
- `adb-setup.sh`: Script to configure ADB connection and screencap daemon on the target device.
- `models/`: Folder containing the lightweight PP-OCRv5 ONNX models.

---

## Requirements

- Python 3.10+
- Android device/emulator with developer options enabled (ADB debugging)
- Tailed / direct ADB network connection to the target device.

---

## Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone <your-repo-url>
   cd wos-bridge
   ```

2. **Set up a Virtual Environment**:
   ```bash
   python3 -m venv env
   source env/bin/python3
   pip install -r requirements.txt
   ```
   *(Note: Ensure `opencv-python-headless`, `numpy`, `requests`, and `rapidocr-onnxruntime` are installed.)*

3. **Configure the script**:
   Open `reader.py` and customize the config variables at the top of the file:
   - `DEVICE`: ADB tailnet IP/port of the game box.
   - `WEBHOOK_URL`: Your Discord channel webhook URL.
   - **Resolution Crops**: The coordinate variables (`LEFT`, `RIGHT`, `CHAT_TOP`, `CHAT_BOTTOM`) are calibrated and **work only on the 720x1280 resolution**. If your device/emulator uses a different resolution, you must adjust and recalibrate these crop coordinates.

4. **Launch the bridge**:
   Using `start.sh`:
   ```bash
   ./start.sh start
   ```
   To check status:
   ```bash
   ./start.sh status
   ```
   To follow logs:
   ```bash
   ./start.sh logs
   ```
