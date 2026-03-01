import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import time
import json
import sys
import os
import argparse
import base64
import threading

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(SCRIPT_DIR, 'hand_landmarker.task')
DEBUG_WINDOW = True 

# State Machine
STATE_IDLE = "IDLE"
STATE_AWAITING_COPY = "AWAITING_COPY"
STATE_AWAITING_PASTE = "AWAITING_PASTE"
current_state = STATE_IDLE
shutdown_flag = False

# --- MediaPipe Task Initialization ---
if not os.path.exists(MODEL_PATH):
    print(f"Error: Model file {MODEL_PATH} not found.")
    sys.exit(1)

base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
options = vision.HandLandmarkerOptions(
    base_options=base_options,
    num_hands=1, # Only need one hand for copy/paste
    min_hand_detection_confidence=0.7,
    min_hand_presence_confidence=0.5,
    min_tracking_confidence=0.5
)
landmarker = vision.HandLandmarker.create_from_options(options)

# --- Stdin Reader Thread ---
def read_stdin():
    global current_state, shutdown_flag
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                # Extension closed the stream (killed process)
                shutdown_flag = True
                break
            msg = json.loads(line.strip())
            if msg.get("event") == "selection_changed":
                has_selection = msg.get("hasSelection", False)
                
                # Only transition if we are IDLE. If we are waiting to paste, don't interrupt.
                # If they highlight new text while waiting to paste, we cancel the paste
                # and prepare to copy the new text instead.
                if has_selection:
                    current_state = STATE_AWAITING_COPY
                elif not has_selection and current_state == STATE_AWAITING_COPY:
                     # Selection cleared before copy
                    current_state = STATE_IDLE
        except Exception:
            pass

def get_distance(p1, p2):
    return ((p1.x - p2.x)**2 + (p1.y - p2.y)**2 + (p1.z - p2.z)**2)**0.5

def get_hand_size(hand_landmarks):
    """Calculate baseline palm size from wrist to middle finger MCP."""
    return get_distance(hand_landmarks[0], hand_landmarks[9])

def is_fist(hand_landmarks):
    """
    Determines if the hand is a strict fist:
    1. Fingertips must be closer to the wrist than their respective MCP joints.
    2. Fingertips must be physically close to the wrist (tightly curled).
    """
    wrist = hand_landmarks[0]
    fingers = [(5, 8), (9, 12), (13, 16), (17, 20)] # (MCP, TIP)
    hand_size = get_hand_size(hand_landmarks)
    
    if hand_size == 0: return False
    
    for mcp_idx, tip_idx in fingers:
        mcp = hand_landmarks[mcp_idx]
        tip = hand_landmarks[tip_idx]
        
        dist_mcp = get_distance(wrist, mcp)
        dist_tip = get_distance(wrist, tip)
        
        # 1. Tip must be tucked closely relative to the MCP
        if dist_tip > dist_mcp * 1.1:
            return False
            
        # 2. Tip must be close to the wrist (tight curl)
        if dist_tip > hand_size * 1.8:
            return False
            
    return True

def is_open(hand_landmarks):
    """
    Determines if the hand is strictly open:
    1. Fingertips must be much further from the wrist than the MCP joints.
    2. Fingertips must be physically far from the wrist (fully extended).
    """
    wrist = hand_landmarks[0]
    fingers = [(5, 8), (9, 12), (13, 16), (17, 20)]
    hand_size = get_hand_size(hand_landmarks)
    
    if hand_size == 0: return False
    
    open_fingers = 0
    for mcp_idx, tip_idx in fingers:
        mcp = hand_landmarks[mcp_idx]
        tip = hand_landmarks[tip_idx]
        
        dist_mcp = get_distance(wrist, mcp)
        dist_tip = get_distance(wrist, tip)
        
        # 1. Tip must be extended past the MCP
        if dist_tip > dist_mcp * 1.15:
            # 2. Tip must be far from the wrist
            if dist_tip > hand_size * 1.4:
                open_fingers += 1
            
    # Require at least 3 fingers to be fully open
    return open_fingers >= 3

