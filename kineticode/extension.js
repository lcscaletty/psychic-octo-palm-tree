const vscode = require('vscode');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

let childProcess = null;
let mainStatusBarItem = null;
let cameraProvider = null;
let outputChannel = null;
let originalFontSize = 14;
let activeMode = null;

function activate(context) {
    // 1. Initialize logs IMMEDIATELY
    outputChannel = vscode.window.createOutputChannel("Kineticode Logs");
    outputChannel.appendLine("Kineticode: Activation starting...");

    try {
        mainStatusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
        mainStatusBarItem.show();
        updateStatusBar();

        cameraProvider = new KineticodeViewProvider(context.extensionUri);
        context.subscriptions.push(
            vscode.window.registerWebviewViewProvider('kineticode.cameraView', cameraProvider)
        );

        // Register commands immediately
        context.subscriptions.push(
            vscode.commands.registerCommand('air-gesture.selectMode', () => showModePicker(context)),
            vscode.commands.registerCommand('air-gesture.stop', () => stopDetection()),
            vscode.commands.registerCommand('air-gesture.showLogs', () => { if (outputChannel) outputChannel.show(); }),
            vscode.commands.registerCommand('air-gesture.diagnostics', () => runDiagnostics())
        );

        outputChannel.appendLine("Kineticode: Commands registered successfully.");
        console.log('Kineticode Extension is now active!');

    } catch (err) {
        if (outputChannel) outputChannel.appendLine(`Kineticode: CRITICAL ACTIVATION ERROR: ${err.message}`);
        vscode.window.showErrorMessage(`Kineticode failed to activate: ${err.message}`);
    }
}

