const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const { randomBytes } = require('node:crypto');
const { validateExternalUrl } = require('./safeExternalUrl.cjs');
const { startStaticServer } = require('./staticServer.cjs');
const { spawn } = require('child_process');
const { resolveBackendPython } = require('./backendPython.cjs');

// Windows-only graphics switches; let Linux use its native Electron/driver defaults.
if (process.platform === 'win32') {
    app.commandLine.appendSwitch('use-angle', 'd3d11');
    app.commandLine.appendSwitch('enable-features', 'Vulkan');
    app.commandLine.appendSwitch('ignore-gpu-blocklist');
}

let mainWindow;
const socketToken = process.env.ADA_SOCKET_TOKEN || randomBytes(32).toString('hex');
const backendReadyNonce = randomBytes(32).toString('hex');
let pythonProcess;
let staticServerHandle = null;

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1920,
        height: 1080,
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            sandbox: true,
            preload: path.join(__dirname, 'preload.cjs'),
        },
        backgroundColor: '#000000',
        frame: false, // Frameless for custom UI
        titleBarStyle: 'hidden',
        show: false, // Don't show until ready
    });

    const isDev = !app.isPackaged && process.env.NODE_ENV !== 'production';
    const allowedOrigin = isDev ? 'http://localhost:5173' : 'http://127.0.0.1:17987';
    const isAllowedAppUrl = (candidate) => {
        try {
            const parsedUrl = new URL(candidate);
            return parsedUrl.origin === allowedOrigin;
        } catch {
            return false;
        }
    };
    const rejectUntrustedNavigation = (event, targetUrl) => {
        if (!isAllowedAppUrl(targetUrl)) event.preventDefault();
    };
    mainWindow.webContents.on('will-navigate', rejectUntrustedNavigation);
    mainWindow.webContents.on('will-redirect', rejectUntrustedNavigation);
    mainWindow.webContents.on('will-frame-navigate', (event, details) => {
        rejectUntrustedNavigation(event, details.url);
    });

    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        try {
            void shell.openExternal(validateExternalUrl(url)).catch(() => {});
        } catch {
            // Unsafe destinations remain blocked; renderer navigation is denied.
        }
        return { action: 'deny' };
    });

    const loadFrontend = async (retries = 3) => {
        try {
            if (isDev) {
                await mainWindow.loadURL('http://localhost:5173');
            } else {
                if (!staticServerHandle) {
                    const configuredPort = 17987;
                    staticServerHandle = await startStaticServer(path.join(__dirname, '../dist'), {
                        host: '127.0.0.1', port: configuredPort,
                    });
                }
                await mainWindow.loadURL(staticServerHandle.url);
            }
            console.log('Frontend loaded successfully!');
            windowWasShown = true;
            mainWindow.show();
            if (isDev) mainWindow.webContents.openDevTools();
        } catch (err) {
            console.error(`Failed to load frontend: ${err.message}`);
            if (retries > 0) {
                console.log(`Retrying in 1 second... (${retries} retries left)`);
                setTimeout(() => loadFrontend(retries - 1), 1000);
            } else {
                console.error('Failed to load frontend after all retries. Keeping window open.');
                windowWasShown = true;
                mainWindow.show();
            }
        }
    };

    void loadFrontend();

    mainWindow.on('closed', () => {
        mainWindow = null;
    });
}

function startPythonBackend() {
    return new Promise((resolve, reject) => {
        const scriptPath = path.join(__dirname, '../backend/server.py');
        const pythonExecutable = resolveBackendPython();
        const readyMarker = `ADA_BACKEND_READY:${backendReadyNonce}`;
        let outputTail = '';
        let ready = false;
        console.log(`Starting Python backend with ${pythonExecutable}: ${scriptPath}`);

        pythonProcess = spawn(pythonExecutable, [scriptPath], {
            cwd: path.join(__dirname, '../backend'),
            env: { ...process.env, ADA_SOCKET_TOKEN: socketToken, ADA_READY_NONCE: backendReadyNonce },
        });

        pythonProcess.stdout.on('data', (data) => {
            const text = data.toString();
            const output = outputTail + text;
            if (!ready && output.includes(readyMarker)) {
                ready = true;
                resolve();
            }
            outputTail = output.slice(-readyMarker.length);
            const safeText = text.replaceAll(readyMarker, '[backend-ready]')
                .replaceAll(socketToken, '[REDACTED]');
            console.log(`[Python]: ${safeText}`);
        });

        pythonProcess.stderr.on('data', (data) => {
            console.error(`[Python Error]: ${data.toString().replaceAll(socketToken, '[REDACTED]')}`);
        });
        pythonProcess.once('error', reject);
        pythonProcess.once('exit', (code) => {
            if (!ready) reject(new Error(`Python backend exited before readiness (${code}).`));
        });
    });
}

