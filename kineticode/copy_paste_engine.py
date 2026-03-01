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
    global current_state
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
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

def get_hand_size(hand_landmarks):
    """Calculates a rough size of the hand to use as a dynamic threshold."""
    # Distance from wrist (0) to middle finger mcp (9)
    p0 = hand_landmarks[0]
    p9 = hand_landmarks[9]
    return ((p9.x - p0.x)**2 + (p9.y - p0.y)**2 + (p9.z - p0.z)**2)**0.5

def is_fist(hand_landmarks):
    """
    Determines if the hand is a fist by checking if fingertips 
    are close to the palm base (landmark 0), scaled by hand size.
    """
    palm_base = hand_landmarks[0]
    fingertips = [8, 12, 16, 20] # Index, Middle, Ring, Pinky
    hand_size = get_hand_size(hand_landmarks)
    
    if hand_size == 0: return False
    
    # Fingers must be tucked in (distance to palm < 1.2x palm length)
    threshold = hand_size * 1.2 
    
    for tip_idx in fingertips:
        tip = hand_landmarks[tip_idx]
        dist = ((tip.x - palm_base.x)**2 + (tip.y - palm_base.y)**2 + (tip.z - palm_base.z)**2)**0.5
        if dist > threshold:
            return False # A finger is extended
    return True

def is_open(hand_landmarks):
    """
    Determines if the hand is generally open (fingers extended), scaled by hand size.
    """
    palm_base = hand_landmarks[0]
    fingertips = [8, 12, 16, 20]
    hand_size = get_hand_size(hand_landmarks)
    
    if hand_size == 0: return False
    
    # Fingers must be extended (distance to palm > 1.8x palm length)
    threshold = hand_size * 1.8 
    
    open_fingers = 0
    for tip_idx in fingertips:
        tip = hand_landmarks[tip_idx]
        dist = ((tip.x - palm_base.x)**2 + (tip.y - palm_base.y)**2 + (tip.z - palm_base.z)**2)**0.5
        if dist > threshold:
            open_fingers += 1
            
    # If at least 3 fingers are fully extended, it's open
    return open_fingers >= 3

def main():
    global current_state, DEBUG_WINDOW
    
    parser = argparse.ArgumentParser(description='Copy/Paste Hand Engine')
    parser.add_argument('--extension', action='store_true', help='Run in extension mode')
    parser.add_argument('--debug', type=str, choices=['true', 'false'], default='true', help='Show debug window')
    parser.add_argument('--workspace', type=str, default='', help='Ignored')
    parser.add_argument('--stream', action='store_true', help='Stream base64 frames to stdout')
    args = parser.parse_args()

    DEBUG_WINDOW = args.debug == 'true'

    # Start stdin reader
    if args.extension:
        threading.Thread(target=read_stdin, daemon=True).start()
        print(json.dumps({"status": "ready"}), flush=True)

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        if args.extension: print(json.dumps({"error": "webcam_fail"}), flush=True)
        return

    last_stream_time = 0
    STREAM_FPS = 15
    action_cooldown = 1.5 # Increased cooldown after actions
    last_action_time = 0
    
    # Debouncing variables
    fist_frames = 0
    open_frames = 0
    REQUIRED_FRAMES = 5 # Require gesture to be held for 5 frames
    
    # We look for a *transition* from fist to open for the paste release
    was_fist_previously = False

    while cap.isOpened():
        success, image = cap.read()
        if not success: continue

        image = cv2.flip(image, 1)
        h, w, _ = image.shape
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
        
        results = landmarker.detect(mp_image)
        display_image = image.copy()
        
        status_text = "Select text in VS Code..."
        box_color = (150, 150, 150) # Gray
        
        if current_state == STATE_AWAITING_COPY:
            status_text = "MAKE A FIST TO COPY"
            box_color = (0, 165, 255) # Orange
        elif current_state == STATE_AWAITING_PASTE:
            status_text = "MOVE CURSOR, THEN OPEN HAND TO PASTE"
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
                open_frames = 0
            elif current_is_open:
                open_frames += 1
                fist_frames = 0
            else:
                fist_frames = 0
                open_frames = 0
                
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
                        was_fist_previously = True # initialize state for next step
                
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
