import { spawn } from "node:child_process";

const node = process.execPath;
const env = { ...process.env, PORT: process.env.PORT || "8788" };
const children = [];

function run(name, args, extraEnv = {}) {
  const child = spawn(node, args, {
    env: { ...env, ...extraEnv },
    stdio: "inherit",
    windowsHide: false,
  });
  child.on("exit", (code) => {
    console.log(`[${name}] exited with ${code}`);
    for (const other of children) {
      if (other !== child && other.exitCode === null) other.kill("SIGTERM");
    }
    if (code && code !== 0) process.exitCode = code;
  });
  children.push(child);
  return child;
}

run("server", ["node_modules/tsx/dist/cli.mjs", "watch", "server/index.ts"]);
run("vite", ["node_modules/vite/bin/vite.js", "--port", "5174", "--strictPort"]);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    for (const child of children) child.kill(signal);
    process.exit(0);
  });
}
