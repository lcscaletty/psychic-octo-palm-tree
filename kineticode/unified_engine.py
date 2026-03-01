import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import time
import json
import sys
import os
import argparse
import pyautogui
import base64

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HAND_MODEL = os.path.join(SCRIPT_DIR, 'hand_landmarker.task')
POSE_MODEL = os.path.join(SCRIPT_DIR, 'pose_landmarker.task')
FACE_MODEL = os.path.join(SCRIPT_DIR, 'face_landmarker.task')
DEBUG_WINDOW = True 

NEUTRAL_ZONE = (0.35, 0.65)
LEFT_ZONE = 0.3
RIGHT_ZONE = 0.7
AUTO_REPEAT_DELAY = 0.4
EMA_ALPHA = 0.3
FACE_EMA_ALPHA = 0.5

# Posture Settings
SLOUCH_THRESHOLD = 0.85
DROP_THRESHOLD = 0.05

# Macro Gesture Cooldowns
MACRO_COOLDOWN = 1.5

# Tilt Settings
TILT_RATIO_THRESHOLD = 0.25 # dy / distance_between_eyes (approx 15 degrees)
TILT_COOLDOWN = 0.8
last_tilt_time = 0

# --- Global Handlers ---
hand_landmarker = None
pose_landmarker = None
face_landmarker = None

def trigger_action(gesture, use_extension=False):
    if use_extension:
        print(json.dumps({"gesture": gesture}), flush=True)
    else:
        if gesture == "swipe_left":
            pyautogui.hotkey('ctrl', 'pageup') 
        elif gesture == "swipe_right":
            pyautogui.hotkey('ctrl', 'pagedown')
        elif gesture == "clap":
            pyautogui.hotkey('ctrl', 'n')
        print(f"Standalone Action: {gesture}")

