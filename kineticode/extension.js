const vscode = require('vscode');
const { spawn, exec } = require('child_process');
const path = require('path');
const fs = require('fs');

let childProcess = null;
let mainStatusBarItem = null;
let cameraProvider = null;
let outputChannel = null;
let activeMode = null;

function activate(context) {
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

        context.subscriptions.push(
            vscode.commands.registerCommand('air-gesture.selectMode', () => showModePicker(context)),
            vscode.commands.registerCommand('air-gesture.stop', () => stopDetection()),
            vscode.commands.registerCommand('air-gesture.showLogs', () => { if (outputChannel) outputChannel.show(); }),
            vscode.commands.registerCommand('air-gesture.diagnostics', () => runDiagnostics())
        );

        outputChannel.appendLine("Kineticode: Commands registered successfully.");
    } catch (err) {
        vscode.window.showErrorMessage(`Kineticode failed to activate: ${err.message}`);
    }
}

function showModePicker(context) {
    if (activeMode) {
        stopDetection();
        return;
    }
    const items = [
        { label: "$(zap) Macro Control", description: "Finger shapes (☝️, ✌️, 🤘, L)", id: 'macro' },
        { label: "$(sync) Tilt Navigation", description: "Head tilt left/right", id: 'tilt' },
        { label: "$(cloud-upload) Git Push Control", description: "Push gesture detection", id: 'push' },
        { label: "$(hand) Hand Control", description: "Zone-based navigation", id: 'hand' },
        { label: "$(eye) Face Control", description: "Wink detection", id: 'face' },
        { label: "$(files) Copy/Paste Control", description: "Fist/Open detection", id: 'copy_paste' },
        { label: "$(history) Script Trigger", description: "OK Sign detection to run a script", id: 'undo' }
    ];

    vscode.window.showQuickPick(items, { canPickMany: true }).then(async selections => {
        if (selections && selections.length > 0) {
            const modes = selections.map(s => s.id);
            startDetection(context, modes);
        }
    });
}

function startDetection(context, modes) {
    if (childProcess) stopDetection();
    activeMode = modes.join(' + ');

    const scriptPath = path.join(context.extensionPath, 'unified_engine.py');
    const config = vscode.workspace.getConfiguration('kineticode');
    const pythonPath = config.get('pythonPath') || 'python';
    const debugWindow = config.get('debugWindow') ? '--debug' : '';
    const enablePreview = config.get('enablePreview', true);
    const workspacePath = vscode.workspace.workspaceFolders?.[0]?.uri?.fsPath || context.extensionPath;

    const args = [scriptPath, '--extension', '--workspace', workspacePath];
    if (debugWindow) args.push(debugWindow);
    if (enablePreview) args.push('--stream');
    if (modes.includes('macro') || modes.includes('hand') || modes.includes('copy_paste') || modes.includes('undo') || modes.includes('push')) args.push('--hands');
    if (modes.includes('posture') || modes.includes('push')) args.push('--posture');
    if (modes.includes('tilt') || modes.includes('face')) args.push('--face');
    if (modes.includes('copy_paste')) args.push('--copy_paste');
    if (modes.includes('push')) args.push('--push');
    if (modes.includes('undo')) args.push('--undo');

    childProcess = spawn(pythonPath, args, { cwd: context.extensionPath });

    childProcess.stdout.on('data', (data) => {
        const lines = data.toString().split('\n');
        lines.forEach(line => {
            const trimmed = line.trim();
            if (!trimmed) return;
            try {
                const msg = JSON.parse(trimmed);
                if (msg.status === 'ready') {
                    vscode.window.showInformationMessage('Kineticode Ready!');
                    if (modes.includes('push')) vscode.window.showInformationMessage('🎯 Push Mode Active: PUSH PALM to start!');
                }
                else if (msg.status === 'awaiting_confirmation') vscode.window.showWarningMessage('🚀 Kineticode: Push Detected! Raise BOTH hands to CONFIRM.', { modal: false });
                else if (msg.error) vscode.window.showErrorMessage(`Kineticode Engine Error: ${msg.error}`);
                else if (msg.gesture) handleGesture(msg.gesture);
                else if (msg.action === 'git_push_trigger') handlePushTrigger(msg);
                else if (msg.action === 'copy') vscode.commands.executeCommand('editor.action.clipboardCopyAction');
                else if (msg.action === 'paste') vscode.commands.executeCommand('editor.action.clipboardPasteAction');
                else if (msg.status === 'active') {
                    outputChannel.appendLine(`[DIAG]: Hand Seen: ${msg.hand_seen}, Ratio: ${msg.push_ratio}, State: ${msg.state}`);
                }
                else if (msg.frame && cameraProvider) cameraProvider.updateFrame(msg.frame);
            } catch (e) {
                if (!trimmed.includes('"frame":')) outputChannel.appendLine(`[RAW]: ${trimmed}`);
            }
        });
    });

    // --- Selection State Tracker for Copy/Paste Engine ---
    const selectionListener = vscode.window.onDidChangeTextEditorSelection(e => {
        if (!childProcess || !activeMode.includes('copy_paste')) return;
        let hasSelection = false;
        for (const selection of e.selections) {
            if (!selection.isEmpty) {
                hasSelection = true;
                break;
            }
        }
        try {
            childProcess.stdin.write(JSON.stringify({ event: 'selection_changed', hasSelection }) + '\n');
        } catch (err) { }
    });
    context.subscriptions.push(selectionListener);

    childProcess.stderr.on('data', (data) => {
        outputChannel.appendLine(`[ENGINE ERROR]: ${data.toString()}`);
    });

    childProcess.on('error', (err) => {
        outputChannel.appendLine(`[SPAWN ERROR]: ${err.message}`);
        vscode.window.showErrorMessage(`Kineticode: Failed to start engine! ${err.message}`);
    });

    childProcess.on('close', (code) => {
        outputChannel.appendLine(`[ENGINE]: Process closed with code ${code}`);
        selectionListener.dispose();
        stopDetection();
    });
    updateStatusBar();
}

