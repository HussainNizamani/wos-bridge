#!/usr/bin/env python3
import sys, time, re, subprocess, difflib, os, socket
from collections import deque

import numpy as np, cv2, requests
from rapidocr_onnxruntime import RapidOCR

# ===== CONFIG (720x1280) =====
DEVICE      = "100.92.29.31:5555"        # game box tailnet adb
WEBHOOK_URL = "https://discord.com/api/webhooks/1511959126023077948/mDxh0om2iDN7ExxDsh8j_wUjWaTfYepZGZkQce3goCunVla63RYQEh5jMlbapWjFC5oH"   # Discord webhook URL
LEFT        = 100                      # ignore avatars; content starts here (expanded to 100 to catch name brackets)
RIGHT       = 590                      # ignore right-most wizard hats / borders
CHAT_TOP    = 190                      # start below sub-tabs
CHAT_BOTTOM = 1200                     # stop above input field (extended to 1200 to capture bottom messages)
OWN_NAME    = ""                       # your in-game name e.g. "[FUX]JoyToy"
POLL_SEC    = 2.0
DUP_RATIO   = 0.85
# =============================

seen = deque(maxlen=15)

# Initialize RapidOCR with the exact same PP-OCRv5 models PaddleOCR used.
# RapidOCR runs them on ONNX Runtime — identical accuracy, stable on ARM64.
_MODELS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
ocr = RapidOCR(
    det_model_path=os.path.join(_MODELS, "PP-OCRv5_mobile_det.onnx"),
    rec_model_path=os.path.join(_MODELS, "en_PP-OCRv5_mobile_rec.onnx"),
    rec_keys_path=os.path.join(_MODELS, "en_PP-OCRv5_dict.txt"),
)

