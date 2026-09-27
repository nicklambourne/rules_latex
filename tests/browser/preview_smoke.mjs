// Exercise the real PDF.js client in headless Chrome. No npm packages:
// Node's WebSocket speaks the small CDP subset needed for this smoke test.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const [url, chromePath, sourcePath] = process.argv.slice(2);
if (!url || !chromePath || !sourcePath) {
  throw new Error("usage: preview_smoke.mjs <preview-url> <chrome> <source.tex>");
}

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(check, label, timeoutMs = 45000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await pause(250);
  }
  throw new Error(`timed out waiting for ${label}`);
}

class Cdp {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.errors = [];
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(message.error.message));
        else pending.resolve(message.result);
      } else if (message.method === "Runtime.exceptionThrown") {
        this.errors.push(message.params.exceptionDetails.text);
      } else if (message.method === "Runtime.consoleAPICalled" &&
                 message.params.type === "error") {
        this.errors.push(message.params.args.map((arg) =>
          arg.value ?? arg.description ?? "console error"
        ).join(" "));
      } else if (message.method === "Log.entryAdded" &&
                 message.params.entry.level === "error") {
        this.errors.push(message.params.entry.text);
      }
    });
  }

  async ready() {
    if (this.socket.readyState === WebSocket.OPEN) return;
    await new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression, returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text);
    }
    return result.result.value;
  }

  close() {
    this.socket.close();
  }
}

const profile = await mkdtemp(path.join(os.tmpdir(), "rules-latex-chrome-"));
const original = await readFile(sourcePath, "utf8");
const chrome = spawn(chromePath, [
  "--headless=new", "--no-sandbox", "--disable-gpu",
  "--disable-dev-shm-usage", "--no-first-run",
  "--remote-debugging-port=0", `--user-data-dir=${profile}`,
  "about:blank",
], { stdio: "ignore" });
let cdp;
try {
  const activePort = await waitFor(async () => {
    try {
      return await readFile(path.join(profile, "DevToolsActivePort"), "utf8");
    } catch {
      if (chrome.exitCode !== null) throw new Error("Chrome exited before CDP started");
      return null;
    }
  }, "Chrome DevTools port", 15000);
  const port = activePort.split("\n")[0];
  const target = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, {
    method: "PUT",
  }).then((response) => response.json());
  cdp = new Cdp(target.webSocketDebuggerUrl);
  await cdp.ready();
  await Promise.all([
    cdp.send("Runtime.enable"), cdp.send("Log.enable"), cdp.send("Page.enable"),
  ]);
  await cdp.send("Page.navigate", { url });

  const state = () => cdp.evaluate(`(() => ({
    rendered: [...document.querySelectorAll("#viewer .page-wrap")]
      .some((page) => page.dataset.rendered === "1" &&
        page.querySelector("canvas")?.width > 0),
    renders: window.__serveWebRenderStats?.count || 0,
    generations: window.__serveWebRenderStats?.generations || 0,
    status: document.querySelector("#status")?.className || "",
  }))()`);
  const first = await waitFor(async () => {
    const current = await state();
    return current?.rendered && current.status === "ok" ? current : null;
  }, "initial PDF.js canvas render");

  const before = await fetch(`${url}status`).then((response) => response.json());
  assert.equal(before.last_success, true);
  assert.match(original, /\\end\{document\}/);
  const revised = original.replace(
    /\\end\{document\}/,
    "\\par Browser smoke revision.\n\\end{document}",
  );
  await writeFile(sourcePath, revised);
  const second = await waitFor(async () => {
    const status = await fetch(`${url}status`).then((response) => response.json());
    const current = await state();
    return status.last_success && status.build_count > before.build_count &&
      current?.rendered && current.renders > first.renders ? current : null;
  }, "PDF.js render after a source edit", 60000);

  assert.deepEqual(cdp.errors, [], `browser errors: ${cdp.errors.join("; ")}`);
  console.log("PDF.js browser smoke passed", { first, second });
} finally {
  await writeFile(sourcePath, original);
  cdp?.close();
  if (chrome.exitCode === null && chrome.signalCode === null) {
    const exited = new Promise((resolve) => chrome.once("exit", resolve));
    chrome.kill("SIGTERM");
    await Promise.race([exited, pause(5000)]);
    if (chrome.exitCode === null && chrome.signalCode === null) {
      chrome.kill("SIGKILL");
      await exited;
    }
  }
  await rm(profile, {
    recursive: true, force: true, maxRetries: 20, retryDelay: 100,
  });
}