function stopDetection() {
    if (childProcess) {
        childProcess.kill();
        childProcess = null;
    }
    if (cameraProvider) cameraProvider.clear();
    activeMode = null;
    updateStatusBar();
}

function updateStatusBar() {
    if (activeMode) {
        const modes = activeMode.split(' + ').map(m => m.toUpperCase()).join(' + ');
        mainStatusBarItem.text = `$(circle-filled) KINETICODE: [${modes}]`;
        mainStatusBarItem.command = 'air-gesture.stop';
        mainStatusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
    } else {
        mainStatusBarItem.text = `$(broadcast) KINETICODE: SELECT MODE`;
        mainStatusBarItem.command = 'air-gesture.selectMode';
        mainStatusBarItem.backgroundColor = undefined;
    }
}

function handleGesture(gesture) {
    // 1. CONSTANT ACTIONS (Head Motions)
    if (gesture === 'tilt_left') {
        vscode.commands.executeCommand('workbench.action.previousEditor');
        return;
    }
    if (gesture === 'tilt_right') {
        vscode.commands.executeCommand('workbench.action.nextEditor');
        return;
    }
    if (gesture === 'wink') {
        vscode.commands.executeCommand('workbench.action.files.newUntitledFile');
        return;
    }

    // 2. CUSTOMIZABLE ACTIONS (Hand Gestures)
    const config = vscode.workspace.getConfiguration('kineticode');
    let actionKey = '';

    // Map internal gesture IDs to configuration keys
    if (gesture === 'gesture_one') actionKey = 'gestureOneAction';
    else if (gesture === 'gesture_peace') actionKey = 'gesturePeaceAction';
    else if (gesture === 'gesture_l') actionKey = 'gestureLAction';
    else if (gesture === 'ok_sign') actionKey = 'okSignAction';

    if (!actionKey) return;

    const action = config.get(actionKey);
    if (!action) return;

    if (action === 'custom_script') {
        executeCustomScript();
    } else {
        vscode.commands.executeCommand(action).then(
            () => { /* success */ },
            (err) => {
                outputChannel.appendLine(`[GESTURE ERROR]: Failed to execute command '${action}' for gesture '${gesture}': ${err}`);
                vscode.window.showErrorMessage(`Kineticode: Command '${action}' failed! Check spelling in settings.`);
            }
        );
    }
}