function showModePicker(context) {
    if (activeMode) {
        stopDetection();
        return;
    }
    const items = [
        { label: "$(zap) Macro Control", description: "Finger shapes (☝️: Move Tab, ✌️: Split, 🤘: Terminal, L: Find)", id: 'macro' },
        { label: "$(sync) Tilt Navigation", description: "Switch tabs by tilting your head left/right", id: 'tilt' },
        { label: "$(cloud-upload) Git Push Control", description: "Choose a gesture to trigger Git Push", id: 'push' },
        { label: "$(hand) Hand Control", description: "Zone-based tab navigation", id: 'hand' },
        { label: "$(eye) Face Control", description: "Wink to open a new tab", id: 'face' },
        { label: "$(files) Copy/Paste Control", description: "Fist to copy, Open hand to paste", id: 'copy_paste' }
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

    // Determine which script to run. ALWAYS use unified engine now.
    const scriptName = 'unified_engine.py';
    const scriptPath = path.join(context.extensionPath, scriptName);

    // --- ISOLATED PYTHON DISCOVERY ---
    // We ignore 'python.defaultInterpreterPath' to avoid triggering the MS Python extension's broken logic.
    const config = vscode.workspace.getConfiguration('airGesture');
    let pythonCommand = config.get('pythonPath') || (process.platform === 'win32' ? 'python' : 'python3');

    outputChannel.appendLine(`Kineticode: Attempting to start with Python: ${pythonCommand}`);

    if (path.isAbsolute(pythonCommand) && !fs.existsSync(pythonCommand)) {
        outputChannel.appendLine(`Kineticode: Absolute path ${pythonCommand} not found. Falling back to system default.`);
        pythonCommand = process.platform === 'win32' ? 'python' : 'python3';
    }

    // Original Font Size removed (Posture mode disabled)

    const debug = config.get('debugWindow', false);
    const enablePreview = config.get('enablePreview', true);

    const workspacePath = vscode.workspace.workspaceFolders?.[0]?.uri?.fsPath || context.extensionPath;

    const args = [scriptPath, '--extension', '--workspace', workspacePath, '--debug', debug.toString()];
    if (enablePreview) args.push('--stream');

    // Enable relevant engines based on selected modes
    if (modes.includes('macro') || modes.includes('hand') || modes.includes('copy_paste')) args.push('--hands');
    if (modes.includes('posture')) args.push('--posture');
    if (modes.includes('tilt') || modes.includes('face')) args.push('--face');
    if (modes.includes('copy_paste')) args.push('--copy_paste');
    if (modes.includes('push')) args.push('--push');

    outputChannel.appendLine(`Kineticode: Command = ${pythonCommand}`);
    outputChannel.appendLine(`Kineticode: Args = ${args.join(' ')}`);

    childProcess = spawn(pythonCommand, args, {
        cwd: context.extensionPath
    });

    childProcess.on('error', (err) => {
        outputChannel.appendLine(`Kineticode: SPAWN ERROR: ${err.message}`);
        outputChannel.show(); // POP UP LOGS ON FAILURE
        vscode.window.showErrorMessage(`Kineticode: Failed to start Python engine (${err.message}). Please ensure Python is installed and in your PATH.`);
        stopDetection();
    });

    childProcess.stderr.on('data', (data) => {
        const str = data.toString();
        console.error(`Python Engine Error: ${str}`);
        if (outputChannel) outputChannel.appendLine(`[STDERR]: ${str.trim()}`);
    });

    // --- Selection State Tracker for Copy/Paste Engine ---
    const selectionListener = vscode.window.onDidChangeTextEditorSelection(e => {
        if (!childProcess || !activeMode.includes('copy_paste')) return;

        let hasSelection = false;
        // Check if any selection has actual length
        for (const selection of e.selections) {
            if (!selection.isEmpty) {
                hasSelection = true;
                break;
            }
        }

        // Send state to python engine via stdin
        try {
            const msg = JSON.stringify({ event: 'selection_changed', hasSelection: hasSelection });
            childProcess.stdin.write(msg + '\n');
        } catch (err) {
            console.error("Failed to send selection state to engine", err);
        }
    });
    context.subscriptions.push(selectionListener);

    let lineBuffer = '';
    childProcess.stdout.on('data', (data) => {
        lineBuffer += data.toString();
        const lines = lineBuffer.split('\n');
        lineBuffer = lines.pop();

        lines.forEach(line => {
            const trimmed = line.trim();
            if (!trimmed) return;

            // --- LOG FILTERING ---
            // Only log if it's NOT a massive binary/coordinate message
            if (outputChannel && !trimmed.includes('"frame":') && !trimmed.includes('"pose":') && !trimmed.includes('"eye":')) {
                outputChannel.appendLine(`[STDOUT]: ${trimmed}`);
            }

            try {
                const msg = JSON.parse(trimmed);
                if (msg.status === 'ready') {
                    vscode.window.showInformationMessage('Kineticode Started!');
                } else if (msg.gesture) {
                    handleGesture(msg.gesture);
                } else if (msg.status === 'awaiting_confirmation') {
                    vscode.window.showInformationMessage('Push Detected! Confirm by raising both hands.', 'Cancel Push').then(selection => {
                        if (selection === 'Cancel Push') {
                            vscode.window.showInformationMessage('Push aborted. Wait for timeout.');
                        }
                    });
                } else if (msg.status === 'pushing_in_progress') {
                    vscode.window.showInformationMessage('Hands up detected! 🚀 Initiating Git Push...');
                } else if (msg.status === 'push_aborted') {
                    vscode.window.showInformationMessage('Git Push confirmation timed out. Push aborted.');
                } else if (msg.action === 'git_push_trigger') {
                    handlePushTrigger(msg);
                } else if (msg.action === 'copy') {
                    vscode.commands.executeCommand('editor.action.clipboardCopyAction');
                } else if (msg.action === 'paste') {
                    vscode.commands.executeCommand('editor.action.clipboardPasteAction');
                } else if (msg.posture) {
                    // Posture handling disabled
                    // handlePosture(msg.posture);
                } else if (msg.frame && cameraProvider) {
                    cameraProvider.updateFrame(msg.frame);
                } else if (msg.error) {
                    vscode.window.showErrorMessage(`Kineticode Error: ${msg.error}`);
                    outputChannel.show(); // POP UP LOGS ON ENGINE ERROR
                    stopDetection();
                }
            } catch (e) {
                if (outputChannel) outputChannel.appendLine(`[RAW]: ${trimmed}`);
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

    /* Posture cleanup removed 
    if (activeMode && activeMode.includes('posture')) {
        vscode.workspace.getConfiguration('editor').update('fontSize', originalFontSize, vscode.ConfigurationTarget.Global);
    }
    */

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

    // Macro Gestures
    else if (gesture === 'gesture_one') vscode.commands.executeCommand('workbench.action.moveEditorToNextGroup');
    else if (gesture === 'gesture_peace') vscode.commands.executeCommand('workbench.action.splitEditor');
    else if (gesture === 'gesture_rock') {
        const terminal = vscode.window.terminals[0] || vscode.window.createTerminal();
        terminal.show();
    }
    else if (gesture === 'gesture_l') vscode.commands.executeCommand('actions.find');
}

function handlePushTrigger(message) {
    if (!message) return;

    // The engine now sends a 'trigger' for us to take over
    if (message.action === 'git_push_trigger') {
        outputChannel.appendLine('🚀 Kineticode: Received Push Gesture. Executing Git sequence in terminal...');

        let terminal = vscode.window.terminals.find(t => t.name === 'Kineticode Git');
        if (!terminal) {
            terminal = vscode.window.createTerminal('Kineticode Git');
        }

        terminal.show();
        const timestamp = new Date().toLocaleString();
        const commitMsg = `Auto-push from Kineticode: ${timestamp}`;

        // Simple, robust command sequence
        terminal.sendText(`git add .`);
        terminal.sendText(`git commit -m "${commitMsg}"`);
        terminal.sendText(`git push`);

        vscode.window.showInformationMessage('🚀 Kineticode: Starting Git Push in terminal...');
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

async function runDiagnostics() {
    if (outputChannel) {
        outputChannel.show();
        outputChannel.appendLine("\n--- KINETICODE DIAGNOSTICS ---");
    }

    const config = vscode.workspace.getConfiguration('airGesture');
    const pythonCommand = config.get('pythonPath') || (process.platform === 'win32' ? 'python' : 'python3');

    outputChannel.appendLine(`Diagnostic: Platform = ${process.platform}`);
    outputChannel.appendLine(`Diagnostic: Configured Python Path = ${pythonCommand}`);

    // Check version
    const check = spawn(pythonCommand, ['--version']);
    check.stdout.on('data', (data) => outputChannel.appendLine(`Diagnostic: Python Version Out = ${data.toString().trim()}`));
    check.stderr.on('data', (data) => outputChannel.appendLine(`Diagnostic: Python Version Err = ${data.toString().trim()}`));

    check.on('error', (err) => {
        outputChannel.appendLine(`Diagnostic: FAILED to run python command. Error: ${err.message}`);
        vscode.window.showErrorMessage(`Diagnostics Failed: Python command '${pythonCommand}' could not be executed.`);
    });

    check.on('close', (code) => {
        outputChannel.appendLine(`Diagnostic: Version check exited with code ${code}`);
        if (code === 0) {
            outputChannel.appendLine("Diagnostic: Testing heart-beat (importing libraries)...");
            const libCheck = spawn(pythonCommand, ['-c', 'import cv2, mediapipe, pyautogui, numpy; print("LIBRARIES_OK")']);
            libCheck.stdout.on('data', (data) => {
                if (data.toString().includes("LIBRARIES_OK")) {
                    outputChannel.appendLine("Diagnostic: Libraries verified successfully! 🏃‍♂️");
                    vscode.window.showInformationMessage("Kineticode Diagnostics: Environment looks perfect!");
                }
            });
            libCheck.on('close', (c) => {
                if (c !== 0) outputChannel.appendLine(`Diagnostic: Library check failed with code ${c}`);

                // Final Check: GIT IDENTITY
                const gitCheck = spawn('git', ['config', 'user.email']);
                gitCheck.stdout.on('data', (d) => outputChannel.appendLine(`Diagnostic: Git Identity = ${d.toString().trim()}`));
                gitCheck.on('close', (gc) => {
                    if (gc !== 0) {
                        outputChannel.appendLine("Diagnostic: Git Identity (email) NOT FOUND! Commits will fail.");
                        vscode.window.showWarningMessage("Git Identity not set! Use 'Fix Git' in Diagnostics.", "Fix Git").then(s => {
                            if (s === "Fix Git") {
                                const term = vscode.window.createTerminal("Kineticode Git Fix");
                                term.show();
                                term.sendText("git config --global user.email 'you@example.com' && git config --global user.name 'Your Name' && git pull");
                            }
                        });
                    } else {
                        outputChannel.appendLine("Diagnostic: Git Identity OK! 🚀");
                    }
                });
            });
        }
    });
}

function deactivate() {
    stopDetection();
}

module.exports = {
    activate,
    deactivate
};
