const vscode = require('vscode');
const { spawn } = require('child_process');
const path = require('path');

let childProcess = null;
let mainStatusBarItem = null;
let cameraProvider = null;
let originalFontSize = 14;
let activeMode = null;

function activate(context) {
    console.log('Kineticode Extension is now active!');

    mainStatusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    mainStatusBarItem.show();
    updateStatusBar();

    cameraProvider = new KineticodeViewProvider(context.extensionUri);
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider('kineticode.cameraView', cameraProvider)
    );

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
        { label: "$(rocket) Dual Control", description: "Hand Gestures + Posture", id: 'dual' },
        { label: "$(cloud-upload) Git Push Control", description: "Choose a gesture to trigger Git Push", id: 'push' },
        { label: "$(hand) Hand Control", description: "Zone-based tab navigation", id: 'hand' },
        { label: "$(person) Posture Control", description: "Font scaling via posture", id: 'posture' },
        { label: "$(eye) Face Control", description: "Wink to add a new tab", id: 'face' }
    ];

    vscode.window.showQuickPick(items, {
        placeHolder: 'Select Kineticode Engines to Enable (Space to toggle)',
        canPickMany: true
    }).then(async selections => {
        if (selections && selections.length > 0) {
            const hasPush = selections.some(s => s.id === 'push');
            if (hasPush) {
                // Default to physical push as requested, no more sub-menu
                await vscode.workspace.getConfiguration('airGesture').update('pushTrigger', 'physical_push', vscode.ConfigurationTarget.Global);
            }
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
                        const terminal = vscode.window.createTerminal("Kineticode Install");
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

    // Determine which script to run. If 'push' is included, we use push_engine.py 
    // for now because unified_engine.py doesn't have the Git logic I built.
    // If multiple are selected including push, we'll need to decide. 
    // To keep it simple and preserve features, if 'push' is in modes, we prioritize it.
    let scriptName = modes.includes('push') ? 'push_engine.py' : 'unified_engine.py';
    const scriptPath = path.join(context.extensionPath, scriptName);
    const pythonCommand = process.platform === 'win32' ? 'python' : 'python3';

    if (modes.includes('posture')) {
        originalFontSize = vscode.workspace.getConfiguration('editor').get('fontSize');
    }

    const config = vscode.workspace.getConfiguration('airGesture');
    const debug = config.get('debugWindow', false);
    const enablePreview = config.get('enablePreview', true);

    const workspacePath = vscode.workspace.workspaceFolders?.[0]?.uri?.fsPath || context.extensionPath;

    const args = [scriptPath, '--extension', '--workspace', workspacePath, '--debug', debug.toString()];
    if (enablePreview) args.push('--stream');
    if (modes.includes('hand') || modes.includes('dual')) args.push('--hands');
    if (modes.includes('posture') || modes.includes('dual')) args.push('--posture');
    if (modes.includes('face')) args.push('--face');

    childProcess = spawn(pythonCommand, args, {
        cwd: context.extensionPath
    });

    let lineBuffer = '';
    childProcess.stdout.on('data', (data) => {
        lineBuffer += data.toString();
        const lines = lineBuffer.split('\n');
        lineBuffer = lines.pop();

        lines.forEach(line => {
            if (!line.trim()) return;
            try {
                const msg = JSON.parse(line.trim());
                if (msg.status === 'ready') {
                    vscode.window.showInformationMessage('Kineticode Started!');
                } else if (msg.status === 'awaiting_confirmation') {
                    vscode.window.showInformationMessage('Push Detected! Confirm by raising both hands.', 'Cancel Push').then(selection => {
                        if (selection === 'Cancel Push') {
                            vscode.window.showInformationMessage('Push aborted. Wait for timeout.');
                        }
                    });
                } else if (msg.action === 'git_push') {
                    handlePushTrigger(msg);
                } else if (msg.posture) {
                    handlePosture(msg.posture);
                } else if (msg.frame && cameraProvider) {
                    cameraProvider.updateFrame(msg.frame);
                } else if (msg.error) {
                    vscode.window.showErrorMessage(`Kineticode Error: ${msg.error}`);
                    stopDetection();
                }
            } catch (e) {
                console.log(`Engine Output: ${line}`);
            }
        });
    });

    childProcess.on('close', (code) => {
        console.log(`Engine process exited with code ${code}`);
        if (code !== 0 && activeMode) {
            vscode.window.showErrorMessage(`Air Gesture Engine stopped unexpectedly (Code: ${code}). Check if another app is using the camera.`);
        }
        stopDetection();
    });
    updateStatusBar();
}

function stopDetection() {
    if (childProcess) {
        childProcess.kill();
        childProcess = null;
    }

    if (activeMode && activeMode.includes('posture')) {
        vscode.workspace.getConfiguration('editor').update('fontSize', originalFontSize, vscode.ConfigurationTarget.Global);
    }

    if (cameraProvider) {
        cameraProvider.clear();
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

function handleGesture(gesture) {
    if (!gesture) return;
    if (gesture === 'swipe_left') vscode.commands.executeCommand('workbench.action.previousEditor');
    else if (gesture === 'swipe_right') vscode.commands.executeCommand('workbench.action.nextEditor');
    else if (gesture === 'clap') vscode.commands.executeCommand('workbench.action.files.newUntitledFile');
}

function handlePushTrigger(message) {
    if (!message) return;

    const trigger = vscode.workspace.getConfiguration('airGesture').get('pushTrigger', 'physical_push');
    let triggered = false;

    if (trigger === 'physical_push' && message.action === 'git_push') {
        if (message.success) {
            vscode.window.showInformationMessage('🚀 Push Successful: Your code is safe on GitHub!');
        } else {
            vscode.window.showErrorMessage('❌ Git Push Failed! Check the Debug Console for details.');
        }
        triggered = true;
    } else if (message.gesture === trigger) {
        triggered = true;
    }

    if (triggered && trigger !== 'physical_push') {
        vscode.window.showInformationMessage('🚀 Gesture Detected: Starting Git Push...', 'Commit & Push').then(selection => {
            if (selection === 'Commit & Push') {
                executeGitPush();
            }
        });
    }
}

async function executeGitPush() {
    const terminal = vscode.window.terminals.find(t => t.name === "Git Push") || vscode.window.createTerminal("Git Push");
    terminal.show();
    terminal.sendText('git add . && git commit -m "Auto-push from Air Gesture" && git push');
}

async function handlePosture(stateOrMessage) {
    const state = (typeof stateOrMessage === 'string') ? stateOrMessage : stateOrMessage.posture;
    if (!state) return;

    const config = vscode.workspace.getConfiguration();
    if (state === 'slouch') {
        // Slouching makes font SMALL (penalty/nudge)
        config.update('editor.fontSize', Math.max(8, originalFontSize - 4), vscode.ConfigurationTarget.Global);
        vscode.window.setStatusBarMessage('🚨 POSTURE: Slouching! Shrinking font...', 2000);
    } else {
        // Upright restores font to BIG (normal)
        config.update('editor.fontSize', originalFontSize, vscode.ConfigurationTarget.Global);
        vscode.window.setStatusBarMessage('✅ POSTURE: Good! Restoring font...', 2000);
    }
}

class KineticodeViewProvider {
    constructor(extensionUri) {
        this._extensionUri = extensionUri;
        this._view = null;
    }

    resolveWebviewView(webviewView) {
        this._view = webviewView;
        webviewView.webview.options = { enableScripts: true };
        this.clear();
    }

    updateFrame(frame) {
        if (this._view) {
            this._view.webview.postMessage({ command: 'updateFrame', frame });
        }
    }

    clear() {
        if (this._view) {
            this._view.webview.postMessage({ command: 'clear' });
            this._view.webview.html = this._getHtmlForWebview();
        }
    }

    _getHtmlForWebview() {
        return `
            <!DOCTYPE html>
            <html>
            <body style="background: #1e1e1e; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; color: white; font-family: sans-serif;">
                <img id="video-stream" style="width: 100%; border: 1px solid #333; display: none;" src=""/>
                <div id="placeholder" style="text-align: center; padding: 20px;">
                    <div style="font-size: 40px; margin-bottom: 10px;">📸</div>
                    <div>Select a mode to start the camera feed</div>
                </div>
                <script>
                    const img = document.getElementById('video-stream');
                    const placeholder = document.getElementById('placeholder');
                    window.addEventListener('message', event => {
                        const message = event.data;
                        if (message.command === 'updateFrame') {
                            img.src = 'data:image/jpeg;base64,' + message.frame;
                            img.style.display = 'block';
                            placeholder.style.display = 'none';
                        } else if (message.command === 'clear') {
                            img.style.display = 'none';
                            img.src = '';
                            placeholder.style.display = 'block';
                        }
                    });
                </script>
            </body>
            </html>
        `;
    }
}

function deactivate() {
    stopDetection();
}

module.exports = {
    activate,
    deactivate
};
