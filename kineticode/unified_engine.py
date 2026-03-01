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

def perform_git_push_async(workspace_path, script_dir, ratio, is_extension):
    """
    Executes the git sequence asynchronously to avoid freezing the camera.
    """
    def task():
        print("\n--- ATTEMPTING GIT PUSH ---", flush=True)
        try:
            target_dir = workspace_path if workspace_path else script_dir
            root_res = subprocess.run(["git", "rev-parse", "--show-toplevel"], 
                                   cwd=target_dir, capture_output=True, text=True, check=True)
            git_root = root_res.stdout.strip()
            
            subprocess.run(["git", "add", "."], cwd=git_root, check=True)
            
            status_res = subprocess.run(["git", "status", "--porcelain"], 
                                     cwd=git_root, capture_output=True, text=True, check=True)
            if not status_res.stdout.strip():
                print("--- NOTHING TO COMMIT ---", flush=True)
            else:
                commit_msg = f"Auto-push from Kineticode Push Engine: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                subprocess.run(["git", "commit", "-m", commit_msg], cwd=git_root, check=True)
            
            subprocess.run(["git", "push"], cwd=git_root, check=True, timeout=10)
            print("--- GIT PUSH SUCCESSFUL ---", flush=True)
            if is_extension:
                print(json.dumps({"action": "git_push", "success": True, "ratio": ratio}), flush=True)
        except subprocess.TimeoutExpired:
            print("--- GIT ERROR: Timeout (May need credentials) ---", flush=True)
            if is_extension:
                print(json.dumps({"action": "git_push", "success": False, "ratio": ratio, "error": "Timeout"}), flush=True)
        except Exception as e:
            print(f"--- GIT ERROR: {e} ---", flush=True)
            if is_extension:
                print(json.dumps({"action": "git_push", "success": False, "ratio": ratio, "error": str(e)}), flush=True)

    threading.Thread(target=task, daemon=True).start()

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
    
    # Thumb: Check if Tip is pointing "up" (lower y value) relative to the IP joint
    # For a fist the thumb is tucked down/in. For a thumbs up it points up.
    # For simple rock on/peace, thumb is usually tucked under the other fingers.
    thumb_tip = landmarks[4]
    thumb_ip = landmarks[3] # Interphalangeal joint
    
    if thumb_tip.y < thumb_ip.y:
        states.append(True)
    else:
        states.append(False)
        
    for tip, joint in zip(finger_tips, finger_joints):
        # Tip y < Joint y means finger is pointing up (higher in image)
        states.append(landmarks[tip].y < landmarks[joint].y)
        
    return states

# --- Copy/Paste Helpers ---
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
    
    # print(f"--- FIST CHECK --- sz: {sz:.4f}")
    for i, (mcp_idx, tip_idx) in enumerate(fingers):
        dist_mcp = get_distance(wrist, hl[mcp_idx])
        dist_tip = get_distance(wrist, hl[tip_idx])
        # print(f"F{i} tip: {dist_tip:.4f}, mcp*1.1: {dist_mcp*1.1:.4f}, sz*1.8: {sz*1.8:.4f}")
        if dist_tip > dist_mcp * 1.1 or dist_tip > sz * 1.8: 
            return False
    return True

