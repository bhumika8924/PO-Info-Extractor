const http = require("http"); //creates a local http server
const { spawn } = require("child_process"); // starts the python backend process
const fs = require("fs"); // reads static files from the frontend directory 
const path = require("path"); // resolves file paths and ensures safe access to static files
const {
  BACKEND_API_URLS,
  DEFAULT_BACKEND,        
  getBackendCandidates,
  getConfiguredApiKey,
  getConfiguredBackendUrl
} = require("./backendApiConfig");

const root = __dirname; // path to the frontend directory
const projectRoot = path.resolve(root, ".."); // path to the project root directory
const preferredPort = Number(process.env.PORT || 8080); // preferred port for the frontend server, can be overridden by the PORT environment variable
const apiCandidates = getBackendCandidates();
const localApiBase = BACKEND_API_URLS[DEFAULT_BACKEND];
const apiKey = getConfiguredApiKey();
let activeApiBase = getConfiguredBackendUrl() || localApiBase;
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

function safePath(urlPath) { // ensures that the requested file path is within the frontend directory
  const decoded = decodeURIComponent(urlPath.split("?")[0]); // decode the URL path and remove any query parameters
  const requested = decoded === "/" ? "/index.html" : decoded; // if the requested path is "/", serve "index.html"
  const resolved = path.resolve(root, `.${requested}`); // resolve the requested path to an absolute path
  const relative = path.relative(root, resolved); // get the relative path from the frontend directory to the resolved path
  return relative && !relative.startsWith("..") && !path.isAbsolute(relative) ? resolved : null; 
}

function createStaticServer() { // creates a local HTTP server that serves static files from the frontend directory
  return http.createServer((req, res) => { // handle incoming requests
    if ((req.url || "").split("?")[0] === "/frontend-config.js") { 
      res.writeHead(200, {
        "Content-Type": "text/javascript; charset=utf-8",
        "Cache-Control": "no-store"
      });
      res.end(`window.PO_EXTRACTOR_CONFIG = ${JSON.stringify({ API_BASE: activeApiBase, API_KEY: apiKey })};`);
      return;
    }

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

function getApiStatus(apiBase) {
  return new Promise((resolve) => {
    const healthUrl = new URL("/health", apiBase);
    const client = healthUrl.protocol === "https:" ? require("https") : http;
    const req = client.request(
      healthUrl,
      {
        method: "GET",
        headers: apiKey ? { "X-API-Key": apiKey } : {}
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
            apiBase,
            healthy: res.statusCode >= 200 && res.statusCode < 500,
            current: Number(payload.frontend_contract_version || 0) >= 2
          });
        });
      }
    );
    req.end();

    req.setTimeout(1200);
    req.on("error", () => resolve({ apiBase, healthy: false, current: false }));
    req.on("timeout", () => {
      req.destroy();
      resolve({ apiBase, healthy: false, current: false });
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
  for (const candidate of apiCandidates) {
    const apiStatus = await getApiStatus(candidate);
    if (apiStatus.healthy && apiStatus.current) {
      activeApiBase = candidate;
      console.log(`Flask API connected at ${activeApiBase}/`);
      return;
    }

    if (apiStatus.healthy && !apiStatus.current) {
      console.warn(`${candidate}/ is running an older Flask API contract. Trying the next backend option...`);
    }
  }

  activeApiBase = localApiBase;
  console.log(`Starting Flask API at ${localApiBase}/ ...`);
  startBackend();

  for (let attempt = 0; attempt < 20; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    const status = await getApiStatus(localApiBase);
    if (status.healthy && status.current) {
      activeApiBase = localApiBase;
      console.log(`Flask API is ready at ${activeApiBase}/`);
      return;
    }
  }

  console.warn("Frontend started, but no Flask API backend became ready yet.");
}

function listen(port, attemptsLeft = 20) { // attempts to start the frontend server on the specified port, retrying with incremented ports if the preferred port is in use
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

function shutdown() { // gracefully shuts down the frontend server and the backend process when the process receives a termination signal
  if (backendProcess) {
    backendProcess.kill();
  }
  process.exit();
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

ensureBackend().then(() => listen(preferredPort)); // start the frontend server after ensuring the backend is ready
