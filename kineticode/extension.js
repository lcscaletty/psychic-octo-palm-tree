const vscode = require('vscode');
const { spawn } = require('child_process');
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
        { label: "$(history) Undo Gesture", description: "OK Sign detection", id: 'undo' }
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
    const config = vscode.workspace.getConfiguration('airGesture');
    const pythonCommand = config.get('pythonPath') || (process.platform === 'win32' ? 'python' : 'python3');

    const debug = config.get('debugWindow', false);
    const enablePreview = config.get('enablePreview', true);
    const workspacePath = vscode.workspace.workspaceFolders?.[0]?.uri?.fsPath || context.extensionPath;

    const args = [scriptPath, '--extension', '--workspace', workspacePath, '--debug', debug.toString()];
    if (enablePreview) args.push('--stream');
    if (modes.includes('macro') || modes.includes('hand') || modes.includes('copy_paste') || modes.includes('undo')) args.push('--hands');
    if (modes.includes('posture') || modes.includes('push')) args.push('--posture');
    if (modes.includes('tilt') || modes.includes('face')) args.push('--face');
    if (modes.includes('copy_paste')) args.push('--copy_paste');
    if (modes.includes('push')) args.push('--push');
    if (modes.includes('undo')) args.push('--undo');

    childProcess = spawn(pythonCommand, args, { cwd: context.extensionPath });

    childProcess.stdout.on('data', (data) => {
        const lines = data.toString().split('\n');
        lines.forEach(line => {
            const trimmed = line.trim();
            if (!trimmed) return;
            try {
                const msg = JSON.parse(trimmed);
                if (msg.status === 'ready') vscode.window.showInformationMessage('Kineticode Ready!');
                else if (msg.gesture) handleGesture(msg.gesture);
                else if (msg.action === 'git_push_trigger') handlePushTrigger(msg);
                else if (msg.action === 'copy') vscode.commands.executeCommand('editor.action.clipboardCopyAction');
                else if (msg.action === 'paste') vscode.commands.executeCommand('editor.action.clipboardPasteAction');
                else if (msg.action === 'undo') vscode.commands.executeCommand('undo');
                else if (msg.frame && cameraProvider) cameraProvider.updateFrame(msg.frame);
            } catch (e) {
                if (!trimmed.includes('"frame":')) outputChannel.appendLine(`[RAW]: ${trimmed}`);
            }
        });
    });

    childProcess.on('close', () => stopDetection());
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
        mainStatusBarItem.text = `$(circle-filled) KINETICODE: ON`;
        mainStatusBarItem.command = 'air-gesture.stop';
    } else {
        mainStatusBarItem.text = `$(broadcast) KINETICODE: OFF`;
        mainStatusBarItem.command = 'air-gesture.selectMode';
    }
}

function handleGesture(gesture) {
    if (gesture === 'swipe_left') vscode.commands.executeCommand('workbench.action.previousEditor');
    else if (gesture === 'swipe_right') vscode.commands.executeCommand('workbench.action.nextEditor');
    else if (gesture === 'clap') vscode.commands.executeCommand('workbench.action.files.newUntitledFile');
    else if (gesture === 'gesture_one') vscode.commands.executeCommand('workbench.action.moveEditorToNextGroup');
    else if (gesture === 'gesture_peace') vscode.commands.executeCommand('workbench.action.splitEditor');
    else if (gesture === 'gesture_l') vscode.commands.executeCommand('actions.find');
}

function handlePushTrigger(message) {
    let terminal = vscode.window.terminals.find(t => t.name === 'Kineticode Git');
    if (!terminal) terminal = vscode.window.createTerminal('Kineticode Git');
    terminal.show();
    terminal.sendText(`git add .`);
    terminal.sendText(`git commit -m "Auto-push: ${new Date().toLocaleString()}"`);
    terminal.sendText(`git push`);
    vscode.window.showInformationMessage('🚀 Kineticode: Git Push Triggered!');
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