app.whenReady().then(() => {
    ipcMain.handle('ada:socket-auth', (event) => {
        if (!mainWindow || event.sender !== mainWindow.webContents ||
            event.senderFrame !== mainWindow.webContents.mainFrame) {
            throw new Error('Socket capability is available only to the main application frame.');
        }
        return { token: socketToken };
    });

    ipcMain.handle('ada:open-external', async (_event, rawUrl) => {
        const safeUrl = validateExternalUrl(rawUrl);
        await shell.openExternal(safeUrl);
        return true;
    });

    ipcMain.on('window-minimize', () => {
        if (mainWindow) mainWindow.minimize();
    });

    ipcMain.on('window-maximize', () => {
        if (mainWindow) {
            if (mainWindow.isMaximized()) {
                mainWindow.unmaximize();
            } else {
                mainWindow.maximize();
            }
        }
    });

    ipcMain.on('window-close', () => {
        if (mainWindow) mainWindow.close();
    });

    checkBackendPort(8000).then((isTaken) => {
        if (isTaken) {
            if (!process.env.ADA_SOCKET_TOKEN) {
                console.error('Port 8000 is occupied; refusing to send a new capability to an untrusted backend.');
                app.quit();
                return;
            }
            console.log('Using an existing backend with the explicitly supplied ADA_SOCKET_TOKEN.');
            waitForBackend().then(createWindow);
        } else {
            startPythonBackend()
                .then(() => waitForBackend())
                .then(createWindow)
                .catch((error) => {
                    console.error(`Backend failed before secure startup: ${error.message}`);
                    app.quit();
                });
        }
    });

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
});

function checkBackendPort(port) {
    return new Promise((resolve) => {
        const net = require('net');
        const server = net.createServer();
        server.once('error', (err) => {
            if (err.code === 'EADDRINUSE') {
                resolve(true);
            } else {
                resolve(false);
            }
        });
        server.once('listening', () => {
            server.close();
            resolve(false);
        });
        server.listen(port);
    });
}

function waitForBackend() {
    return new Promise((resolve) => {
        const check = () => {
            const http = require('http');
            http.get('http://127.0.0.1:8000/status', (res) => {
                if (res.statusCode === 200) {
                    console.log('Backend is ready!');
                    resolve();
                } else {
                    console.log('Backend not ready, retrying...');
                    setTimeout(check, 1000);
                }
            }).on('error', (err) => {
                console.log('Waiting for backend...');
                setTimeout(check, 1000);
            });
        };
        check();
    });
}

let windowWasShown = false;

app.on('window-all-closed', () => {
    // Only quit if the window was actually shown at least once
    // This prevents quitting during startup if window creation fails
    if (process.platform !== 'darwin' && windowWasShown) {
        app.quit();
    } else if (!windowWasShown) {
        console.log('Window was never shown - keeping app alive to allow retries');
    }
});

app.on('will-quit', () => {
    console.log('App closing... Killing Python backend.');
    if (staticServerHandle) {
        const serverHandle = staticServerHandle;
        staticServerHandle = null;
        serverHandle.close().catch((error) => console.error('Failed to close static asset server:', error.message));
    }
    if (pythonProcess) {
        if (process.platform === 'win32') {
            // Windows: Force kill the process tree synchronously
            try {
                const { execSync } = require('child_process');
                execSync(`taskkill /pid ${pythonProcess.pid} /f /t`);
            } catch (e) {
                console.error('Failed to kill python process:', e.message);
            }
        } else {
            // Unix: SIGKILL
            pythonProcess.kill('SIGKILL');
        }
        pythonProcess = null;
    }
});
