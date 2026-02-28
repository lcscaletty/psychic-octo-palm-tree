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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--extension', action='store_true')
    parser.add_argument('--debug', type=str, choices=['true', 'false'], default='true')
    parser.add_argument('--hands', action='store_true', help='Enable Hand Tracking')
    parser.add_argument('--posture', action='store_true', help='Enable Posture Tracking')
    parser.add_argument('--face', action='store_true', help='Enable Face/Wink Tracking')
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
                elif smoothed_x < LEFT_ZONE:
                    gesture = "swipe_left"
                    if can_trigger:
                        can_trigger = False
                        last_event_time = time.time()
                        current_gesture = gesture
                    elif time.time() - last_event_time > AUTO_REPEAT_DELAY:
                        last_event_time = time.time()
                        current_gesture = gesture
                        hand_status = "Scrolling..."
                    hand_box_color = (0, 255, 0)
                elif smoothed_x > RIGHT_ZONE:
                    gesture = "swipe_right"
                    if can_trigger:
                        can_trigger = False
                        last_event_time = time.time()
                        current_gesture = gesture
                    elif time.time() - last_event_time > AUTO_REPEAT_DELAY:
                        last_event_time = time.time()
                        current_gesture = gesture
                        hand_status = "Scrolling..."
                    hand_box_color = (0, 255, 0)
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

            cv2.imshow('Kineticode Control Hub', image)
            if cv2.waitKey(1) & 0xFF == ord('q'): break

    cap.release()
    cv2.destroyAllWindows()
    if hand_landmarker: hand_landmarker.close()
    if pose_landmarker: pose_landmarker.close()
    if face_landmarker: face_landmarker.close()

if __name__ == "__main__":
    main()
