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

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# We'll use the Pose Landmarker to track the head/shoulders as a proxy for face distance
MODEL_PATH = os.path.join(SCRIPT_DIR, 'pose_landmarker.task')
DEBUG_WINDOW = True 

# Push Detection Settings
PUSH_THRESHOLD = 0.85  # Current head size < 85% of neutral = Push detected
COOLDOWN = 5.0        # Prevent multiple pushes in 5 seconds
WARMUP_TIME = 2.0      # Time to calibrate neutral distance

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

def perform_git_push():
    """
    Executes the git sequence to push current changes to GitHub.
    """
    print("\n--- DETECTED PUSH: TRIGGERING GIT PUSH ---")
    try:
        # 1. Add all changes
        subprocess.run(["git", "add", "."], check=True)
        # 2. Commit with a timestamp
        commit_msg = f"Auto-push from Kineticode Push Engine: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        # 3. Push to original branch
        subprocess.run(["git", "push"], check=True)
        print("--- GIT PUSH SUCCESSFUL ---\n")
    except subprocess.CalledProcessError as e:
        print(f"--- GIT PUSH FAILED: {e} ---")
    except Exception as e:
        print(f"--- ERROR: {e} ---")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--extension', action='store_true', help='Extension mode (JSON output)')
    parser.add_argument('--debug', type=str, choices=['true', 'false'], default='true', help='Show debug window')
    args = parser.parse_args()

    global DEBUG_WINDOW
    DEBUG_WINDOW = args.debug == 'true'

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    
    neutral_dist = None
    last_push_time = 0
    start_time = time.time()
    
    # State for detection
    push_in_progress = False

    if args.extension:
        print(json.dumps({"status": "push_engine_ready"}), flush=True)
    else:
        print("--- Push-to-GitHub Engine Active ---")
        print("1. Sit naturally for 2 seconds to calibrate.")
        print("2. Push your laptop/computer away to trigger a Git Push.")
        print("Press 'Q' or 'ESC' to quit.")

    while cap.isOpened():
        success, image = cap.read()
        if not success: continue

        image = cv2.flip(image, 1)
        h, w, _ = image.shape
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
        
        results = landmarker.detect(mp_image)
        status_text = "Calibrating..."
        box_color = (255, 100, 0) # Orange

        if results.pose_landmarks:
            for pose_landmarks in results.pose_landmarks:
                # Keypoints: 2(LE), 5(RE) 11(LS), 12(RS)
                # Distance between eyes is a good proxy for face distance
                eye_dist = ((pose_landmarks[2].x - pose_landmarks[5].x)**2 + 
                            (pose_landmarks[2].y - pose_landmarks[5].y)**2)**0.5
                
                # Calibration phase
                if time.time() - start_time < WARMUP_TIME:
                    if neutral_dist is None:
                        neutral_dist = eye_dist
                    else:
                        # EMA for stable calibration
                        neutral_dist = 0.1 * eye_dist + 0.9 * neutral_dist
                    status_text = f"Calibrating: {int((time.time()-start_time)/WARMUP_TIME*100)}%"
                else:
                    # Detection phase
                    ratio = eye_dist / neutral_dist if neutral_dist else 1.0
                    
                    if ratio < PUSH_THRESHOLD:
                        status_text = "PUSH DETECTED!"
                        box_color = (0, 255, 0) # Green
                        
                        if time.time() - last_push_time > COOLDOWN:
                            perform_git_push()
                            last_push_time = time.time()
                            if args.extension:
                                print(json.dumps({"action": "git_push", "ratio": ratio}), flush=True)
                    else:
                        status_text = "Monitoring..."
                        box_color = (255, 0, 0) # Blue

                # Visuals
                if DEBUG_WINDOW:
                    # Draw eye line
                    p1 = (int(pose_landmarks[2].x*w), int(pose_landmarks[2].y*h))
                    p2 = (int(pose_landmarks[5].x*w), int(pose_landmarks[5].y*h))
                    cv2.line(image, p1, p2, box_color, 2)
                    cv2.putText(image, status_text, (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, box_color, 2)
                    cv2.putText(image, f"Dist Ratio: {ratio:.2f}" if 'ratio' in locals() else "Calibrating...", (50, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.6, box_color, 2)

        if DEBUG_WINDOW:
            cv2.imshow('Push Engine Preview', image)
            if cv2.waitKey(1) & 0xFF == ord('q') or cv2.waitKey(1) & 0xFF == 27: break

    cap.release()
    cv2.destroyAllWindows()
    landmarker.close()

if __name__ == "__main__":
    main()
