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
COOLDOWN = 0.6  # Seconds between events
SWIPE_THRESHOLD = 0.12
DEBUG_WINDOW = True # Show the camera feed

# --- PyAutoGUI Safety Settings ---
pyautogui.PAUSE = 0.1
pyautogui.FAILSAFE = True # Move mouse to corner to abort

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

def get_gesture(hand_landmarks):
    """
    Gesture detection based on finger states.
    4(thumb_tip), 8(index_tip), 12(middle_tip), 16(ring_tip), 20(pinky_tip)
    """
    fingers = []
    
    # Simple 'is it up' check (tip vs mcp)
    # Thumb
    if hand_landmarks[4].y < hand_landmarks[3].y:
        fingers.append(1)
    else:
        fingers.append(0)
        
    # Other 4 fingers
    for tip, mcp in [(8, 5), (12, 9), (16, 13), (20, 17)]:
        if hand_landmarks[tip].y < hand_landmarks[mcp].y:
            fingers.append(1)
        else:
            fingers.append(0)
            
    if sum(fingers) == 0:
        return "fist"
    return None

def trigger_action(gesture, use_extension=False):
    """
    Performs system actions based on gestures.
    """
    if use_extension:
        # Output JSON for the VS Code Extension
        print(json.dumps({"gesture": gesture}), flush=True)
    else:
        # Standalone mode: UI Automation
        print(f"Action: {gesture}")
        if gesture == "swipe_left":
            pyautogui.hotkey('ctrl', 'pageup') 
        elif gesture == "swipe_right":
            pyautogui.hotkey('ctrl', 'pagedown')
        elif gesture == "fist":
            pyautogui.hotkey('ctrl', 's')

def main():
    parser = argparse.ArgumentParser(description='Air Gesture Engine')
    parser.add_argument('--extension', action='store_true', help='Run in extension mode (JSON output)')
    args = parser.parse_args()

    # Use cv2.CAP_DSHOW for faster initialization on Windows
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    
    if not cap.isOpened():
        print("Error: Could not open webcam.")
        return

    prev_gesture = None
    last_event_time = 0
    swipe_start_x = None
    SWIPE_DISTANCE_THRESHOLD = 0.15 # Reduced for better sensitivity
    
    if args.extension:
        print(json.dumps({"status": "ready"}), flush=True)
    else:
        print("--- Air Gesture Control: Standalone Mode ---")
        print("Commands:")
        print(" - Swipe Left (Inwards): Next Tab")
        print(" - Swipe Right (Outwards): Previous Tab")
        print(" - Fist: Save File")
        print("Press 'ESC' in the window or 'Q' in terminal to quit.")

    # Create window before starting loop
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
            for hand_landmarks in results.hand_landmarks:
                current_gesture = get_gesture(hand_landmarks)
                
                # Bounding Box Calculation
                x_coords = [lm.x for lm in hand_landmarks]
                y_coords = [lm.y for lm in hand_landmarks]
                min_x, max_x = min(x_coords), max(x_coords)
                min_y, max_y = min(y_coords), max(y_coords)
                
                # Robust Swipe detection
                index_x = hand_landmarks[8].x # Index finger tip X (0 to 1)
                
                if time.time() - last_event_time < COOLDOWN:
                    swipe_start_x = None
                    box_color = (0, 255, 0) # Green (Just triggered/Cooldown)
                else:
                    if swipe_start_x is None:
                        swipe_start_x = index_x
                    else:
                        diff = index_x - swipe_start_x
                        # Change color if moving significant distance
                        if abs(diff) > 0.05:
                            box_color = (0, 255, 255) # Yellow (Moving)
                            
                        if diff > SWIPE_DISTANCE_THRESHOLD:
                            current_gesture = "swipe_right"
                            swipe_start_x = None
                        elif diff < -SWIPE_DISTANCE_THRESHOLD:
                            current_gesture = "swipe_left"
                            swipe_start_x = None
                
                # Draw visual feedback
                if DEBUG_WINDOW:
                    # Draw Bounding Box
                    cv2.rectangle(image, 
                                  (int(min_x * w), int(min_y * h)), 
                                  (int(max_x * w), int(max_y * h)), 
                                  box_color, 2)
                    
                    if current_gesture:
                        status_text = f"Gesture: {current_gesture.upper()}"
                        cv2.putText(image, status_text, (50, 50), 
                                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

        # Trigger Action
        if current_gesture and current_gesture != prev_gesture:
            if time.time() - last_event_time > COOLDOWN:
                trigger_action(current_gesture, use_extension=args.extension)
                last_event_time = time.time()
                prev_gesture = current_gesture
        
        if current_gesture is None:
            prev_gesture = None

        if DEBUG_WINDOW:
            cv2.imshow('Air Gesture Preview', image)
            
        # Check for keyboard input in the window
        key = cv2.waitKey(1) & 0xFF
        if key == 27 or key == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    landmarker.close()

if __name__ == "__main__":
    main()
