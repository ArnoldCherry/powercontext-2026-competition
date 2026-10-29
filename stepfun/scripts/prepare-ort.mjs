import fs from "node:fs";
import path from "node:path";

const source = path.resolve("node_modules/onnxruntime-web/dist");
const dest = path.resolve("public/ort");
const files = [
  "ort-wasm-simd-threaded.jsep.mjs",
  "ort-wasm-simd-threaded.jsep.wasm",
  "ort-wasm-simd-threaded.mjs",
  "ort-wasm-simd-threaded.wasm",
];

fs.mkdirSync(dest, { recursive: true });
let total = 0;
for (const file of files) {
  const from = path.join(source, file);
  const to = path.join(dest, file);
  if (!fs.existsSync(from)) {
    console.error(`missing ${from}; run npm install first`);
    process.exit(1);
  }
  fs.copyFileSync(from, to);
  const size = fs.statSync(to).size;
  total += size;
  console.log(`ort: ${file} ${(size / 1024 / 1024).toFixed(1)} MiB`);
}
console.log(`ort runtime vendored to public/ort (${(total / 1024 / 1024).toFixed(1)} MiB total)`);
