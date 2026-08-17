import { createServer } from "node:http";
import { createReadStream, existsSync, statSync } from "node:fs";
import { dirname, extname, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)));
const port = Number(process.env.FRONTEND_PORT || 3000);
const apiOrigin = String(process.env.RAG_API_ORIGIN || "http://127.0.0.1:8001").replace(/\/$/, "");

const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
};

function sendJson(response, status, payload) {
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  response.end(JSON.stringify(payload));
}

function safeStaticPath(requestPath) {
  const pathname = decodeURIComponent(requestPath.split("?")[0]);
  const requested = pathname === "/" ? "/index.html" : pathname;
  const candidate = resolve(frontendRoot, `.${normalize(requested)}`);
  return candidate === frontendRoot || candidate.startsWith(`${frontendRoot}/`) || candidate.startsWith(`${frontendRoot}\\`)
    ? candidate
    : null;
}

async function proxyApi(request, response) {
  const target = `${apiOrigin}${request.url}`;
  const headers = { Accept: "application/json" };
  if (request.headers["content-type"]) headers["Content-Type"] = request.headers["content-type"];

  let body;
  if (request.method === "POST") {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    body = Buffer.concat(chunks);
  }

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
    });
    const payload = Buffer.from(await upstream.arrayBuffer());
    response.writeHead(upstream.status, {
      "Content-Type": upstream.headers.get("content-type") || "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    });
    response.end(payload);
  } catch {
    sendJson(response, 502, { detail: "无法连接本地 RAG 服务" });
  }
}

const server = createServer(async (request, response) => {
  const requestPath = request.url || "/";
  let pathname;
  try {
    pathname = new URL(requestPath, "http://127.0.0.1").pathname;
  } catch {
    sendJson(response, 400, { detail: "Invalid URL" });
    return;
  }
  if (pathname === "/health" || pathname === "/api/rag/query") {
    if (pathname === "/health" && request.method !== "GET") {
      sendJson(response, 405, { detail: "Method Not Allowed" });
      return;
    }
    if (pathname === "/api/rag/query" && request.method !== "POST") {
      sendJson(response, 405, { detail: "Method Not Allowed" });
      return;
    }
    await proxyApi(request, response);
    return;
  }

  if (request.method !== "GET" && request.method !== "HEAD") {
    sendJson(response, 405, { detail: "Method Not Allowed" });
    return;
  }

  let filePath;
  try {
    filePath = safeStaticPath(requestPath);
  } catch {
    sendJson(response, 400, { detail: "Invalid path" });
    return;
  }
  if (!filePath || !existsSync(filePath) || !statSync(filePath).isFile()) {
    sendJson(response, 404, { detail: "Not Found" });
    return;
  }

  response.writeHead(200, {
    "Content-Type": contentTypes[extname(filePath)] || "application/octet-stream",
    "Cache-Control": "no-store",
  });
  if (request.method === "HEAD") {
    response.end();
    return;
  }
  createReadStream(filePath).pipe(response);
});

server.listen(port, "127.0.0.1", () => {
  console.log(`RAG frontend listening on http://127.0.0.1:${port}`);
  console.log(`RAG API proxy: ${apiOrigin}`);
});

function shutdown() {
  server.close(() => process.exit(0));
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
