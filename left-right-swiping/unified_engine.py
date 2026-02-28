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
DEBUG_WINDOW = True 

# Zone Settings
VERTICAL_LIMIT = 0.12
NEUTRAL_ZONE = (0.35, 0.65)
LEFT_ZONE = 0.3
RIGHT_ZONE = 0.7
AUTO_REPEAT_DELAY = 0.4
EMA_ALPHA = 0.3

# Posture Settings
SLOUCH_THRESHOLD = 0.85
DROP_THRESHOLD = 0.05

# --- Initialization ---
base_hand = python.BaseOptions(model_asset_path=HAND_MODEL)
hand_options = vision.HandLandmarkerOptions(base_options=base_hand, num_hands=1)
hand_landmarker = vision.HandLandmarker.create_from_options(hand_options)

base_pose = python.BaseOptions(model_asset_path=POSE_MODEL)
pose_options = vision.PoseLandmarkerOptions(base_options=base_pose, num_poses=1)
pose_landmarker = vision.PoseLandmarker.create_from_options(pose_options)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--extension', action='store_true')
    args = parser.parse_args()

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    
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

    if args.extension:
        print(json.dumps({"status": "ready"}), flush=True)

    while cap.isOpened():
        success, image = cap.read()
        if not success: continue

        image = cv2.flip(image, 1)
        h, w, _ = image.shape
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
        
        # 1. PROCESS HANDS
        hand_results = hand_landmarker.detect(mp_image)
        current_gesture = None
        hand_status = "No Hand"
        hand_box_color = (128, 128, 128)

        if hand_results.hand_landmarks:
            if hand_presence_start is None: hand_presence_start = time.time()
            is_warming_up = (time.time() - hand_presence_start) < 0.2

            for hl in hand_results.hand_landmarks:
                # Smoothing
                px = (hl[0].x + hl[5].x + hl[17].x) / 3
                py = (hl[0].y + hl[5].y + hl[17].y) / 3
                if smoothed_x is None: smoothed_x, smoothed_y = px, py
                else:
                    smoothed_x = EMA_ALPHA * px + (1 - EMA_ALPHA) * smoothed_x
                    smoothed_y = EMA_ALPHA * py + (1 - EMA_ALPHA) * smoothed_y
                
                if is_warming_up:
                    hand_box_color = (255, 255, 255)
                    hand_status = "Hand Warming Up..."
                    can_trigger = True
                    neutral_y = smoothed_y
                else:
                    if neutral_y is not None and abs(smoothed_y - neutral_y) > VERTICAL_LIMIT:
                        hand_box_color = (0, 0, 255)
                        hand_status = "Vertical Block"
                    else:
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
                
                if current_gesture and args.extension:
                    print(json.dumps({"gesture": current_gesture}), flush=True)

        else:
            hand_presence_start = None
            smoothed_x = smoothed_y = neutral_y = None
            can_trigger = True

        # 2. PROCESS POSE
        pose_results = pose_landmarker.detect(mp_image)
        posture_status = "Analyzing Posture..."
        pose_color = (255, 0, 0)

        if pose_results.pose_landmarks:
            for pl in pose_results.pose_landmarks:
                ey = (pl[2].y + pl[5].y) / 2
                sy = (pl[11].y + pl[12].y) / 2
                nd = abs(sy - ey)

                if time.time() - posture_start_time < 1.5:
                    neutral_neck_dist = nd
                    neutral_shoulder_y = sy
                    posture_status = "Calibrating Posture..."
                    pose_color = (255, 255, 255)
                else:
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

        # 3. VISUALS
        if DEBUG_WINDOW:
            # Draw Hand Box (Top Left Info)
            cv2.putText(image, f"HAND: {hand_status}", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, hand_box_color, 2)
            # Draw Posture (Bottom Left Info)
            cv2.putText(image, f"POSE: {posture_status}", (50, 430), cv2.FONT_HERSHEY_SIMPLEX, 0.7, pose_color, 2)
            
            # Simple skeleton dots
            if pose_results.pose_landmarks:
                for pl in pose_results.pose_landmarks:
                    cv2.circle(image, (int(pl[0].x*w), int(pl[0].y*h)), 5, pose_color, -1)
                    cv2.line(image, (int(pl[11].x*w), int(pl[11].y*h)), (int(pl[12].x*w), int(pl[12].y*h)), pose_color, 2)

            cv2.imshow('Unified Air Control', image)
            if cv2.waitKey(1) & 0xFF == ord('q'): break

    cap.release()
    cv2.destroyAllWindows()
    hand_landmarker.close()
    pose_landmarker.close()

if __name__ == "__main__":
    main()
