const vscode = require('vscode');
const { spawn } = require('child_process');
const path = require('path');

let childProcess = null;
let mainStatusBarItem = null;
let originalFontSize = 14;
let activeMode = null;

function activate(context) {
    console.log('Kineticode Extension is now active!');

    mainStatusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    mainStatusBarItem.show();
    updateStatusBar();

    const selectModeCommand = vscode.commands.registerCommand('air-gesture.selectMode', () => {
        showModePicker(context);
    });

    const stopCommand = vscode.commands.registerCommand('air-gesture.stop', () => {
        stopDetection();
    });

    context.subscriptions.push(selectModeCommand, stopCommand, mainStatusBarItem);
}

function showModePicker(context) {
    if (activeMode) {
        stopDetection();
        return;
    }

    const items = [
        { label: "$(hand) Hand Control", description: "Tab navigation via swipes", id: 'hand' },
        { label: "$(person) Posture Control", description: "Font scaling via posture", id: 'posture' },
        { label: "$(eye) Face Control", description: "Wink to add a new tab", id: 'face' }
    ];

    vscode.window.showQuickPick(items, {
        placeHolder: 'Select Kineticode Engines to Enable (Space to toggle)',
        canPickMany: true
    }).then(async selections => {
        if (selections && selections.length > 0) {
            const ready = await checkDependencies();
            if (ready) {
                const modes = selections.map(s => s.id);
                startDetection(context, modes);
            }
        }
    });
}

async function checkDependencies() {
    const pythonCommand = process.platform === 'win32' ? 'python' : 'python3';
    return new Promise((resolve) => {
        const check = spawn(pythonCommand, ['-c', 'import cv2, mediapipe, pyautogui, numpy; print("READY")']);
        check.on('error', () => {
            vscode.window.showErrorMessage("Python 3 not found! Please install Python to use Kineticode.", "Download Python").then(selection => {
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

function startDetection(context, modes) {
    if (childProcess) {
        stopDetection();
    }

    activeMode = modes.join(' + ');
    const scriptPath = path.join(context.extensionPath, 'unified_engine.py');
    const pythonCommand = process.platform === 'win32' ? 'python' : 'python3';

    if (modes.includes('posture')) {
        originalFontSize = vscode.workspace.getConfiguration('editor').get('fontSize');
    }

    const config = vscode.workspace.getConfiguration('airGesture');
    const debug = config.get('debugWindow', true);

    const args = [scriptPath, '--extension', '--debug', debug.toString()];
    if (modes.includes('hand')) args.push('--hands');
    if (modes.includes('posture')) args.push('--posture');
    if (modes.includes('face')) args.push('--face');

    childProcess = spawn(pythonCommand, args, {
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
                    handleGesture(message);
                    handlePosture(message);
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

    if (activeMode && activeMode.includes('POSTURE')) {
        vscode.workspace.getConfiguration('editor').update('fontSize', originalFontSize, vscode.ConfigurationTarget.Global);
    }

    activeMode = null;
    updateStatusBar();
}

function updateStatusBar() {
    if (activeMode) {
        mainStatusBarItem.text = `$(circle-filled) Kineticode ${activeMode.toUpperCase()}: On (Stop)`;
        mainStatusBarItem.command = 'air-gesture.stop';
        mainStatusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
    } else {
        mainStatusBarItem.text = `$(broadcast) Kineticode: Select Mode`;
        mainStatusBarItem.command = 'air-gesture.selectMode';
        mainStatusBarItem.backgroundColor = undefined;
    }
}

function handleGesture(message) {
    if (!message || !message.gesture) return;
    if (message.gesture === 'swipe_left') vscode.commands.executeCommand('workbench.action.previousEditor');
    else if (message.gesture === 'swipe_right') vscode.commands.executeCommand('workbench.action.nextEditor');
    else if (message.gesture === 'clap') vscode.commands.executeCommand('workbench.action.files.newUntitledFile');
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