def grab():
    # Try to grab via the fast TCP port forwarder
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect(('127.0.0.1', 28888))
        data = b''
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        s.close()
        if data:
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                return img
    except Exception as e:
        # Attempt to restart the fast TCP server in the background
        try:
            subprocess.run(["adb", "-s", DEVICE, "forward", "tcp:28888", "tcp:28888"], capture_output=True)
            subprocess.Popen(
                ["adb", "-s", DEVICE, "shell",
                 "setsid nc -p 28888 -L /data/local/tmp/snap.sh >/dev/null 2>&1 &"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception:
            pass

    # Fallback to slow adb exec-out screencap
    raw = subprocess.run(["adb","-s",DEVICE,"exec-out","screencap","-p"],
                         capture_output=True).stdout
    if not raw:
        return None
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)

def adb_click(x, y):
    subprocess.run(["adb", "-s", DEVICE, "shell", "input", "tap", str(int(x)), str(int(y))])

def detect_green_bubble(frame):
    h, w = frame.shape[:2]
    roi_y_start = max(0, int(h * 0.6))
    roi_y_end = min(h, int(h * 0.95))
    roi_x_start = max(0, int(w * 0.8))
    roi_x_end = w
    
    roi = frame[roi_y_start:roi_y_end, roi_x_start:roi_x_end]
    if roi.size == 0:
        return None
        
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    lower_green = np.array([45, 50, 100])
    upper_green = np.array([85, 200, 255])
    
    mask = cv2.inRange(hsv, lower_green, upper_green)
    pts = np.argwhere(mask > 0)
    
    if len(pts) > 500:
        avg_y = pts[:, 0].mean() + roi_y_start
        avg_x = pts[:, 1].mean() + roi_x_start
        return int(avg_x), int(avg_y)
    return None

def clean_tag_and_name(tag, name_part):
    if len(tag) == 4:
        # Case 1: The 4th character is a duplicate of the first character of the name
        if name_part and tag[3].lower() == name_part[0].lower():
            tag = tag[:3]
        # Case 2: The 4th character is a common bracket/noise misread
        elif tag[3].lower() in ['i', 'j', 'l', 'h', 't', 's', 'z', 'd', 'e', '1']:
            tag = tag[:3]
        # Case 3: The 4th character is lowercase, so it belongs to the name
        elif tag[3].islower():
            name_part = tag[3] + name_part
            tag = tag[:3]
        # Case 4: The 4th character is uppercase and name starts with lowercase
        elif tag[3].isupper() and name_part and name_part[0].islower():
            name_part = tag[3] + name_part
            tag = tag[:3]
    return tag, name_part

EXCLUDED_NAME_WORDS = {
    'wanna', 'will', 'have', 'from', 'with', 'your', 'that', 'this', 'they', 'them', 'their', 'here', 'always',
    'would', 'should', 'could', 'been', 'were', 'some', 'about', 'just', 'gonna', 'dont', 'does', 'did', 'doing',
    'done', 'than', 'then', 'because', 'what', 'where', 'when', 'why', 'how', 'who', 'maybe', 'help', 'later',
    'fully', 'hope'
}

def clean_name(name_line):
    name_line = name_line.strip()
    if len(name_line) > 30:
        return None
    if any(w.lower() in EXCLUDED_NAME_WORDS for w in re.findall(r'[A-Za-z]+', name_line)):
        return None
        
    words = name_line.split()
    if not words:
        return None
        
    for w in words:
        m = re.match(r'^([A-Za-z]{3,4})[|/\\\\]([A-Za-z0-9_]{3,})$', w)
        if m:
            tag, name = clean_tag_and_name(m.group(1), m.group(2))
            return f"[{tag}]{name}"
            
    for idx, w in enumerate(words):
        m = re.match(r'^[\[{(|1I]([A-Za-z]{3,4})', w)
        if m:
            tag = m.group(1)
            rest_of_word = w[m.end():]
            name_part = re.sub(r'^[\]})|1I\-?~=:_]+', '', rest_of_word)
            if len(name_part) >= 3:
                rest_words = words[idx+1:]
                if rest_words:
                    name_part += " " + " ".join(rest_words)
                name_part = re.sub(r'[^A-Za-z0-9_ ]+', '', name_part).strip()
                tag, name_part = clean_tag_and_name(tag, name_part)
                return f"[{tag}]{name_part}"
            if idx + 1 < len(words):
                name_part = re.sub(r'^[\]})|1I\-?~=:_]+', '', words[idx+1])
                rest_words = words[idx+2:]
                if rest_words:
                    name_part += " " + " ".join(rest_words)
                name_part = re.sub(r'[^A-Za-z0-9_ ]+', '', name_part).strip()
                if len(name_part) >= 3:
                    tag, name_part = clean_tag_and_name(tag, name_part)
                    return f"[{tag}]{name_part}"
                    
    m = re.search(r'\b[\[{(|1I]?([A-Za-z]{3,4})[\]})|1I\-?~=:_]+([A-Za-z0-9_]{3,})', name_line)
    if m:
        tag, name = clean_tag_and_name(m.group(1), m.group(2))
        return f"[{tag}]{name}"
        
    return None

def clean_coordinates(text):
    text_upper = text.upper()
    x_match = re.search(r'X\s*[:\s]*(\d+)', text_upper)
    y_match = re.search(r'Y\s*[:\s]*(\d+)', text_upper)
    
    if x_match and y_match:
        return f"X:{x_match.group(1)} Y:{y_match.group(1)}"
    elif x_match:
        y_fallback = re.search(r'(?:Y\s*[:\s]*|[:\s])\s*(\d{3,4})', text_upper[x_match.end():])
        if y_fallback:
            return f"X:{x_match.group(1)} Y:{y_fallback.group(1)}"
        return f"X:{x_match.group(1)}"
        
    digits = re.findall(r'\d{3,4}', text_upper)
    if len(digits) >= 2:
        return f"X:{digits[0]} Y:{digits[1]}"
    elif len(digits) == 1:
        return f"X:{digits[0]}"
    return None

def format_alliance_label(text):
    if "alliance label" not in text.lower() and "sun label" not in text.lower():
        return text
    label_type = "Alliance label"
    if "sun label" in text.lower():
        label_type = "Sun label"
    else:
        labels = re.findall(r'([A-Za-z0-9_-]+)\s+label', text, re.IGNORECASE)
        for l in labels:
            if l.lower() not in ['alliance']:
                label_type = f"{l} label"
                break
            
    coords = clean_coordinates(text)
    if coords:
        label_type = label_type[0].upper() + label_type[1:]
        return f"{label_type} at {coords}"
    return label_type

def is_noise_word(txt):
    if re.match(r"^[«»\-\—~=:.'''\"\\_/\[\]{}<>*^|&§()©+,!""»«]+$", txt):
        return True
    return False

def clean_msg(s):
    # Remove display timestamps only at the start/end of the line (game UI clock).
    # Do NOT strip times mentioned mid-message (e.g. "cj at 19:00 utc again ?").
    s = re.sub(r'^\d{1,2}:\d{2}\s*', '', s)
    s = re.sub(r'\s+\d{1,2}:\d{2}\s*$', '', s.strip())
    # Strip graphical noise symbols, keeping standard punctuation
    s = re.sub(r'[«»\-\—~=_\\/\[\]{}<>*^|&§()©+""»«]+', ' ', s)
    # Remove leading single digit followed by space
    s = re.sub(r'^\d\s+', '', s)
    words = s.split()
    cleaned_words = []
    for w in words:
        if w in ['l', '1', '|']:
            w = 'I'
            
        if len(w) == 1 and w.lower() not in ['a', 'i', 'u', 'y', 'o', '2', '3', '4', '5', '6', '7', '8', '9', '0', '?', '!']:
            continue
        w_clean = re.sub(r'[!?.,\'\""""]+', '', w.lower())
        if w_clean in ['cs', 'ans', 'ry', 'ns', 'gaz', 'ge', 'ty', 'ce', 'ooo', 'di', 'ly', 'ange', 'atin', 'tind', 'wh', 'weet', 'vit', 'vipt', 'al', 'mll', 'm11']:
            continue
        cleaned_words.append(w)
    return " ".join(cleaned_words).strip()

def clean_and_join_lines(msg_lines):
    cleaned_lines = []
    for line in msg_lines:
        cleaned = clean_msg(line)
        if cleaned:
            cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines)

