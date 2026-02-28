const vscode = require('vscode');
const { spawn } = require('child_process');
const path = require('path');

let childProcess = null;
let mainStatusBarItem = null;
let originalFontSize = 14;
let activeMode = null;

function activate(context) {
    console.log('Air Gesture Extension is now active!');

    mainStatusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    mainStatusBarItem.show();
    updateStatusBar();

    const selectModeCommand = vscode.commands.registerCommand('air-gesture.selectMode', () => {
        showModePicker(context);
    });

    const startHandCommand = vscode.commands.registerCommand('air-gesture.startHand', () => {
        startDetection(context, 'hand');
    });

    const startPostureCommand = vscode.commands.registerCommand('air-gesture.startPosture', () => {
        startDetection(context, 'posture');
    });

    const startDualCommand = vscode.commands.registerCommand('air-gesture.startDual', () => {
        startDetection(context, 'dual');
    });


    const stopCommand = vscode.commands.registerCommand('air-gesture.stop', () => {
        stopDetection();
    });

    context.subscriptions.push(selectModeCommand, startHandCommand, startPostureCommand, startDualCommand, stopCommand, mainStatusBarItem);
}

function showModePicker(context) {
    if (activeMode) {
        stopDetection();
        return;
    }

    const items = [
        { label: "$(rocket) Dual Control", description: "Hand Gestures + Posture", id: 'dual' },
        { label: "$(hand) Hand Control", description: "Zone-based tab navigation", id: 'hand' },
        { label: "$(person) Posture Control", description: "Font scaling based on posture", id: 'posture' }
    ];

    vscode.window.showQuickPick(items, { placeHolder: 'Select Air Control Mode' }).then(async selection => {
        if (selection) {
            const ready = await checkDependencies();
            if (ready) {
                startDetection(context, selection.id);
            }
        }
    });
}

async function checkDependencies() {
    const pythonCommand = process.platform === 'win32' ? 'python' : 'python3';
    return new Promise((resolve) => {
        const check = spawn(pythonCommand, ['-c', 'import cv2, mediapipe, pyautogui, numpy; print("READY")']);
        check.on('error', () => {
            vscode.window.showErrorMessage("Python 3 not found! Please install Python to use Air Gesture.", "Download Python").then(selection => {
                if (selection === "Download Python") vscode.env.openExternal(vscode.Uri.parse("https://www.python.org/downloads/"));
            });
            resolve(false);
        });

        let output = '';
        check.stdout.on('data', (data) => output += data.toString());
        check.on('close', (code) => {
            if (code === 0 && output.includes("READY")) {
                resolve(true);
            } else {
                vscode.window.showErrorMessage("Missing Python dependencies (opencv, mediapipe, etc.). Install them now?", "Install", "Cancel").then(selection => {
                    if (selection === "Install") {
                        const terminal = vscode.window.createTerminal("Air Gesture Install");
                        terminal.show();
                        terminal.sendText(`${pythonCommand} -m pip install opencv-python mediapipe pyautogui numpy`);
                        vscode.window.showInformationMessage("Installing dependencies... Please restart the mode once finished.");
                    }
                });
                resolve(false);
            }
        });
    });
}

function startDetection(context, mode) {
    if (childProcess) {
        stopDetection();
    }

    activeMode = mode;
    let scriptName;
    if (mode === 'hand') scriptName = 'gesture_engine.py';
    else if (mode === 'posture') scriptName = 'posture_engine.py';
    else scriptName = 'unified_engine.py';

    const scriptPath = path.join(context.extensionPath, scriptName);
    const pythonCommand = process.platform === 'win32' ? 'python' : 'python3';

    if (mode === 'posture' || mode === 'dual') {
        originalFontSize = vscode.workspace.getConfiguration('editor').get('fontSize');
    }

    const config = vscode.workspace.getConfiguration('airGesture');
    const debug = config.get('debugWindow', true);
    const snapThreshold = config.get('snapThreshold', 0.05);

    childProcess = spawn(pythonCommand, [
        scriptPath,
        '--extension',
        '--debug', debug.toString(),
        '--snap_threshold', snapThreshold.toString()
    ], {
        cwd: context.extensionPath
    });

    let buffer = '';
    childProcess.stdout.on('data', (data) => {
        buffer += data.toString();
        const lines = buffer.split('\n');
        buffer = lines.pop();

        for (const line of lines) {
            if (line.trim()) {
                try {
                    const message = JSON.parse(line);
                    if (mode === 'dual') {
                        handleGesture(message);
                        handlePosture(message);
                    } else if (mode === 'hand') {
                        handleGesture(message);
                    } else if (mode === 'posture') {
                        handlePosture(message);
                    }
                } catch (e) { }
            }
        }
    });

    childProcess.on('close', () => stopDetection());
    updateStatusBar();
}

function stopDetection() {
    if (childProcess) {
        childProcess.kill();
        childProcess = null;
    }

    if (activeMode === 'posture' || activeMode === 'dual') {
        vscode.workspace.getConfiguration('editor').update('fontSize', originalFontSize, vscode.ConfigurationTarget.Global);
    }

    activeMode = null;
    updateStatusBar();
}

function updateStatusBar() {
    if (activeMode) {
        mainStatusBarItem.text = `$(circle-filled) Air ${activeMode.toUpperCase()}: On (Stop)`;
        mainStatusBarItem.command = 'air-gesture.stop';
        mainStatusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
    } else {
        mainStatusBarItem.text = `$(broadcast) Air Control: Select Mode`;
        mainStatusBarItem.command = 'air-gesture.selectMode';
        mainStatusBarItem.backgroundColor = undefined;
    }
}

function handleGesture(message) {
    if (!message || !message.gesture) return;
    if (message.gesture === 'swipe_left') vscode.commands.executeCommand('workbench.action.previousEditor');
    else if (message.gesture === 'swipe_right') vscode.commands.executeCommand('workbench.action.nextEditor');
    else if (message.gesture === 'snap') vscode.commands.executeCommand('workbench.action.files.newUntitledFile');
}

async function handlePosture(message) {
    if (!message || !message.posture) return;

    const config = vscode.workspace.getConfiguration('editor');
    if (message.posture === 'slouch') {
        vscode.window.setStatusBarMessage('🚨 POSTURE: Slouching! Shrinking font...', 2000);
        await config.update('fontSize', 8, vscode.ConfigurationTarget.Global);
    } else if (message.posture === 'upright') {
        vscode.window.setStatusBarMessage('✅ POSTURE: Good! Restoring font...', 2000);
        await config.update('fontSize', originalFontSize, vscode.ConfigurationTarget.Global);
    }
}


function deactivate() {
    stopDetection();
}

module.exports = {
    activate,
    deactivate
};