def get_finger_states(landmarks):
    """Returns a list of 5 booleans [thumb, index, middle, ring, pinky] indicating if finger is UP."""
    # Landmarks: 0: Wrist, 4: Thumb Tip, 8: Index Tip, 12: Middle Tip, 16: Ring Tip, 20: Pinky Tip
    # For index/middle/ring/pinky: check if Tip y < Joint y (lower landmark index)
    # Note: y decreases as we go up in the image.
    finger_tips = [8, 12, 16, 20]
    finger_joints = [6, 10, 14, 18] # PIP joints
    
    states = []
    
    # Thumb: Check if Tip is further from wrist than the base (approximate)
    # Using x coordinate for thumb (assuming palm horizontal-ish) or distance
    # Simpler: check if thumb tip is to the left/right of the palm? 
    # Let's use distance from wrist for thumb
    thumb_tip = landmarks[4]
    thumb_base = landmarks[2]
    wrist = landmarks[0]
    
    # Simple x-axis check for thumb (assuming palm facing camera)
    # If Tip x is outside Joint2.x relative to wrist, it's out/up
    if abs(thumb_tip.x - wrist.x) > abs(thumb_base.x - wrist.x):
        states.append(True)
    else:
        states.append(False)
        
    for tip, joint in zip(finger_tips, finger_joints):
        # Tip y < Joint y means finger is pointing up (higher in image)
        states.append(landmarks[tip].y < landmarks[joint].y)
        
    return states

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--extension', action='store_true')
    parser.add_argument('--debug', type=str, choices=['true', 'false'], default='true', help='Show debug window')
    parser.add_argument('--hands', action='store_true', help='Enable Hand Tracking')
    parser.add_argument('--posture', action='store_true', help='Enable Posture Tracking')
    parser.add_argument('--face', action='store_true', help='Enable Face/Wink Tracking')
    parser.add_argument('--stream', action='store_true', help='Stream base64 frames to stdout')
    parser.add_argument('--workspace', type=str, default='', help='Target workspace path')
    parser.add_argument('--snap_threshold', type=float, default=0.05, help='Snap detection threshold')
    args = parser.parse_args()

    global DEBUG_WINDOW, hand_landmarker, pose_landmarker, face_landmarker
    DEBUG_WINDOW = args.debug == 'true'

    # Lazy Initialization based on flags
    if args.hands:
        base_hand = python.BaseOptions(model_asset_path=HAND_MODEL)
        hand_options = vision.HandLandmarkerOptions(base_options=base_hand, num_hands=2)
        hand_landmarker = vision.HandLandmarker.create_from_options(hand_options)
    
    if args.posture:
        base_pose = python.BaseOptions(model_asset_path=POSE_MODEL)
        pose_options = vision.PoseLandmarkerOptions(base_options=base_pose, num_poses=1)
        pose_landmarker = vision.PoseLandmarker.create_from_options(pose_options)

    if args.face:
        base_face = python.BaseOptions(model_asset_path=FACE_MODEL)
        face_options = vision.FaceLandmarkerOptions(
            base_options=base_face,
            output_face_blendshapes=True,
            num_faces=1
        )
        face_landmarker = vision.FaceLandmarker.create_from_options(face_options)

    # Try with CAP_DSHOW first, fallback to default
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened() or cap.read()[0] == False:
        cap.release()
        cap = cv2.VideoCapture(0)
    
    if not cap.isOpened():
        print(json.dumps({"error": "Webcam not found or busy"}), flush=True)
        if hand_landmarker: hand_landmarker.close()
        if pose_landmarker: pose_landmarker.close()
        if face_landmarker: face_landmarker.close()
        return

    # Warmup and flush
    for _ in range(15):
        cap.grab() # grab() is faster than read() for flushing
    time.sleep(1.0)
    
    # Hand State
    can_trigger = True
    last_event_time = 0
    neutral_y = None
    smoothed_x = None
    smoothed_y = None
    hand_presence_start = None

    # Posture State
    neutral_neck_dist = None
    neutral_shoulder_y = None
    posture_start_time = time.time()
    current_posture = "upright"
    
    # Wink State
    last_wink_time = 0
    left_blink_ema = 0
    right_blink_ema = 0
    wink_dwell_counter = 0
    WINK_DWELL_THRESHOLD = 3 # frames
    last_macro_time = 0
    last_tilt_time = 0
    last_stream_time = 0
    STREAM_FPS = 15

    if args.extension:
        print(json.dumps({"status": "ready"}), flush=True)

    consecutive_failures = 0
    while cap.isOpened():
        success, image = cap.read()
        if not success:
            consecutive_failures += 1
            if consecutive_failures > 30:
                print(json.dumps({"error": "Camera stream lost"}), flush=True)
                break
            continue
        consecutive_failures = 0

        image = cv2.flip(image, 1)
        h, w, _ = image.shape
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
        
        current_time = time.time()
        current_gesture = None
        hand_status = "No Hand"
        hand_box_color = (128, 128, 128)
        posture_status = "Analyzing..."
        pose_color = (255, 0, 0)
        if args.hands and hand_landmarker:
            hand_results = hand_landmarker.detect(mp_image)
            if hand_results.hand_landmarks:
                if hand_presence_start is None: hand_presence_start = time.time()

                # Primary Hand (for spikes/scrolling)
                hl = hand_results.hand_landmarks[0]
                px = (hl[0].x + hl[5].x + hl[17].x) / 3
                py = (hl[0].y + hl[5].y + hl[17].y) / 3
                if smoothed_x is None: smoothed_x, smoothed_y = px, py
                else:
                    smoothed_x = EMA_ALPHA * px + (1 - EMA_ALPHA) * smoothed_x
                    smoothed_y = EMA_ALPHA * py + (1 - EMA_ALPHA) * smoothed_y
                
                if neutral_y is None:
                    neutral_y = smoothed_y

                if NEUTRAL_ZONE[0] < smoothed_x < NEUTRAL_ZONE[1]:
                    can_trigger = True
                    neutral_y = smoothed_y
                    hand_status = "Neutral"
                    hand_box_color = (255, 0, 0)
                # Removed horizontal swipe_left/right here to use head tilt instead
                
                # --- Macro Gesture Detection ---
                if current_time - last_macro_time > MACRO_COOLDOWN:
                    fingers = get_finger_states(hl)
                    # states: [thumb, index, middle, ring, pinky]
                    
                    macro = None
                    if fingers == [False, True, True, False, False]:
                        macro = "gesture_peace"
                    elif fingers == [False, True, False, False, True]:
                        macro = "gesture_rock"
                    elif fingers == [True, True, False, False, False]:
                        macro = "gesture_l"
                        
                    if macro:
                        last_macro_time = current_time
                        trigger_action(macro, use_extension=args.extension)
                        hand_status = f"MACRO: {macro}"
                        hand_box_color = (255, 255, 0) # Gold

                if current_gesture:
                    trigger_action(current_gesture, use_extension=args.extension)
            else:
                hand_presence_start = None
                smoothed_x = smoothed_y = neutral_y = None
                can_trigger = True

        # 2. PROCESS POSE
        if args.posture and pose_landmarker:
            pose_results = pose_landmarker.detect(mp_image)
            if pose_results.pose_landmarks:
                for pl in pose_results.pose_landmarks:
                    ey = (pl[2].y + pl[5].y) / 2
                    sy = (pl[11].y + pl[12].y) / 2
                    nd = abs(sy - ey)

                    if neutral_neck_dist is None:
                        neutral_neck_dist = nd
                        neutral_shoulder_y = sy
                    
                    nr = nd / neutral_neck_dist if neutral_neck_dist else 1.0
                    sd = sy - neutral_shoulder_y if neutral_shoulder_y else 0
                    is_slouching = (nr < 0.85) or (sd > 0.05)
                    
                    state = "slouch" if is_slouching else "upright"
                    pose_color = (0, 0, 255) if is_slouching else (0, 255, 0)
                    posture_status = "🚨 SLOUCHING" if is_slouching else "✅ GOOD POSTURE"

                    if state != current_posture:
                        current_posture = state
                        if args.extension:
                            print(json.dumps({"posture": current_posture}), flush=True)

        # 3. PROCESS FACE (Wink)
        face_status = "Analyzing Face..."
        if args.face and face_landmarker:
            face_results = face_landmarker.detect(mp_image)
            if face_results.face_blendshapes:
                shapes = {c.category_name: c.score for c in face_results.face_blendshapes[0]}
                
                curr_left = shapes.get('eyeBlinkLeft', 0)
                curr_right = shapes.get('eyeBlinkRight', 0)
                
                # EMA Smoothing
                left_blink_ema = FACE_EMA_ALPHA * curr_left + (1 - FACE_EMA_ALPHA) * left_blink_ema
                right_blink_ema = FACE_EMA_ALPHA * curr_right + (1 - FACE_EMA_ALPHA) * right_blink_ema
                
                # A wink is one eye closed, other open
                wink_score = abs(left_blink_ema - right_blink_ema)
                is_winking = wink_score > 0.25 and max(left_blink_ema, right_blink_ema) > 0.35
                
                if is_winking:
                    wink_dwell_counter += 1
                    if wink_dwell_counter >= WINK_DWELL_THRESHOLD:
                        if time.time() - last_wink_time > 1.2:
                            trigger_action("clap", use_extension=args.extension)
                            last_wink_time = time.time()
                            face_status = "WINK DETECTED!"
                else:
                    wink_dwell_counter = 0
                    face_status = f"L:{left_blink_ema:.2f} R:{right_blink_ema:.2f}"

                # --- Head Tilt Detection (Face Engine) ---
                if face_results.face_landmarks and time.time() - last_tilt_time > TILT_COOLDOWN:
                    fl = face_results.face_landmarks[0]
                    # Left eye: 33, Right eye: 263
                    lx, ly = fl[33].x, fl[33].y
                    rx, ry = fl[263].x, fl[263].y
                    
                    dx = rx - lx
                    dy = ry - ly # Right eye lower than left means positive (tilt left)
                    dist = ((dx**2) + (dy**2))**0.5
                    
                    if dist > 0:
                        tilt_ratio = dy / dist
                        # Note: with image flipped, tilting head right makes right eye lower (dy > 0)
                        # We want tilting right to trigger "swipe_right"
                        if abs(tilt_ratio) > TILT_RATIO_THRESHOLD:
                            last_tilt_time = time.time()
                            gesture = "swipe_right" if tilt_ratio > 0 else "swipe_left"
                            trigger_action(gesture, use_extension=args.extension)
                            face_status = f"TILT: {gesture.upper()} ({tilt_ratio:.2f})"

        # 3. VISUALS
        if DEBUG_WINDOW:
            if args.hands:
                num_hands = len(hand_results.hand_landmarks) if hand_results and hand_results.hand_landmarks else 0
                cv2.putText(image, f"HANDS: {num_hands} | {hand_status}", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, hand_box_color, 2)
                # Draw hand boxes
                if hand_results and hand_results.hand_landmarks:
                    for hl in hand_results.hand_landmarks:
                        x_coords = [lm.x for lm in hl]
                        y_coords = [lm.y for lm in hl]
                        min_x, max_x = min(x_coords), max(x_coords)
                        min_y, max_y = min(y_coords), max(y_coords)
                        cv2.rectangle(image, (int(min_x*w), int(min_y*h)), (int(max_x*w), int(max_y*h)), hand_box_color, 2)
                        
                        # Debug fingers
                        states = get_finger_states(hl)
                        f_str = "T:{} I:{} M:{} R:{} P:{}".format(*["U" if s else "D" for s in states])
                        cv2.putText(image, f_str, (int(min_x*w), int(max_y*h)+20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            if args.posture:
                cv2.putText(image, f"POSE: {posture_status}", (50, 430), cv2.FONT_HERSHEY_SIMPLEX, 0.7, pose_color, 2)
                if pose_results and pose_results.pose_landmarks:
                    for pl in pose_results.pose_landmarks:
                        cv2.circle(image, (int(pl[0].x*w), int(pl[0].y*h)), 5, pose_color, -1)
                        cv2.line(image, (int(pl[11].x*w), int(pl[11].y*h)), (int(pl[12].x*w), int(pl[12].y*h)), pose_color, 2)

            if args.face:
                cv2.putText(image, f"FACE: {face_status}", (50, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                if face_results and face_results.face_landmarks:
                    for fl in face_results.face_landmarks:
                        # Eye Landmark Indices (approx)
                        # Left Eye: 33, 133, 159, 145
                        # Right Eye: 362, 263, 386, 374
                        for eye_indices, color in [([33, 133, 159, 145], (0, 255, 255)), ([362, 263, 386, 374], (255, 255, 0))]:
                            ex_coords = [fl[i].x for i in eye_indices]
                            ey_coords = [fl[i].y for i in eye_indices]
                            min_ex, max_ex = min(ex_coords), max(ex_coords)
                            min_ey, max_ey = min(ey_coords), max(ey_coords)
                            # Add some padding
                            padding = 0.02
                            cv2.rectangle(image, 
                                          (int((min_ex-padding)*w), int((min_ey-padding)*h)), 
                                          (int((max_ex+padding)*w), int((max_ey+padding)*h)), 
                                          color, 2)
                        # Draw Tilt Line
                        fl = face_results.face_landmarks[0]
                        p1 = (int(fl[33].x * w), int(fl[33].y * h))
                        p2 = (int(fl[263].x * w), int(fl[263].y * h))
                        cv2.line(image, p1, p2, (255, 0, 255), 2)
            
            if DEBUG_WINDOW:
                cv2.imshow('Kineticode Control Hub', image)
                if cv2.waitKey(1) & 0xFF == ord('q'): break

        # 4. STREAM TO WEBVIEW
        if args.stream and time.time() - last_stream_time > (1.0 / STREAM_FPS):
            last_stream_time = time.time()
            try:
                # Resize for performance
                small_image = cv2.resize(image, (320, 240))
                _, buffer = cv2.imencode('.jpg', small_image, [cv2.IMWRITE_JPEG_QUALITY, 70])
                jpg_as_text = base64.b64encode(buffer).decode('utf-8')
                print(json.dumps({"frame": jpg_as_text}), flush=True)
            except Exception as e:
                pass 

    cap.release()
    cv2.destroyAllWindows()
    if hand_landmarker: hand_landmarker.close()
    if pose_landmarker: pose_landmarker.close()
    if face_landmarker: face_landmarker.close()

if __name__ == "__main__":
    main()