def is_reply_line(line_text):
    # Matches Name: Message where name does not contain colon and is followed by a colon and space
    m = re.match(r'^([^:\s][^:]{1,24}):\s+(.*)$', line_text)
    if not m:
        return False
        
    name_part = m.group(1).strip()
    name_lower = name_part.lower()
    
    # Common non-name words that might be followed by colon at start of line
    excluded = {
        'x', 'y', 'http', 'https', 'system', 'announcement', 'notice', 'alliance',
        'note', 'warning', 'tip', 'info', 'attention', 'congratulations',
        'first', 'second', 'third', 'step', 'level', 'stage', 'phase',
        'please', 'pls', 'ok', 'okay', 'yes', 'no', 'sure', 'thanks', 'ty',
        'date', 'time', 'utc', 'gmt', 'pst', 'est', 'cet', 'local',
        'edit', 'update', 'status', 'error', 'success', 'fail', 'failed'
    }
    
    if name_lower in excluded:
        return False
        
    if name_lower.endswith(' label'):
        return False
        
    if name_part.isdigit():
        return False
        
    # Usernames don't usually contain certain punctuation/symbols like slashes or brackets
    if any(c in name_part for c in ['/', '\\', '[', ']', '{', '}', '<', '>', '*', '=', '+']):
        return False
        
    common_words = {
        'i', 'you', 'he', 'she', 'it', 'we', 'they', 'my', 'your', 'his', 'her', 'their', 'our', 'me', 'him', 'us', 'them',
        'am', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'done',
        'go', 'goes', 'went', 'gone', 'going', 'get', 'gets', 'got', 'gotten', 'getting', 'say', 'says', 'said', 'saying',
        'make', 'makes', 'made', 'making', 'know', 'knows', 'knew', 'known', 'think', 'thinks', 'thought', 'thinking',
        'take', 'takes', 'took', 'taken', 'taking', 'see', 'sees', 'saw', 'seen', 'seeing', 'come', 'comes', 'came', 'coming',
        'want', 'wants', 'wanted', 'wanting', 'give', 'gives', 'gave', 'given', 'giving', 'tell', 'tells', 'told', 'telling',
        'work', 'works', 'worked', 'working', 'call', 'calls', 'called', 'calling', 'try', 'tries', 'tried', 'trying',
        'ask', 'asks', 'asked', 'asking', 'need', 'needs', 'needed', 'needing', 'feel', 'feels', 'felt', 'feeling',
        'become', 'becomes', 'became', 'becoming', 'leave', 'leaves', 'left', 'leaving', 'put', 'puts', 'putting',
        'mean', 'means', 'meant', 'meaning', 'keep', 'keeps', 'kept', 'keeping', 'let', 'lets', 'letting',
        'begin', 'begins', 'began', 'begun', 'beginning', 'seem', 'seems', 'seemed', 'seeming', 'help', 'helps', 'helped', 'helping',
        'talk', 'talks', 'talked', 'talking', 'start', 'starts', 'started', 'starting', 'show', 'shows', 'showed', 'shown', 'showing',
        'play', 'plays', 'played', 'playing', 'run', 'runs', 'ran', 'running', 'move', 'moves', 'moved', 'moving',
        'live', 'lives', 'lived', 'living', 'believe', 'believes', 'believed', 'believing', 'bring', 'brings', 'brought', 'bringing',
        'write', 'writes', 'wrote', 'written', 'writing', 'sit', 'sits', 'sat', 'sitting', 'stand', 'stands', 'stood', 'standing',
        'lose', 'loses', 'lost', 'losing', 'pay', 'pays', 'paid', 'paying', 'meet', 'meets', 'met', 'meeting',
        'forget', 'forgets', 'forgot', 'forgotten', 'forgetting', 'about', 'this', 'that', 'there', 'here', 'with', 'from', 'for',
        'and', 'but', 'not', 'yes', 'no', 'can', 'cant', 'could', 'couldnt', 'will', 'wont', 'would', 'wouldnt',
        'should', 'shouldnt', 'must', 'mustnt', 'shall', 'may', 'might', 'a', 'an', 'the'
    }
    if any(w in common_words for w in name_lower.split()):
        return False
        
    return True

