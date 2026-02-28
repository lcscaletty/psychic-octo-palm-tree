# Air Gesture Control for VS Code

Control VS Code with hand gestures and posture monitoring. Powered by MediaPipe and Python.

## Features

- **Zone-based Hand Control**: Navigate between tabs (`swipe left` / `swipe right`).
- **Posture Monitoring**: Automatically shrink editor font when slouching and restore when sitting upright.
- **Dual Mode**: Combine hand gestures and posture monitoring for a touch-free experience.
- **Snap to Create**: Snap your fingers (pinch thumb and middle finger and release) to create a new untitled file.
- **Standalone Mode**: `gesture_engine.py` can be run independently for system-wide tab navigation.

## Requirements

The extension requires **Python 3.x** and the following packages:
- `opencv-python`
- `mediapipe`
- `pyautogui`
- `numpy`

The extension will attempt to install these automatically if they are missing.

## Usage

1. Click the **Air Control** status bar item or run **Air Gesture: Open Control Menu** from the command palette.
2. Select your desired mode (Hand, Posture, or Dual).
3. The tracking window will open (if enabled in settings).
4. Perform gestures or monitor your posture!

## Extension Settings

This extension contributes the following settings:

* `airGesture.debugWindow`: Show/hide the camera preview and tracking visualizers.
* `airGesture.snapThreshold`: Adjust the sensitivity for the snap-to-create-file gesture.

## License

MIT
