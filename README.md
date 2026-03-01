# Kineticode: AI-Powered Body Control for VS Code

Kineticode transforms your webcam into a powerful interface for VS Code, allowing you to control your editor using hand shapes, head movements, facial expressions, and posture. Built entirely locally with MediaPipe, your data never leaves your machine.

---

## 🚀 Features & Control Modes

Select exactly which features you want to enable via the Kineticode Status Bar menu `$(broadcast)`. You can mix and match to build your perfect workflow!

### 🤘 Macro Control (Hand Shapes)
Execute complex editor commands instantly using specific finger shapes. 
- **Index Finger (Point Up)** ☝️: Moves the current editor back and forth between split groups (`workbench.action.moveEditorToNextGroup`).
- **Peace Sign (V)** ✌️: Splits the current editor screen (`workbench.action.splitEditor`).
- **Rock On** 🤘: Toggles the integrated terminal (`workbench.action.terminal.toggleTerminal`).
- **L-Shape (Gun)** 🔫: Opens the Find widget to search within the file (`actions.find`).

### 🧠 Tilt Navigation (Head Movement)
Navigate your workspace hands-free.
- **Tilt Head Left**: Switches to the previous editor tab (`workbench.action.previousEditor`).
- **Tilt Head Right**: Switches to the next editor tab (`workbench.action.nextEditor`).

### 🧘 Posture Control
Ergonomic feedback built right into your code.
- Kineticode tracks your shoulder-to-eye distance.
- When you **slouch**, your editor font size automatically shrinks! 😱
- When you **sit up straight**, your font size returns to a comfortable, large size. 😎

### 😉 Face Control
Quick actions with a wink of an eye.
- **Wink**: Automatically creates a new, untitled text file (`workbench.action.files.newUntitledFile`).

### 🚀 Git Push Control
Trigger a `git push` with a physical action.
- Select from either a **Physical Push** (push the screen away) or a **Swipe**. 

---

## ⚙️ Configuration

Kineticode provides a non-intrusive **Camera Preview** directly in your VS Code Sidebar (Secondary/Right side recommended), complete with a debug overlay showing exactly what the AI sees!

Customize your experience via VS Code Settings (`Ctrl+,`):
- `airGesture.enablePreview`: Toggle the integrated sidebar camera preview feed (Default: `true`).
- `airGesture.debugWindow`: Show the traditional, external OpenCV window for advanced tracking diagnostics (Default: `false`).
- `airGesture.pushTrigger`: Select your preferred action for Git Push Control.

---

## 🛠️ Setup & Requirements

1. Ensure **Python 3.x** is installed and registered in your system PATH.
2. The extension will automatically prompt to install the required Python packages (`opencv-python`, `mediapipe`, `pyautogui`) the first time an engine starts.
3. Allow VS Code access to your primary webcam.

**Privacy Note**: All processing is done *locally* via MediaPipe. No images or video are ever transmitted over the internet.

---

*Navigate, format, and code—all without lifting a finger.*