def main():
    global current_state, DEBUG_WINDOW
    
    parser = argparse.ArgumentParser(description='Copy/Paste Hand Engine')
    parser.add_argument('--extension', action='store_true', help='Run in extension mode')
    parser.add_argument('--debug', type=str, choices=['true', 'false'], default='true', help='Show debug window')
    parser.add_argument('--workspace', type=str, default='', help='Ignored')
    parser.add_argument('--stream', action='store_true', help='Stream base64 frames to stdout')
    args, _ = parser.parse_known_args()

    DEBUG_WINDOW = args.debug == 'true'

    # Start stdin reader
    if args.extension:
        threading.Thread(target=read_stdin, daemon=True).start()
        print(json.dumps({"status": "ready"}), flush=True)

    cap = None
    for cam_idx in range(5):
        test_cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
        if test_cap.isOpened():
            # Let sensor warm up and drop black frames typical of virtual/dummy cameras
            valid = False
            for _ in range(10):
                success, img = test_cap.read()
                if success and img is not None and img.max() > 10:
                    valid = True
                    break
                time.sleep(0.1)
            if valid:
                cap = test_cap
                break
            test_cap.release()
            
    if cap is None:
        test_cap = cv2.VideoCapture(0)
        if test_cap.isOpened():
            cap = test_cap

    if cap is None or not cap.isOpened():
        if args.extension: print(json.dumps({"error": "webcam_fail"}), flush=True)
        return

    last_stream_time = 0
    STREAM_FPS = 15
    action_cooldown = 1.5 # Increased cooldown after actions
    last_action_time = 0
    
    # Debouncing variables
    fist_frames = 0
    open_frames = 0
    REQUIRED_FRAMES = 3 # Reduced hold time for snappier gestures
    
    # We look for a *transition* from fist to open for the paste release
    was_fist_previously = False

    while cap.isOpened() and not shutdown_flag:
        success, image = cap.read()
        if not success or image is None:
            if DEBUG_WINDOW:
                if cv2.waitKey(1) & 0xFF == 27: break
            continue

        image = cv2.flip(image, 1)
        h, w, _ = image.shape
        
        # Check if the camera is returning pure black frames (privacy shutter closed)
        is_blank = image.max() < 15
        if is_blank:
            # Create a blank gray canvas so text is highly visible
            image[:, :] = (50, 50, 50)
            cv2.putText(image, "CAMERA IS BLACK / COVERED", (10, h // 2 - 20), cv2.FONT_HERSHEY_DUPLEX, 0.6, (0, 0, 255), 2)
            cv2.putText(image, "Check privacy shutter or permissions", (10, h // 2 + 20), cv2.FONT_HERSHEY_DUPLEX, 0.4, (0, 0, 255), 1)

        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
        
        if not is_blank:
            results = landmarker.detect(mp_image)
        else:
            class DummyResults:
                hand_landmarks = []
            results = DummyResults()
            
        display_image = image.copy()
        
        status_text = "Highlight text to start"
        box_color = (150, 150, 150) # Gray
        
        if current_state == STATE_AWAITING_COPY:
            status_text = "FIST = COPY"
            box_color = (0, 165, 255) # Orange
        elif current_state == STATE_AWAITING_PASTE:
            status_text = "OPEN = PASTE"
            box_color = (255, 0, 255) # Purple

        if results.hand_landmarks:
            hand = results.hand_landmarks[0]
            
            # Manual skeleton drawing (cv2)
            def get_p(idx):
                return (int(hand[idx].x * w), int(hand[idx].y * h))
                
            connections = [
                (0,1), (1,2), (2,3), (3,4), # Thumb
                (0,5), (5,6), (6,7), (7,8), # Index
                (0,9), (9,10), (10,11), (11,12), # Middle
                (0,13), (13,14), (14,15), (15,16), # Ring
                (0,17), (17,18), (18,19), (19,20), # Pinky
                (5,9), (9,13), (13,17) # Palm base
            ]
            
            for p1_idx, p2_idx in connections:
                cv2.line(display_image, get_p(p1_idx), get_p(p2_idx), (255, 255, 255), 2)
            for i in range(21):
                cv2.circle(display_image, get_p(i), 3, (0, 255, 0), -1)
                
            # Logic with Debounce
            current_is_fist = is_fist(hand)
            current_is_open = is_open(hand)
            
            if current_is_fist:
                fist_frames += 1
                open_frames = max(0, open_frames - 1)
            elif current_is_open:
                open_frames += 1
                fist_frames = max(0, fist_frames - 1)
            else:
                fist_frames = max(0, fist_frames - 1)
                open_frames = max(0, open_frames - 1)
                
            is_stable_fist = fist_frames >= REQUIRED_FRAMES
            is_stable_open = open_frames >= REQUIRED_FRAMES
            
            if time.time() - last_action_time > action_cooldown:
                if current_state == STATE_AWAITING_COPY:
                    if is_stable_fist:
                        # COPY TRIGGERED
                        print(json.dumps({"action": "copy"}), flush=True)
                        current_state = STATE_AWAITING_PASTE
                        last_action_time = time.time()
                        status_text = "COPIED!"
                        box_color = (0, 255, 0)
                        was_fist_previously = False # Ensure user holds fist or makes a new one
                
                elif current_state == STATE_AWAITING_PASTE:
                    # Maintain fist state memory
                    if is_stable_fist:
                        was_fist_previously = True
                        
                    # Look for release (was stably fist, now stably open)
                    if was_fist_previously and is_stable_open:
                        # PASTE TRIGGERED
                        print(json.dumps({"action": "paste"}), flush=True)
                        current_state = STATE_IDLE
                        last_action_time = time.time()
                        status_text = "PASTED!"
                        box_color = (0, 255, 0)
                        was_fist_previously = False

        # Visuals
        cv2.putText(display_image, status_text, (30, 50), cv2.FONT_HERSHEY_DUPLEX, 0.8, box_color, 2)
        
        if current_state != STATE_IDLE:
             border_thickness = 10 if int(time.time() * 4) % 2 == 0 else 4
             cv2.rectangle(display_image, (0, 0), (w, h), box_color, border_thickness)
             
        # Stream
        if args.stream and time.time() - last_stream_time > (1.0 / STREAM_FPS):
            last_stream_time = time.time()
            try:
                small_image = cv2.resize(display_image, (320, 240))
                _, buffer = cv2.imencode('.jpg', small_image, [cv2.IMWRITE_JPEG_QUALITY, 80])
                jpg_as_text = base64.b64encode(buffer).decode('utf-8')
                print(json.dumps({"frame": jpg_as_text}), flush=True)
            except Exception:
                pass

        if DEBUG_WINDOW:
            cv2.imshow('Copy/Paste Engine Preview', display_image)
            if cv2.waitKey(1) & 0xFF == 27: break

    cap.release()
    cv2.destroyAllWindows()
    landmarker.close()

if __name__ == "__main__":
    main()
