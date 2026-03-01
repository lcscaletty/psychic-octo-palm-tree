import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import time
import json
import sys
import os
import argparse
import subprocess
import pyautogui
import base64
import threading

HAND_MODEL = None
POSE_MODEL = None
FACE_MODEL = None

# Global state for push lock-out
last_push_trigger_time = 0.0
PUSH_LOCKOUT_DURATION = 15.0 # Seconds to ignore push gestures after a trigger

def perform_git_push_trigger(is_extension):
    """
    Signals the VS Code extension to perform the Git push sequence.
    """
    global last_push_trigger_time
    last_push_trigger_time = time.time()
    
    if is_extension:
        # Simple signal for the extension to take over the Git work
        print(json.dumps({"action": "git_push_trigger"}), flush=True)
    else:
        print("\n--- PUSH GESTURE DETECTED ---", flush=True)
        print("Note: In standalone mode, please run git commands manually.", flush=True)

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
    finger_tips = [8, 12, 16, 20]
    finger_joints = [6, 10, 14, 18] # PIP joints
    
    states = []
    thumb_tip = landmarks[4]
    thumb_ip = landmarks[3] # Interphalangeal joint
    
    if thumb_tip.y < thumb_ip.y:
        states.append(True)
    else:
        states.append(False)
        
    for tip, joint in zip(finger_tips, finger_joints):
        states.append(landmarks[tip].y < landmarks[joint].y)
        
    return states

# --- State Helpers ---
STATE_IDLE = "IDLE"
STATE_AWAITING_COPY = "AWAITING_COPY"
STATE_AWAITING_PASTE = "AWAITING_PASTE"
current_state = STATE_IDLE
shutdown_flag = False

def read_stdin():
    global current_state, shutdown_flag
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                shutdown_flag = True
                break
            msg = json.loads(line.strip())
            if msg.get("event") == "selection_changed":
                has_selection = msg.get("hasSelection", False)
                if has_selection:
                    current_state = STATE_AWAITING_COPY
                elif not has_selection and current_state == STATE_AWAITING_COPY:
                    current_state = STATE_IDLE
        except Exception:
            pass

def get_distance(p1, p2):
    return ((p1.x - p2.x)**2 + (p1.y - p2.y)**2 + (p1.z - p2.z)**2)**0.5

def get_hand_size(hl):
    return get_distance(hl[0], hl[9])

def is_fist(hl):
    wrist = hl[0]
    fingers = [(5, 8), (9, 12), (13, 16), (17, 20)]
    sz = get_hand_size(hl)
    if sz == 0: return False
    for i, (mcp_idx, tip_idx) in enumerate(fingers):
        dist_mcp = get_distance(wrist, hl[mcp_idx])
        dist_tip = get_distance(wrist, hl[tip_idx])
        if dist_tip > dist_mcp * 1.1 or dist_tip > sz * 1.8: 
            return False
    return True

