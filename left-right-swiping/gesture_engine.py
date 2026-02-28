import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import time
import json
import sys
import os
import pyautogui
import argparse

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(SCRIPT_DIR, 'hand_landmarker.task')
COOLDOWN = 0.35  
AUTO_REPEAT_DELAY = 0.4 
DEBUG_WINDOW = True 

# --- PyAutoGUI Safety Settings ---
pyautogui.PAUSE = 0.1
pyautogui.FAILSAFE = True 

# --- MediaPipe Task Initialization ---
if not os.path.exists(MODEL_PATH):
    print(f"Error: Model file {MODEL_PATH} not found. Please ensure it is in the same directory.")
    sys.exit(1)

base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
options = vision.HandLandmarkerOptions(
    base_options=base_options,
    num_hands=1,
    min_hand_detection_confidence=0.7,
    min_hand_presence_confidence=0.5,
    min_tracking_confidence=0.5
)
landmarker = vision.HandLandmarker.create_from_options(options)

def trigger_action(gesture, use_extension=False):
    """
    Performs system actions based on gestures/zones.
    """
    if use_extension:
        # Output JSON for the VS Code Extension
        print(json.dumps({"gesture": gesture}), flush=True)
    else:
        # Standalone mode: UI Automation
        if gesture == "swipe_left":
            print("Action: Previous Tab (Left Side)")
            pyautogui.hotkey('ctrl', 'pageup') 
        elif gesture == "swipe_right":
            print("Action: Next Tab (Right Side)")
            pyautogui.hotkey('ctrl', 'pagedown')

def main():
    parser = argparse.ArgumentParser(description='Air Gesture Engine')
    parser.add_argument('--extension', action='store_true', help='Run in extension mode (JSON output)')
    args = parser.parse_args()

    # Use cv2.CAP_DSHOW for faster initialization on Windows
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    
    if not cap.isOpened():
        print("Error: Could not open webcam.")
        return

    # --- Zone State ---
    can_trigger = True
    last_event_time = 0
    neutral_y = None
    VERTICAL_LIMIT = 0.12 # Max deviation from entry height
    NEUTRAL_ZONE = (0.35, 0.65) # Reset here
    LEFT_ZONE = 0.3
    RIGHT_ZONE = 0.7
    
    smoothed_x = None
    smoothed_y = None
    EMA_ALPHA = 0.3 
    
    hand_presence_start = None
    hand_lost_frames = 0
    LOST_FRAME_LIMIT = 5 
    WARMUP_DELAY = 0.2
    
    if args.extension:
        print(json.dumps({"status": "ready"}), flush=True)
    else:
        print("--- Air Gesture Control: Zone Mode ---")
        print("Zones:")
        print(" [0.0 - 0.3] : Previous Tab (Left Side)")
        print(" [0.3 - 0.7] : Neutral (Reset)")
        print(" [0.7 - 1.0] : Next Tab (Right Side)")
        print("Press 'ESC' in the window or 'Q' in terminal to quit.")

    if DEBUG_WINDOW:
        cv2.namedWindow('Air Gesture Preview', cv2.WINDOW_AUTOSIZE)

    while cap.isOpened():
        success, image = cap.read()
        if not success:
            continue

        h, w, _ = image.shape
        image = cv2.flip(image, 1) # Mirror
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
        
        results = landmarker.detect(mp_image)
        current_gesture = None
        status_text = "Tracking..."
        box_color = (255, 0, 0) # Blue (Idle)

        if results.hand_landmarks:
            hand_lost_frames = 0
            if hand_presence_start is None:
                hand_presence_start = time.time()
            
            time_present = time.time() - hand_presence_start
            is_warming_up = time_present < WARMUP_DELAY

            for hand_landmarks in results.hand_landmarks:
                # Coordinate Smoothing (EMA)
                palm_center_x = (hand_landmarks[0].x + hand_landmarks[5].x + hand_landmarks[17].x) / 3
                palm_center_y = (hand_landmarks[0].y + hand_landmarks[5].y + hand_landmarks[17].y) / 3
                
                if smoothed_x is None:
                    smoothed_x, smoothed_y = palm_center_x, palm_center_y
                else:
                    smoothed_x = EMA_ALPHA * palm_center_x + (1 - EMA_ALPHA) * smoothed_x
                    smoothed_y = EMA_ALPHA * palm_center_y + (1 - EMA_ALPHA) * smoothed_y
                
                # Bounding Box for Visuals
                x_coords = [lm.x for lm in hand_landmarks]
                y_coords = [lm.y for lm in hand_landmarks]
                min_x, max_x = min(x_coords), max(x_coords)
                min_y, max_y = min(y_coords), max(y_coords)

                if is_warming_up:
                    box_color = (255, 255, 255) # White
                    status_text = "Warming up..."
                    can_trigger = True 
                    neutral_y = smoothed_y
                else:
                    # Vertical Limit Check
                    if neutral_y is not None and abs(smoothed_y - neutral_y) > VERTICAL_LIMIT:
                        box_color = (0, 0, 255) # Red
                        status_text = "Move too vertical"
                    else:
                        # Zone Logic
                        if NEUTRAL_ZONE[0] < smoothed_x < NEUTRAL_ZONE[1]:
                            can_trigger = True
                            neutral_y = smoothed_y 
                            status_text = "Neutral (Center)"
                            box_color = (255, 0, 0) # Blue
                        elif smoothed_x < LEFT_ZONE:
                            if can_trigger:
                                current_gesture = "swipe_left"
                                can_trigger = False
                                last_event_time = time.time()
                                box_color = (0, 255, 0) # Green
                                trigger_action(current_gesture, use_extension=args.extension)
                            elif time.time() - last_event_time > AUTO_REPEAT_DELAY:
                                current_gesture = "swipe_left"
                                last_event_time = time.time()
                                box_color = (0, 255, 0) # Green
                                status_text = "Scrolling Left..."
                                trigger_action(current_gesture, use_extension=args.extension)
                            else:
                                box_color = (0, 255, 255) # Yellow
                                status_text = "In Left Zone"
                        elif smoothed_x > RIGHT_ZONE:
                            if can_trigger:
                                current_gesture = "swipe_right"
                                can_trigger = False
                                last_event_time = time.time()
                                box_color = (0, 255, 0) # Green
                                trigger_action(current_gesture, use_extension=args.extension)
                            elif time.time() - last_event_time > AUTO_REPEAT_DELAY:
                                current_gesture = "swipe_right"
                                last_event_time = time.time()
                                box_color = (0, 255, 0) # Green
                                status_text = "Scrolling Right..."
                                trigger_action(current_gesture, use_extension=args.extension)
                            else:
                                box_color = (0, 255, 255) # Yellow
                                status_text = "In Right Zone"
                
                # Draw Visuals
                if DEBUG_WINDOW:
                    cv2.rectangle(image, (int(min_x*w), int(min_y*h)), (int(max_x*w), int(max_y*h)), box_color, 2)
                    cv2.putText(image, status_text, (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, box_color, 2)
                    if current_gesture:
                        cv2.putText(image, f"ACTION: {current_gesture.upper()}", (50, 90), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        else:
            hand_lost_frames += 1
            if hand_lost_frames > LOST_FRAME_LIMIT:
                hand_presence_start = None
                smoothed_x = None
                smoothed_y = None
                neutral_y = None
                can_trigger = True

        if DEBUG_WINDOW:
            cv2.imshow('Air Gesture Preview', image)
            
        key = cv2.waitKey(1) & 0xFF
        if key == 27 or key == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    landmarker.close()

if __name__ == "__main__":
    main()
