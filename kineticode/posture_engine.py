import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import time
import json
import sys
import os
import argparse

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(SCRIPT_DIR, 'pose_landmarker.task')
DEBUG_WINDOW = True 

# Thresholds
SLOUCH_THRESHOLD = 0.85 # eye_to_shoulder < 85% of neutral = Slouch
WARMUP_DELAY = 1.0     # 1 second to calibrate height

if not os.path.exists(MODEL_PATH):
    print(f"Error: Model file {MODEL_PATH} not found.")
    sys.exit(1)

base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
options = vision.PoseLandmarkerOptions(
    base_options=base_options,
    num_poses=1,
    min_pose_detection_confidence=0.5,
    min_pose_presence_confidence=0.5,
    min_tracking_confidence=0.5
)
landmarker = vision.PoseLandmarker.create_from_options(options)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--extension', action='store_true')
    parser.add_argument('--debug', type=str, choices=['true', 'false'], default='true', help='Show debug window')
    parser.add_argument('--snap_threshold', type=float, default=0.05, help='Snap detection threshold')
    parser.add_argument('--workspace', type=str, default='', help='Target workspace path')
    args = parser.parse_args()

    global DEBUG_WINDOW
    DEBUG_WINDOW = args.debug == 'true'

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    
    neutral_neck_dist = None
    neutral_shoulder_y = None
    start_time = time.time()
    current_state = "upright"

    while cap.isOpened():
        success, image = cap.read()
        if not success: continue

        image = cv2.flip(image, 1)
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
        
        results = landmarker.detect(mp_image)
        status_text = "Analyzing..."
        box_color = (255, 0, 0)

        if results.pose_landmarks:
            for pose_landmarks in results.pose_landmarks:
                # Keypoints: 0(Nose), 11(LS), 12(RS), 2(LE), 5(RE)
                eye_y = (pose_landmarks[2].y + pose_landmarks[5].y) / 2 
                shoulder_y = (pose_landmarks[11].y + pose_landmarks[12].y) / 2
                neck_dist = abs(shoulder_y - eye_y)

                if neutral_neck_dist is None:
                    neutral_neck_dist = neck_dist
                    neutral_shoulder_y = shoulder_y
                
                # Metrics
                # 1. Neck Scrunch (Vertical compression of neck)
                neck_ratio = neck_dist / neutral_neck_dist if neutral_neck_dist else 1.0
                # 2. Shoulder Drop (Absolute sinking in frame)
                shoulder_drop = shoulder_y - neutral_shoulder_y if neutral_shoulder_y else 0
                
                is_slouching = (neck_ratio < 0.85) or (shoulder_drop > 0.05)
                
                if is_slouching:
                    new_state = "slouch"
                    box_color = (0, 0, 255) # Red
                    status_text = "🚨 SLOUCHING! (Sit Up)"
                else:
                    new_state = "upright"
                    box_color = (0, 255, 0) # Green
                    status_text = "✅ GOOD POSTURE"

                    if new_state != current_state:
                        current_state = new_state
                        if args.extension:
                            print(json.dumps({"posture": current_state}), flush=True)

                # Visuals
                if DEBUG_WINDOW:
                    h, w, _ = image.shape
                    # Shoulder line
                    cv2.line(image, (int(pose_landmarks[11].x*w), int(pose_landmarks[11].y*h)), (int(pose_landmarks[12].x*w), int(pose_landmarks[12].y*h)), box_color, 2)
                    # Nose
                    cv2.circle(image, (int(pose_landmarks[0].x*w), int(pose_landmarks[0].y*h)), 5, box_color, -1)
                    cv2.putText(image, status_text, (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, box_color, 2)

        if DEBUG_WINDOW:
            cv2.imshow('Air Posture Preview', image)
            if cv2.waitKey(1) & 0xFF == ord('q'): break

    cap.release()
    cv2.destroyAllWindows()
    landmarker.close()

if __name__ == "__main__":
    main()