def is_open(hl):
    wrist = hl[0]
    fingers = [(5, 8), (9, 12), (13, 16), (17, 20)]
    sz = get_hand_size(hl)
    if sz == 0: return False
    open_count = 0
    for i, (mcp_idx, tip_idx) in enumerate(fingers):
        dist_mcp = get_distance(wrist, hl[mcp_idx])
        dist_tip = get_distance(wrist, hl[tip_idx])
        if dist_tip > dist_mcp * 1.15 and dist_tip > sz * 1.4: 
            open_count += 1
    return open_count >= 3

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--extension', action='store_true')
    parser.add_argument('--debug', type=str, choices=['true', 'false'], default='false', help='Show debug window')
    parser.add_argument('--hands', action='store_true', help='Enable Hand Tracking')
    parser.add_argument('--posture', action='store_true', help='Enable Posture Tracking')
    parser.add_argument('--face', action='store_true', help='Enable Face/Wink Tracking')
    parser.add_argument('--copy_paste', action='store_true', help='Enable Copy/Paste Tracking')
    parser.add_argument('--push', action='store_true', help='Enable Push-to-GitHub Tracking')
    parser.add_argument('--undo', action='store_true', help='Enable Undo Gesture Tracking')
    parser.add_argument('--stream', action='store_true', help='Stream base64 frames to stdout')
    parser.add_argument('--workspace', type=str, default='', help='Target workspace path')
    parser.add_argument('--snap_threshold', type=float, default=0.05, help='Snap detection threshold')
    args = parser.parse_args()

    global DEBUG_WINDOW, hand_landmarker, pose_landmarker, face_landmarker, current_state
    DEBUG_WINDOW = args.debug == 'true'

    # Dependencies
    if args.copy_paste or args.undo: args.hands = True
    if args.push: args.posture = True

    # Initialization
    if args.hands:
        base_hand = python.BaseOptions(model_asset_path=HAND_MODEL)
        hand_options = vision.HandLandmarkerOptions(base_options=base_hand, num_hands=2)
        hand_landmarker = vision.HandLandmarker.create_from_options(hand_options)
    
    if args.posture or args.push:
        base_pose = python.BaseOptions(model_asset_path=POSE_MODEL)
        pose_options = vision.PoseLandmarkerOptions(base_options=base_pose, num_poses=1)
        pose_landmarker = vision.PoseLandmarker.create_from_options(pose_options)

    if args.face:
        base_face = python.BaseOptions(model_asset_path=FACE_MODEL)
        face_options = vision.FaceLandmarkerOptions(base_options=base_face, output_face_blendshapes=True, num_faces=1)
        face_landmarker = vision.FaceLandmarker.create_from_options(face_options)

    # Camera Logic
    with open('camera_debug_log.txt', 'w') as log_file:
        log_file.write("Starting camera initialization...\n")
        cap = None
        for cam_idx in range(5):
            test_cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
            if test_cap.isOpened():
                valid = False
                for j in range(20):
                    success, img = test_cap.read()
                    if success and img is not None and img.max() > 15:
                        valid = True
                        break
                    time.sleep(0.1)
                if valid:
                    cap = test_cap
                    break
                test_cap.release()
        if cap is None:
            cap = cv2.VideoCapture(0)
    
    if not cap.isOpened():
        print(json.dumps({"error": "Webcam not found"}), flush=True)
        return

    # States
    neutral_neck_dist = None
    neutral_shoulder_y = None
    current_posture = "upright"
    last_wink_time = 0
    left_blink_ema = 0
    right_blink_ema = 0
    wink_dwell_counter = 0
    WINK_DWELL_THRESHOLD = 3
    last_macro_time = 0
    last_tilt_time = 0
    last_stream_time = 0
    STREAM_FPS = 15

    if args.extension:
        threading.Thread(target=read_stdin, daemon=True).start()
        print(json.dumps({"status": "ready"}), flush=True)

    # Hand/Action States
    fist_frames = 0
    open_frames = 0
    REQUIRED_FRAMES = 3
    was_fist_previously = False
    action_cooldown = 1.5
    last_cp_action_time = 0

    PUSH_STATE_MONITORING = "MONITORING"
    PUSH_STATE_AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    push_state = PUSH_STATE_MONITORING
    neutral_dist = None
    smoothed_ratio = None
    last_push_time = 0
    push_start_time = time.time()
    confirmation_start_time = 0
    CONFIRM_TIMEOUT = 10.0
    PUSH_THRESHOLD = 0.82
    PUSH_COOLDOWN = 5.0
    WARMUP_TIME = 2.0

    UNDO_STATE_IDLE = "IDLE"
    UNDO_STATE_TOUCH = "TOUCH"
    undo_state = UNDO_STATE_IDLE
    last_undo_time = 0
    undo_touch_start = 0

    consecutive_failures = 0
    while cap.isOpened() and not shutdown_flag:
        success, image = cap.read()
        if not success:
            consecutive_failures += 1
            if consecutive_failures > 30: break
            continue
        consecutive_failures = 0
        
        # Blank detection
        if image.max() < 15:
            image[:, :] = (50, 50, 50)
            cv2.putText(image, "CAMERA FEED BLACK", (50, 200), cv2.FONT_HERSHEY_DUPLEX, 1.0, (0, 0, 255), 2)

        image = cv2.flip(image, 1)
        h, w, _ = image.shape
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
        
        current_time = time.time()
        hand_status = "No Hand"
        hand_box_color = (128, 128, 128)
        posture_status = "Analyzing..."
        pose_color = (255, 0, 0)
        face_status = "Analyzing..."
        
        hand_results = None
        pose_results = None
        face_results = None
        
        # 1. HANDS
        if args.hands and hand_landmarker:
            try:
                hand_results = hand_landmarker.detect(mp_image)
            except: pass
            
            if hand_results and hand_results.hand_landmarks:
                hl = hand_results.hand_landmarks[0]
                fingers = get_finger_states(hl)
                
                # Macros
                if current_state != STATE_AWAITING_COPY and current_time - last_macro_time > MACRO_COOLDOWN:
                    macro = None
                    if fingers == [False, True, False, False, False]: macro = "gesture_one"
                    elif fingers[1:] == [True, True, False, False]: macro = "gesture_peace"
                    elif fingers[1:] == [True, False, False, True]: macro = "gesture_rock"
                    elif fingers == [True, True, False, False, False]: macro = "gesture_l"
                    if macro:
                        last_macro_time = current_time
                        trigger_action(macro, use_extension=args.extension)
                        hand_status = f"MACRO: {macro}"

                # Copy/Paste
                if args.copy_paste:
                    current_is_fist = is_fist(hl)
                    current_is_open = is_open(hl)
                    if current_is_fist: fist_frames += 1
                    elif current_is_open: open_frames += 1
                    
                    if current_time - last_cp_action_time > action_cooldown:
                        if current_state == STATE_AWAITING_COPY and fist_frames >= REQUIRED_FRAMES:
                            print(json.dumps({"action": "copy"}), flush=True)
                            current_state = STATE_AWAITING_PASTE
                            last_cp_action_time = current_time
                        elif current_state == STATE_AWAITING_PASTE:
                            if is_fist(hl): was_fist_previously = True
                            if was_fist_previously and open_frames >= REQUIRED_FRAMES:
                                print(json.dumps({"action": "paste"}), flush=True)
                                current_state = STATE_IDLE
                                last_cp_action_time = current_time
                                was_fist_previously = False

                # Undo (OK Sign)
                if args.undo:
                    dist_ti = ((hl[4].x - hl[8].x)**2 + (hl[4].y - hl[8].y)**2)**0.5
                    sz = get_hand_size(hl)
                    if dist_ti < sz * 0.4 and fingers[2:] == [True, True, True]:
                        if undo_state == UNDO_STATE_IDLE:
                            undo_state = UNDO_STATE_TOUCH
                            undo_touch_start = current_time
                        elif current_time - undo_touch_start > 0.4:
                            if current_time - last_undo_time > 1.5:
                                if args.extension: print(json.dumps({"action": "undo"}), flush=True)
                                last_undo_time = current_time
                                undo_state = UNDO_STATE_IDLE
                    else: undo_state = UNDO_STATE_IDLE

        # 2. POSE & PUSH
        if (args.posture or args.push) and pose_landmarker:
            try:
                pose_results = pose_landmarker.detect(mp_image)
            except: pass
            if pose_results and pose_results.pose_landmarks:
                for pl in pose_results.pose_landmarks:
                    if len(pl) < 17: continue
                    ey = (pl[2].y + pl[5].y) / 2
                    sy = (pl[11].y + pl[12].y) / 2
                    nd = abs(sy - ey)
                    eye_dist = ((pl[2].x - pl[5].x)**2 + (pl[2].y - pl[5].y)**2)**0.5
                    
                    if args.posture:
                        if neutral_neck_dist is None:
                            neutral_neck_dist = nd
                            neutral_shoulder_y = sy
                        is_slouching = (nd / neutral_neck_dist < 0.85) or (sy - neutral_shoulder_y > 0.05)
                        state = "slouch" if is_slouching else "upright"
                        if state != current_posture:
                            current_posture = state
                            if args.extension: print(json.dumps({"posture": current_posture}), flush=True)
                    
                    if args.push:
                        if time.time() - last_push_trigger_time > PUSH_LOCKOUT_DURATION:
                            if time.time() - push_start_time < WARMUP_TIME:
                                neutral_dist = 0.1 * eye_dist + 0.9 * neutral_dist if neutral_dist else eye_dist
                            else:
                                ratio = eye_dist / neutral_dist if neutral_dist else 1.0
                                smoothed_ratio = 0.4 * ratio + 0.6 * smoothed_ratio if smoothed_ratio else ratio
                                if push_state == PUSH_STATE_MONITORING:
                                    if smoothed_ratio < PUSH_THRESHOLD and (current_time - last_push_time > PUSH_COOLDOWN):
                                        push_state = PUSH_STATE_AWAITING_CONFIRMATION
                                        confirmation_start_time = current_time
                                        if args.extension: print(json.dumps({"status": "awaiting_confirmation"}), flush=True)
                                elif push_state == PUSH_STATE_AWAITING_CONFIRMATION:
                                    hands_up = (pl[15].y < sy and pl[16].y < sy)
                                    if hands_up:
                                        perform_git_push_trigger(args.extension)
                                        last_push_time = current_time
                                        push_state = PUSH_STATE_MONITORING
                                    elif current_time - confirmation_start_time > CONFIRM_TIMEOUT:
                                        push_state = PUSH_STATE_MONITORING

        # 3. FACE
        if args.face and face_landmarker:
            try:
                face_results = face_landmarker.detect(mp_image)
            except: pass
            if face_results and face_results.face_blendshapes:
                shapes = {c.category_name: c.score for c in face_results.face_blendshapes[0]}
                left_blink_ema = FACE_EMA_ALPHA * shapes.get('eyeBlinkLeft', 0) + (1 - FACE_EMA_ALPHA) * left_blink_ema
                right_blink_ema = FACE_EMA_ALPHA * shapes.get('eyeBlinkRight', 0) + (1 - FACE_EMA_ALPHA) * right_blink_ema
                if abs(left_blink_ema - right_blink_ema) > 0.3 and max(left_blink_ema, right_blink_ema) > 0.4:
                    wink_dwell_counter += 1
                    if wink_dwell_counter >= WINK_DWELL_THRESHOLD and current_time - last_wink_time > 1.2:
                        trigger_action("clap", use_extension=args.extension)
                        last_wink_time = current_time
                else: wink_dwell_counter = 0

                if face_results.face_landmarks and current_time - last_tilt_time > TILT_COOLDOWN:
                    fl = face_results.face_landmarks[0]
                    dx = fl[263].x - fl[33].x
                    dy = fl[263].y - fl[33].y
                    dist = (dx**2 + dy**2)**0.5
                    if dist > 0 and abs(dy/dist) > TILT_RATIO_THRESHOLD:
                        last_tilt_time = current_time
                        trigger_action("swipe_right" if dy > 0 else "swipe_left", use_extension=args.extension)

        # Visuals
        if DEBUG_WINDOW:
            fps = 1.0 / (time.time() - current_time) if (time.time() - current_time) > 0 else 0
            cv2.putText(image, f"FPS: {fps:.1f}", (w - 150, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.imshow('Kineticode Control Hub', image)
            if cv2.waitKey(1) & 0xFF == ord('q'): break

        # Stream
        if args.stream and current_time - last_stream_time > (1.0 / STREAM_FPS):
            last_stream_time = current_time
            try:
                small = cv2.resize(image, (320, 240))
                _, buf = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 70])
                print(json.dumps({"frame": base64.b64encode(buf).decode('utf-8')}), flush=True)
            except: pass

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
