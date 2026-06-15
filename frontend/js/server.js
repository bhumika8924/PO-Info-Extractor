const http = require("http");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const root = __dirname;
const projectRoot = path.resolve(root, "..", "..");
const preferredPort = Number(process.env.PORT || 8080);
const apiHost = "127.0.0.1";
const apiPort = 5000;
let backendProcess = null;

const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".pdf": "application/pdf"
};

function safePath(urlPath) {
  const decoded = decodeURIComponent(urlPath.split("?")[0]);
  const requested = decoded === "/" ? "/index.html" : decoded;
  const resolved = path.resolve(root, `.${requested}`);
  return resolved.startsWith(root) ? resolved : null;
}

function createStaticServer() {
  return http.createServer((req, res) => {
    const filePath = safePath(req.url || "/");

    if (!filePath) {
      res.writeHead(403);
      res.end("Forbidden");
      return;
    }

    fs.readFile(filePath, (error, content) => {
      if (error) {
        res.writeHead(error.code === "ENOENT" ? 404 : 500);
        res.end(error.code === "ENOENT" ? "Not found" : "Server error");
        return;
      }

      const ext = path.extname(filePath).toLowerCase();
      res.writeHead(200, {
        "Content-Type": contentTypes[ext] || "application/octet-stream"
      });
      res.end(content);
    });
  });
}

function getApiStatus() {
  return new Promise((resolve) => {
    const req = http.get(
      {
        host: apiHost,
        port: apiPort,
        path: "/health",
        timeout: 1200
      },
      (res) => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => {
          body += chunk;
        });
        res.on("end", () => {
          let payload = {};
          try {
            payload = JSON.parse(body);
          } catch {
            payload = {};
          }

          resolve({
            healthy: res.statusCode >= 200 && res.statusCode < 500,
            current: Number(payload.frontend_contract_version || 0) >= 2
          });
        });
      }
    );

    req.on("error", () => resolve({ healthy: false, current: false }));
    req.on("timeout", () => {
      req.destroy();
      resolve({ healthy: false, current: false });
    });
  });
}

function startBackend() {
  const venvPython = path.join(projectRoot, ".venv", "Scripts", "python.exe");
  const pythonCommand = fs.existsSync(venvPython) ? venvPython : "python";
  backendProcess = spawn(pythonCommand, ["flask_api.py"], {
    cwd: projectRoot,
    stdio: "inherit",
    shell: false
  });

  backendProcess.on("exit", (code) => {
    backendProcess = null;
    if (code !== 0 && code !== null) {
      console.error(`Flask API stopped with exit code ${code}.`);
    }
  });
}

async function ensureBackend() {
  const apiStatus = await getApiStatus();
  if (apiStatus.healthy && apiStatus.current) {
    console.log(`Flask API already running at http://${apiHost}:${apiPort}/`);
    return;
  }

  if (apiStatus.healthy && !apiStatus.current) {
    console.warn("Port 5000 is running an older Flask API. Stop that process, then run npm run dev again.");
    return;
  }

  console.log("Starting Flask API at http://127.0.0.1:5000/ ...");
  startBackend();

  for (let attempt = 0; attempt < 20; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    const status = await getApiStatus();
    if (status.healthy && status.current) {
      console.log("Flask API is ready.");
      return;
    }
  }

  console.warn("Frontend started, but Flask API did not become ready yet.");
}

function listen(port, attemptsLeft = 20) {
  const server = createStaticServer();

  server.on("error", (error) => {
    if (error.code === "EADDRINUSE" && attemptsLeft > 0) {
      listen(port + 1, attemptsLeft - 1);
      return;
    }

    console.error(`Unable to start frontend server: ${error.message}`);
    process.exit(1);
  });

  server.listen(port, "127.0.0.1", () => {
    console.log(`Frontend running at http://127.0.0.1:${port}/`);
  });
}

function shutdown() {
  if (backendProcess) {
    backendProcess.kill();
  }
  process.exit();
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

ensureBackend().then(() => listen(preferredPort));