def is_open(hl):
    wrist = hl[0]
    fingers = [(5, 8), (9, 12), (13, 16), (17, 20)]
    sz = get_hand_size(hl)
    if sz == 0: return False
    
    open_count = 0
    # print(f"--- OPEN CHECK --- sz: {sz:.4f}")
    for i, (mcp_idx, tip_idx) in enumerate(fingers):
        dist_mcp = get_distance(wrist, hl[mcp_idx])
        dist_tip = get_distance(wrist, hl[tip_idx])
        # print(f"F{i} tip: {dist_tip:.4f}, mcp*1.15: {dist_mcp*1.15:.4f}, sz*1.4: {sz*1.4:.4f}")
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
    parser.add_argument('--undo', action='store_true', help='Enable Undo (Head Tap) Tracking')
    parser.add_argument('--stream', action='store_true', help='Stream base64 frames to stdout')
    parser.add_argument('--workspace', type=str, default='', help='Target workspace path')
    parser.add_argument('--snap_threshold', type=float, default=0.05, help='Snap detection threshold')
    args = parser.parse_args()

    global DEBUG_WINDOW, hand_landmarker, pose_landmarker, face_landmarker, current_state
    DEBUG_WINDOW = args.debug == 'true'

    # Lazy Initialization based on flags
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
        face_options = vision.FaceLandmarkerOptions(
            base_options=base_face,
            output_face_blendshapes=True,
            num_faces=1
        )
        face_landmarker = vision.FaceLandmarker.create_from_options(face_options)

    with open('camera_debug_log.txt', 'w') as log_file:
        log_file.write("Starting camera initialization...\n")
        cap = None
        for cam_idx in range(5):
            log_file.write(f"Testing index {cam_idx} with DSHOW...\n")
            test_cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
            if test_cap.isOpened():
                log_file.write(f"  Opened {cam_idx} successfully.\n")
                valid = False
                for j in range(20): # Increased from 10 to 20 for safety
                    success, img = test_cap.read()
                    if success and img is not None:
                        max_val = img.max()
                        log_file.write(f"    Frame {j}: max_val={max_val}\n")
                        if max_val > 15:
                            valid = True
                            log_file.write(f"  -> Valid camera found at exactly index {cam_idx}\n")
                            break
                    time.sleep(0.1)
                
                if valid:
                    cap = test_cap
                    log_file.write(f"Successfully selected {cam_idx}\n")
                    break
                else:
                    log_file.write(f"  Camera {cam_idx} frames were black.\n")
                test_cap.release()
            else:
                log_file.write(f"  Failed to open {cam_idx}\n")
                
        if cap is None:
            log_file.write("DSHOW failed. Falling back to default ANY...\n")
            test_cap = cv2.VideoCapture(0)
            if test_cap.isOpened():
                cap = test_cap
                log_file.write("Default ANY opened successfully.\n")
    
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
        threading.Thread(target=read_stdin, daemon=True).start()
        print(json.dumps({"status": "ready"}), flush=True)

    # Copy/Paste specific state
    fist_frames = 0
    open_frames = 0
    REQUIRED_FRAMES = 3
    was_fist_previously = False
    action_cooldown = 1.5
    last_cp_action_time = 0

    # Push specific state
    PUSH_STATE_MONITORING = "MONITORING"
    PUSH_STATE_AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    push_state = PUSH_STATE_MONITORING
    
    neutral_dist = None
    last_push_time = 0
    push_start_time = time.time()
    confirmation_start_time = 0
    CONFIRM_TIMEOUT = 10.0 # 10 seconds to confirm
    PUSH_THRESHOLD = 0.85  # Current head size < 85% of neutral = Push detected
    PUSH_COOLDOWN = 5.0
    WARMUP_TIME = 2.0

    # Undo specific state
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
            if consecutive_failures > 30:
                print(json.dumps({"error": "Camera stream lost"}), flush=True)
                break
            continue
        consecutive_failures = 0
        
        # Check if the camera is returning pure black frames (privacy shutter closed or virtual cam active)
        is_blank = image.max() < 15
        if is_blank:
            # Create a blank gray canvas so text is highly visible
            image[:, :] = (50, 50, 50)
            cv2.putText(image, "CAMERA FEED BLACK", (50, 200), cv2.FONT_HERSHEY_DUPLEX, 1.0, (0, 0, 255), 2)
            cv2.putText(image, "Check privacy shutter or other apps", (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
            if args.extension:
                print(json.dumps({"error": "Camera feed is black. Check privacy shutter or OBS Virtual Camera."}), flush=True)

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
                if current_state != STATE_AWAITING_COPY and current_time - last_macro_time > MACRO_COOLDOWN:
                    fingers = get_finger_states(hl)
                    # states: [thumb, index, middle, ring, pinky]
                    
                    macro = None
                    if fingers == [False, True, False, False, False]:
                        macro = "gesture_one"
                    elif fingers[1:] == [True, True, False, False]:
                        macro = "gesture_peace"
                    elif fingers[1:] == [True, False, False, True]:
                        macro = "gesture_rock"
                    elif fingers == [True, True, False, False, False]:
                        macro = "gesture_l"
                        
                    if macro:
                        last_macro_time = current_time
                        trigger_action(macro, use_extension=args.extension)
                        hand_status = f"MACRO: {macro}"
                        hand_box_color = (255, 255, 0) # Gold

                # --- Copy/Paste Detection ---
                if args.copy_paste:
                    if current_state == STATE_AWAITING_COPY:
                        hand_status = "FIST = COPY"
                        hand_box_color = (0, 165, 255)
                    elif current_state == STATE_AWAITING_PASTE:
                        hand_status = "OPEN = PASTE"
                        hand_box_color = (255, 0, 255)

                    current_is_fist = is_fist(hl)
                    current_is_open = is_open(hl)
                    
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
                    
                    if current_time - last_cp_action_time > action_cooldown:
                        if current_state == STATE_AWAITING_COPY:
                            if is_stable_fist:
                                print(json.dumps({"action": "copy"}), flush=True)
                                current_state = STATE_AWAITING_PASTE
                                last_cp_action_time = current_time
                                hand_status = "COPIED!"
                                hand_box_color = (0, 255, 0)
                                was_fist_previously = False
                        
                        elif current_state == STATE_AWAITING_PASTE:
                            if is_stable_fist: was_fist_previously = True
                            if was_fist_previously and is_stable_open:
                                print(json.dumps({"action": "paste"}), flush=True)
                                current_state = STATE_IDLE
                                last_cp_action_time = current_time
                                hand_status = "PASTED!"
                                hand_box_color = (0, 255, 0)
                                was_fist_previously = False

                # --- Undo Detection ---
                if args.undo:
                    fingers = get_finger_states(hl)
                    # Check distance between thumb tip (4) and index tip (8)
                    dist_thumb_index = ((hl[4].x - hl[8].x)**2 + (hl[4].y - hl[8].y)**2)**0.5
                    hand_size = ((hl[0].x - hl[9].x)**2 + (hl[0].y - hl[9].y)**2)**0.5
                    
                    # OK Gesture: Thumb/index touching, middle/ring/pinky extended
                    # get_finger_states returns True for extended
                    if dist_thumb_index < hand_size * 0.4 and fingers[2:] == [True, True, True]:
                        if undo_state == UNDO_STATE_IDLE:
                            undo_state = UNDO_STATE_TOUCH
                            undo_touch_start = current_time
                        elif current_time - undo_touch_start > 0.4: # Hold for a short duration
                            if current_time - last_undo_time > 1.5:
                                if args.extension:
                                    print(json.dumps({"action": "undo"}), flush=True)
                                last_undo_time = current_time
                                hand_status = "UNDO: OK GESTURE"
                                hand_box_color = (0, 255, 0)
                                undo_state = UNDO_STATE_IDLE # Reset state after firing
                    else:
                        undo_state = UNDO_STATE_IDLE

                if current_gesture:
                    trigger_action(current_gesture, use_extension=args.extension)
            else:
                hand_presence_start = None
                smoothed_x = smoothed_y = neutral_y = None
                can_trigger = True

        # 2. PROCESS POSE AND PUSH
        if (args.posture or args.push) and pose_landmarker and current_state != STATE_AWAITING_COPY:
            pose_results = pose_landmarker.detect(mp_image)
            if pose_results.pose_landmarks:
                try:
                    for pl in pose_results.pose_landmarks:
                        if len(pl) < 21:
                            continue # Skip if we don't have enough landmarks for fingers
                        # Common landmarks
                        ey = (pl[2].y + pl[5].y) / 2
                        sy = (pl[11].y + pl[12].y) / 2
                        nd = abs(sy - ey)
                        
                        eye_dist = ((pl[2].x - pl[5].x)**2 + (pl[2].y - pl[5].y)**2)**0.5
                        nose_y = pl[0].y
                        lw_y = pl[15].y
                        rw_y = pl[16].y

                        if args.posture:
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

                        if args.push:
                            # Warmup phase
                            if time.time() - push_start_time < WARMUP_TIME:
                                if neutral_dist is None: neutral_dist = eye_dist
                                else: neutral_dist = 0.1 * eye_dist + 0.9 * neutral_dist
                            else:
                                ratio = eye_dist / neutral_dist if neutral_dist else 1.0
                                if push_state == PUSH_STATE_MONITORING:
                                    if ratio < PUSH_THRESHOLD and (time.time() - last_push_time > PUSH_COOLDOWN):
                                        push_state = PUSH_STATE_AWAITING_CONFIRMATION
                                        confirmation_start_time = time.time()
                                        if args.extension:
                                            print(json.dumps({"status": "awaiting_confirmation"}), flush=True)
                                
                                elif push_state == PUSH_STATE_AWAITING_CONFIRMATION:
                                    # For Y axis, smaller is higher up. Hands higher than shoulders = <
                                    hands_up = lw_y < sy and rw_y < sy
                                    elapsed = time.time() - confirmation_start_time
                                    
                                    if hands_up:
                                        if args.extension:
                                            print(json.dumps({"status": "pushing_in_progress"}), flush=True)
                                        perform_git_push_async(args.workspace, SCRIPT_DIR, ratio, args.extension)
                                        last_push_time = time.time()
                                        push_state = PUSH_STATE_MONITORING
                                    elif elapsed > CONFIRM_TIMEOUT:
                                        if args.extension:
                                            print(json.dumps({"status": "push_aborted"}), flush=True)
                                        push_state = PUSH_STATE_MONITORING

                except Exception as e:
                    print(json.dumps({"error": f"Pose Engine Crash: {str(e)}"}), flush=True)

        # 3. PROCESS FACE (Wink)
        face_status = "Analyzing Face..."
        if args.face and face_landmarker and current_state != STATE_AWAITING_COPY:
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
            
            if args.undo:
                status_text = "UNDO READY" if time.time() - last_undo_time > 1.5 else "UNDO COOLDOWN"
                if undo_state == UNDO_STATE_TOUCH: status_text = "TOUCHING"
                cv2.putText(image, f"UNDO: {status_text}", (50, 460), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 165, 0), 2)

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
