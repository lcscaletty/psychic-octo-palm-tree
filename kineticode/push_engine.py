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

print("--- PUSH ENGINE STARTING ---", flush=True)
print(f"Python Version: {sys.version}", flush=True)
print(f"CWD: {os.getcwd()}", flush=True)
print(f"Script Dir: {os.path.dirname(os.path.abspath(__file__))}", flush=True)

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

try:
    print(f"Loading Model: {MODEL_PATH}", flush=True)
    base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
    options = vision.PoseLandmarkerOptions(
        base_options=base_options,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5
    )
    landmarker = vision.PoseLandmarker.create_from_options(options)
    print("Model Loaded Successfully", flush=True)
except Exception as e:
    print(f"CRITICAL ERROR during initialization: {e}", flush=True)
    if "--extension" in sys.argv:
        print(json.dumps({"error": f"init_fail: {str(e)}"}), flush=True)
    sys.exit(1)

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
    parser.add_argument('--snap_threshold', type=float, default=0.05, help='Snap detection threshold')
    args = parser.parse_args()

    global DEBUG_WINDOW
    DEBUG_WINDOW = args.debug == 'true'

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("Warning: CAP_DSHOW failed, trying default...")
        cap = cv2.VideoCapture(0)
    
    if not cap.isOpened():
        print("Error: Could not open webcam.")
        if args.extension:
            print(json.dumps({"error": "webcam_fail"}), flush=True)
        return

    neutral_dist = None
    last_push_time = 0
    start_time = time.time()
    
    # State for detection
    STATE_MONITORING = "MONITORING"
    STATE_AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    
    current_state = STATE_MONITORING
    confirmation_start_time = 0
    CONFIRM_TIMEOUT = 10.0 # 10 seconds to confirm

    if args.extension:
        print(json.dumps({"status": "push_engine_ready"}), flush=True)
    else:
        print("--- Push-to-GitHub Engine Active ---")
        print("1. Sit naturally for 2 seconds to calibrate.")
        print("2. Push your laptop away to enter confirmation mode.")
        print("3. Raise BOTH HANDS above your head to confirm the push.")
        print("Press 'Q' or 'ESC' to quit.")

    print("Main loop starting...", flush=True)

    while cap.isOpened():
        success, image = cap.read()
        if not success:
            print("Failed to read frame", flush=True)
            continue

        image = cv2.flip(image, 1)
        h, w, _ = image.shape
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
        
        results = landmarker.detect(mp_image)
        status_text = "Calibrating..."
        box_color = (255, 100, 0) # Orange

        if results.pose_landmarks:
            for pose_landmarks in results.pose_landmarks:
                # Keypoints: 0(Nose), 2(LE), 5(RE), 15(LW), 16(RW)
                nose_y = pose_landmarks[0].y
                lw_y = pose_landmarks[15].y
                rw_y = pose_landmarks[16].y
                
                # Distance between eyes is a good proxy for face distance
                eye_dist = ((pose_landmarks[2].x - pose_landmarks[5].x)**2 + 
                            (pose_landmarks[2].y - pose_landmarks[5].y)**2)**0.5
                
                # Calibration phase
                if time.time() - start_time < WARMUP_TIME:
                    if neutral_dist is None:
                        neutral_dist = eye_dist
                    else:
                        neutral_dist = 0.1 * eye_dist + 0.9 * neutral_dist
                    status_text = f"Calibrating: {int((time.time()-start_time)/WARMUP_TIME*100)}%"
                else:
                    # Detection phase
                    ratio = eye_dist / neutral_dist if neutral_dist else 1.0
                    
                    if current_state == STATE_MONITORING:
                        if ratio < PUSH_THRESHOLD and (time.time() - last_push_time > COOLDOWN):
                            current_state = STATE_AWAITING_CONFIRMATION
                            confirmation_start_time = time.time()
                            print("Push detected! Awaiting hands-up confirmation...", flush=True)
                        else:
                            status_text = "Monitoring..."
                            box_color = (255, 0, 0) # Blue
                    
                    elif current_state == STATE_AWAITING_CONFIRMATION:
                        # Check for hands up (both wrists above nose)
                        # Note: y grows downwards, so "above" means y is smaller
                        hands_up = lw_y < nose_y and rw_y < nose_y
                        
                        elapsed = time.time() - confirmation_start_time
                        if hands_up:
                            status_text = "CONFIRMED! PUSHING..."
                            box_color = (0, 255, 0) # Green
                            perform_git_push()
                            last_push_time = time.time()
                            current_state = STATE_MONITORING
                            if args.extension:
                                print(json.dumps({"action": "git_push", "ratio": ratio}), flush=True)
                        elif elapsed > CONFIRM_TIMEOUT:
                            print("Confirmation timed out.", flush=True)
                            current_state = STATE_MONITORING
                        else:
                            status_text = f"RAISE HANDS! ({int(CONFIRM_TIMEOUT - elapsed)}s)"
                            box_color = (0, 165, 255) # Bright Orange

                # Visuals
                if DEBUG_WINDOW:
                    # Draw indicators
                    p_nose = (int(pose_landmarks[0].x*w), int(pose_landmarks[0].y*h))
                    p_lw = (int(pose_landmarks[15].x*w), int(pose_landmarks[15].y*h))
                    p_rw = (int(pose_landmarks[16].x*w), int(pose_landmarks[16].y*h))
                    
                    cv2.circle(image, p_nose, 5, (255, 255, 255), -1)
                    cv2.circle(image, p_lw, 8, box_color, -1)
                    cv2.circle(image, p_rw, 8, box_color, -1)
                    
                    cv2.putText(image, status_text, (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, box_color, 2)
                    cv2.putText(image, f"Dist Ratio: {ratio:.2f}" if 'ratio' in locals() else "Calibrating...", (50, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.6, box_color, 2)
                    if current_state == STATE_AWAITING_CONFIRMATION:
                         cv2.rectangle(image, (0,0), (w,h), box_color, 10) # Flash border during confirmation phase

        if DEBUG_WINDOW:
            cv2.imshow('Push Engine Preview', image)
            if cv2.waitKey(1) & 0xFF == ord('q') or cv2.waitKey(1) & 0xFF == 27: break

    cap.release()
    cv2.destroyAllWindows()
    landmarker.close()

if __name__ == "__main__":
    main()