async function handlePushTrigger(message) {
    outputChannel.appendLine('[GIT PUSH]: Triggered via gesture.');

    try {
        const gitExtension = vscode.extensions.getExtension('vscode.git');
        if (!gitExtension) throw new Error('VS Code Git extension not found');

        const api = gitExtension.exports.getAPI(1);
        if (!api || !api.repositories || api.repositories.length === 0) {
            throw new Error('No Git repository found or API not ready');
        }

        const repository = api.repositories[0];

        // Stage changes
        await repository.add(['.']);

        // Robust check for any changes (staged or unstaged)
        const hasChanges = repository.state.workingTreeChanges.length > 0 ||
            repository.state.indexChanges.length > 0 ||
            repository.state.mergeChanges.length > 0;

        if (!hasChanges) {
            vscode.window.showInformationMessage('Kineticode: Nothing found to push.');
            return;
        }

        const commitMsg = `Kineticode Auto-Push: ${new Date().toLocaleString()}`;
        await repository.commit(commitMsg);

        // Push with error handling
        try {
            await repository.push();
            vscode.window.showInformationMessage('🚀 Kineticode: Git Push Success!');
        } catch (pushErr) {
            outputChannel.appendLine(`[PUSH ERROR]: ${pushErr}`);
            vscode.window.showWarningMessage('Kineticode: Commit successful, but push failed (check your remote).');
        }
    } catch (err) {
        outputChannel.appendLine(`[GIT FATAL ERROR]: ${err.message || err}`);
        vscode.window.showErrorMessage(`Kineticode: Push Failed! ${err.message || 'Check extension logs.'}`);

        // Terminal Fallback
        const terminal = vscode.window.terminals.find(t => t.name === 'Kineticode Git') || vscode.window.createTerminal('Kineticode Git');
        terminal.show();
        terminal.sendText('git add .');
        terminal.sendText(`git commit -m "Kineticode Recovery Commit: ${new Date().toLocaleString()}"`);
        terminal.sendText('git push');
    }
}

function executeCustomScript() {
    const config = vscode.workspace.getConfiguration('kineticode');
    const scriptPath = config.get('customScriptPath');

    if (!scriptPath) {
        vscode.window.showWarningMessage('Kineticode: No custom script path set in settings!');
        return;
    }

    if (!fs.existsSync(scriptPath)) {
        vscode.window.showErrorMessage(`Kineticode: Script not found at ${scriptPath}`);
        return;
    }

    outputChannel.appendLine(`Kineticode: Executing custom script: ${scriptPath}`);

    // Determine command based on extension
    let command = scriptPath;
    if (scriptPath.endsWith('.py')) {
        const pythonPath = config.get('pythonPath') || (process.platform === 'win32' ? 'python' : 'python3');
        command = `"${pythonPath}" "${scriptPath}"`;
    } else {
        command = `"${scriptPath}"`;
    }

    exec(command, (error, stdout, stderr) => {
        if (error) {
            outputChannel.appendLine(`[SCRIPT ERROR]: ${error.message}`);
            vscode.window.showErrorMessage(`Kineticode: Script execution failed! Check logs.`);
            return;
        }
        if (stderr) {
            outputChannel.appendLine(`[SCRIPT STDERR]: ${stderr}`);
        }
        outputChannel.appendLine(`[SCRIPT OUTPUT]: ${stdout}`);
        vscode.window.showInformationMessage('✅ Kineticode: Custom Script Executed!');
    });
}

class KineticodeViewProvider {
    constructor(extensionUri) { this._view = null; }
    resolveWebviewView(webviewView) {
        this._view = webviewView;
        webviewView.webview.options = { enableScripts: true };
        this.clear();
    }
    updateFrame(frame) {
        if (this._view) this._view.webview.postMessage({ command: 'updateFrame', frame });
    }
    clear() {
        if (this._view) {
            this._view.webview.postMessage({ command: 'clear' });
            this._view.webview.html = this._getHtmlForWebview();
        }
    }
    _getHtmlForWebview() {
        return `<html><body style="background:#1e1e1e;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;"><img id="stream" style="width:100%;max-height:100vh;object-fit:contain;" src=""/><div id="p" style="color:white;">Select Mode</div><script>const i=document.getElementById('stream');const p=document.getElementById('p');window.addEventListener('message',e=>{if(e.data.command==='updateFrame'){i.src='data:image/jpeg;base64,'+e.data.frame;i.style.display='block';p.style.display='none';}else{i.style.display='none';p.style.display='block';}});</script></body></html>`;
    }
}

async function runDiagnostics() { /* Simplified */ }
function deactivate() { stopDetection(); }

module.exports = { activate, deactivate };
