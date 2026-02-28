const vscode = require('vscode');
const { spawn } = require('child_process');
const path = require('path');

let childProcess = null;
let statusBarItem = null;

function activate(context) {
    console.log('Air Gesture Extension is now active!');

    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.text = "$(feedback) Air Gesture: Off";
    statusBarItem.command = 'air-gesture.start';
    statusBarItem.show();

    const startCommand = vscode.commands.registerCommand('air-gesture.start', () => {
        if (childProcess) {
            vscode.window.showInformationMessage('Air Gesture Engine is already running.');
            return;
        }

        const scriptPath = path.join(context.extensionPath, 'gesture_engine.py');
        const pythonCommand = process.platform === 'win32' ? 'python' : 'python3';

        console.log(`Spawning Gesture Engine: ${pythonCommand} ${scriptPath}`);

        childProcess = spawn(pythonCommand, [scriptPath, '--extension'], {
            cwd: context.extensionPath
        });

        childProcess.on('error', (err) => {
            console.error('Failed to start Python process:', err);
            vscode.window.showErrorMessage(`Failed to start Gesture Engine: ${err.message}. Ensure Python is in your PATH.`);
            stopDetection();
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
                    } catch (e) {
                        console.log(`Output: ${line}`);
                    }
                }
            }
        });

        childProcess.stderr.on('data', (data) => {
            const errorOutput = data.toString();
            console.error(`Gesture Engine Error: ${errorOutput}`);
            // Don't show every error in UI to avoids spam, but log it.
        });

        childProcess.on('close', (code) => {
            if (code !== 0 && code !== null) {
                vscode.window.showErrorMessage(`Gesture Engine crashed with code ${code}. Check the Debug Console.`);
            }
            console.log(`Gesture Engine exited with code ${code}`);
            stopDetection();
        });

        statusBarItem.text = "$(feedback) Air Gesture: ON";
        statusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
        statusBarItem.command = 'air-gesture.stop';
        vscode.window.showInformationMessage('Air Gesture Detection Started!');
    });

    const stopCommand = vscode.commands.registerCommand('air-gesture.stop', () => {
        stopDetection();
    });

    context.subscriptions.push(startCommand, stopCommand, statusBarItem);
}

function stopDetection() {
    if (childProcess) {
        childProcess.kill();
        childProcess = null;
    }
    if (statusBarItem) {
        statusBarItem.text = "$(feedback) Air Gesture: Off";
        statusBarItem.backgroundColor = undefined;
        statusBarItem.command = 'air-gesture.start';
    }
    vscode.window.showInformationMessage('Air Gesture Detection Stopped.');
}

function handleGesture(message) {
    if (!message || !message.gesture) return;

    console.log(`Received Gesture: ${message.gesture}`);

    if (message.gesture === 'swipe_left') {
        vscode.window.setStatusBarMessage('Gesture: Swipe Left', 1000);
        vscode.commands.executeCommand('workbench.action.previousEditor');
    } else if (message.gesture === 'swipe_right') {
        vscode.window.setStatusBarMessage('Gesture: Swipe Right', 1000);
        vscode.commands.executeCommand('workbench.action.nextEditor');
    } else if (message.gesture === 'fist') {
        vscode.window.setStatusBarMessage('Gesture: Save', 2000);
        vscode.commands.executeCommand('workbench.action.files.save');
    } else if (message.gesture === 'palm') {
        vscode.window.setStatusBarMessage('Gesture Active: Palm', 2000);
    }
}

function deactivate() {
    stopDetection();
}

module.exports = {
    activate,
    deactivate
};