def is_line_inside_grey_bubble(img, line):
    top, bottom, left, right = line['top'], line['bottom'], line['left'], line['right']
    crop = img[top:bottom, left:right]
    if crop.size == 0:
        return False
    b, g, r = np.median(crop, axis=(0, 1))
    diff = max(b, g, r) - min(b, g, r)
    brightness = (b + g + r) / 3
    return (diff < 15) and (85 <= brightness <= 210)

def clean_reply_lines(img, msg_lines):
    cleaned = []
    for idx, line in enumerate(msg_lines):
        line_text = line['text']
        # Safeguard: Never check or discard the first line (index 0) of the message text
        if idx > 0:
            if is_line_inside_grey_bubble(img, line) or is_reply_line(line_text):
                break
        cleaned.append(line_text)
    return cleaned

def is_name_line(text):
    text_lower = text.lower()
    if "vip" in text_lower or "fux" in text_lower or "eux" in text_lower:
        return True
    return False

def check_system_message(text):
    text_lower = text.lower()
    is_left = re.search(r'\bleft\s+(?:your\s+|the\s+|a\s+|their\s+|an\s+)*al\w*', text_lower)
    is_joined = re.search(r'\bjoined\s+(?:your\s+|the\s+|a\s+|their\s+|an\s+)*al\w*', text_lower)
    if is_left or is_joined:
        cleaned = re.sub(r'\b(your|the|their)\s+[A-Za-z0-9_]\s+al\w*', r'\1 Alliance', text, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(your|the|their)\s+al\w*', r'\1 Alliance', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(left|joined)\s+[A-Za-z0-9_]\s+al\w*', r'\1 Alliance', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(left|joined)\s+al\w*', r'\1 Alliance', cleaned, flags=re.IGNORECASE)
        return "[System]", cleaned
    if re.search(r'\b(congratulat|ongratulat|ratulat)\w*', text, re.IGNORECASE):
        normalized = re.sub(r'\b(congratulat|ongratulat|ratulat)\w*', 'Congratulations', text, flags=re.IGNORECASE)
        return "[System]", normalized
    if re.search(r'\b(system|alliance notice|announcement):', text, re.IGNORECASE):
        return "[System]", text
    return None

def has_translate_button(frame, y_min, y_max, x_start=None):
    h, w = frame.shape[:2]
    y_start = max(0, int(y_min) - 15)
    y_end = min(h, int(y_max) + 20)
    if x_start is not None:
        start_x = max(int(x_start) + 10, LEFT)
    else:
        start_x = 580
    end_x = min(635, w)
    
    if start_x >= end_x:
        return False
    roi = frame[y_start:y_end, start_x:end_x]
    if roi.size == 0:
        return False
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    lower_blue = np.array([100, 120, 150])
    upper_blue = np.array([140, 255, 255])
    mask = cv2.inRange(hsv, lower_blue, upper_blue)
    return np.sum(mask > 0) > 200

def is_seen(line):
    norm_line = line.lower()
    norm_line = re.sub(r'al[li1|]{1,3}a[n|][c|e]{1,2}', 'alliance', norm_line)
    
    is_sys = line.startswith("[System]:")
    
    for s in seen:
        s_norm = s.lower()
        s_norm = re.sub(r'al[li1|]{1,3}a[n|][c|e]{1,2}', 'alliance', s_norm)
        
        # Fast path 1: Exact normalized string equality
        if s_norm == norm_line:
            return True
            
        if is_sys:
            if not s.startswith("[System]:"):
                continue
            type1 = "left" in norm_line
            type2 = "left" in s_norm
            if type1 != type2:
                continue
            
            name1 = re.sub(r'^\[system\]:\s*|\s*(?:left|joined).*$', '', norm_line).strip()
            name2 = re.sub(r'^\[system\]:\s*|\s*(?:left|joined).*$', '', s_norm).strip()
            
            if name1 == name2:
                return True
            if len(name1) > 2 and len(name2) > 2:
                if abs(len(name1) - len(name2)) <= 3 and difflib.SequenceMatcher(None, name1, name2).ratio() >= 0.85:
                    return True
        else:
            # Fast path 2: If length difference is high, they cannot be duplicates
            if abs(len(s_norm) - len(norm_line)) > 10:
                continue
            if difflib.SequenceMatcher(None, s_norm, norm_line).ratio() >= DUP_RATIO:
                return True
    return False

def process(frame):
    # Crop to the chat content area
    crop = frame[CHAT_TOP:CHAT_BOTTOM, LEFT:RIGHT]

    # Run RapidOCR on the raw BGR crop.
    # RapidOCR returns (result, elapse) where result is a list of [box, text, score].
    # box = [[x0,y0],[x1,y1],[x2,y2],[x3,y3]] (4 corner points of the text region).
    res, _ = ocr(crop)
    if not res:
        return []

    blocks = []
    for item in res:
        box, text, score = item[0], item[1], item[2]
        text = text.strip()

        if not text or is_noise_word(text):
            continue

        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        x_min = int(min(xs)) + LEFT
        x_max = int(max(xs)) + LEFT
        y_min = int(min(ys)) + CHAT_TOP
        y_max = int(max(ys)) + CHAT_TOP

        # Filter single symbols/noise but allow letters/digits (like 'I' or 'A')
        if len(text) == 1 and x_min < 160 and not text.isalnum():
            continue
            
        blocks.append({
            'text': text,
            'left': x_min,
            'right': x_max,
            'top': y_min,
            'bottom': y_max
        })
        
    # Group blocks on the same horizontal line (within 15 vertical pixels)
    blocks.sort(key=lambda b: b['top'])
    lines_grouped = []
    for b in blocks:
        matched = None
        for line in lines_grouped:
            avg_top = sum(item['top'] for item in line) / len(line)
            if abs(b['top'] - avg_top) < 15:
                matched = line
                break
        if matched is not None:
            matched.append(b)
        else:
            lines_grouped.append([b])
            
    raw_lines = []
    for line in lines_grouped:
        line.sort(key=lambda item: item['left'])
        joined_text = " ".join(item['text'] for item in line)
        min_left = min(item['left'] for item in line)
        max_right = max(item['right'] for item in line)
        min_top = min(item['top'] for item in line)
        max_bottom = max(item['bottom'] for item in line)
        raw_lines.append({
            'text': joined_text,
            'left': min_left,
            'right': max_right,
            'top': min_top,
            'bottom': max_bottom
        })
        
    # Sort lines by vertical position
    raw_lines.sort(key=lambda l: l['top'])
    
    # Filter lines
    lines = []
    for line in raw_lines:
        line_text = line['text']
        line_text = re.sub(r'\bVIP\d+\b', '', line_text, flags=re.IGNORECASE).strip()
        
        if check_system_message(line_text) or line['left'] <= 240:
            line['text'] = line_text
            lines.append(line)
            
    messages = []
    current_sender = None
    current_msg_lines = []
    current_y_min = None
    current_y_max = None
    
    for line in lines:
        line_text = line['text']
        if 'tap' in line_text.lower() or 'enter' in line_text.lower() or 'send' in line_text.lower():
            break
        if re.match(r'^\b\d{1,2}:\d{2}\b$', line_text.strip()):
            continue
            
        sys_match = check_system_message(line_text)
        if sys_match and has_translate_button(frame, line['top'], line['bottom']):
            sys_match = None
            
        if sys_match:
            if current_sender and current_msg_lines:
                messages.append((current_sender, format_alliance_label(clean_and_join_lines(clean_reply_lines(frame, current_msg_lines))), current_y_min, current_y_max))
            current_sender, sys_text = sys_match
            current_msg_lines = [{'text': sys_text, 'left': line['left'], 'right': line['right'], 'top': line['top'], 'bottom': line['bottom']}]
            current_y_min = line['top']
            current_y_max = line['bottom']
            continue
            
        cleaned_sender = clean_name(line_text)
        if cleaned_sender:
            if current_sender and current_msg_lines:
                messages.append((current_sender, format_alliance_label(clean_and_join_lines(clean_reply_lines(frame, current_msg_lines))), current_y_min, current_y_max))
            current_sender = cleaned_sender
            current_msg_lines = []
            current_y_min = line['top']
            current_y_max = line['bottom']
        elif is_name_line(line_text):
            if current_sender and current_msg_lines:
                messages.append((current_sender, format_alliance_label(clean_and_join_lines(clean_reply_lines(frame, current_msg_lines))), current_y_min, current_y_max))
            current_sender = None
            current_msg_lines = []
            current_y_min = None
            current_y_max = None
        else:
            if current_sender:
                if current_y_max is not None and (line['top'] - current_y_max) > 70:
                    if current_sender and current_msg_lines:
                        messages.append((current_sender, format_alliance_label(clean_and_join_lines(clean_reply_lines(frame, current_msg_lines))), current_y_min, current_y_max))
                    current_sender = None
                    current_msg_lines = []
                    current_y_min = None
                    current_y_max = None
                else:
                    current_msg_lines.append(line)
                    if current_y_min is None or line['top'] < current_y_min:
                        current_y_min = line['top']
                    if current_y_max is None or line['bottom'] > current_y_max:
                        current_y_max = line['bottom']
                        
    if current_sender and current_msg_lines:
        messages.append((current_sender, format_alliance_label(clean_and_join_lines(clean_reply_lines(frame, current_msg_lines))), current_y_min, current_y_max))
        
    out = []
    for name, msg, y_min, y_max in messages:
        if not name or not msg:
            continue
        if "[discord]" in msg.lower():
            continue
        if OWN_NAME and name == OWN_NAME:
            continue
            
        is_system = (name == "[System]")
        has_btn = False
        if not is_system and y_min is not None and y_max is not None:
            if "dreamscape fusion" in msg.lower():
                has_btn = True
            else:
                x_start = LEFT
                for line in lines:
                    if line['top'] >= y_min - 5 and line['bottom'] <= y_max + 5:
                        if line['right'] > x_start:
                            x_start = line['right']
                has_btn = has_translate_button(frame, y_min, y_max, x_start)
            
        if is_system or has_btn:
            msg_lower = msg.lower()
            if "alliance gift" in msg_lower or "alliange gift" in msg_lower or ("gift" in msg_lower and "received" in msg_lower):
                continue
            line = f"{name}: {msg}"
            out.append(line)
            
    fresh = []
    for line in out:
        if not is_seen(line):
            fresh.append(line)
    for line in fresh:
        seen.append(line)
    return fresh

def post_to_discord(lines):
    if not lines: return
    buf = ""
    for ln in lines:
        if len(buf) + len(ln) + 5 > 1900: _send(buf); buf = ""
        buf += ln + "\n\n\u200b"
    if buf: _send(buf)

def _send(content):
    try: requests.post(WEBHOOK_URL, json={"content": content[:2000]}, timeout=10)
    except Exception as e: print("webhook error:", e)

prev_crop = None

def has_screen_changed(curr_frame):
    global prev_crop
    if curr_frame is None:
        return False
    curr_crop = curr_frame[CHAT_TOP:CHAT_BOTTOM, LEFT:RIGHT]
    if prev_crop is None:
        prev_crop = curr_crop
        return True
    
    diff = cv2.absdiff(prev_crop, curr_crop)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 15, 255, cv2.THRESH_BINARY)
    ratio = np.count_nonzero(thresh) / thresh.size
    
    if ratio >= 0.005:  # 0.5% pixel change threshold (captures scrolling or new message bubble)
        prev_crop = curr_crop
        return True
    return False

def cmd_run():
    global prev_crop
    prev_crop = None
    print("running… Ctrl+C to stop")
    while True:
        f = grab()
        if f is None:
            time.sleep(POLL_SEC)
            continue
            
        retries = 3
        while retries > 0:
            bubble_pos = detect_green_bubble(f)
            if bubble_pos is not None:
                x, y = bubble_pos
                print(f"Green bubble detected at ({x}, {y}). Clicking to scroll down...", flush=True)
                adb_click(x, y)
                time.sleep(1.2)
                f = grab()
                if f is None:
                    break
                retries -= 1
            else:
                break
                
        if f is None:
            time.sleep(POLL_SEC)
            continue
            
        # Skip OCR if the screen has not changed
        if not has_screen_changed(f):
            time.sleep(POLL_SEC)
            continue
            
        fresh = process(f)
        for ln in fresh:
            print(ln, flush=True)
            print(flush=True)
        post_to_discord(fresh)
        time.sleep(POLL_SEC)

if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["shot"]:
        cv2.imwrite("screen.png", grab())
        print("saved screen.png")
    else:
        cmd_run()
