import type { CorpusDoc } from "./types.ts";

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function formatMs(ms: number): string {
  if (!Number.isFinite(ms)) return "-";
  if (ms < 1) return `${(ms * 1000).toFixed(0)} µs`;
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 2 : 1)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[index];
}

export async function sha256Hex(text: string): Promise<string> {
  const data = new TextEncoder().encode(text);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(hash)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function chunkText(text: string, maxChars = 900): string[] {
  const normalized = text.replace(/\r\n/g, "\n").trim();
  if (!normalized) return [];
  const paragraphs = normalized
    .split(/\n{2,}/)
    .map((part) => part.trim())
    .filter(Boolean);
  const chunks: string[] = [];
  let current = "";
  const flush = () => {
    if (current.trim()) chunks.push(current.trim());
    current = "";
  };
  for (const paragraph of paragraphs) {
    if (paragraph.length <= maxChars) {
      if (current.length + paragraph.length + 2 > maxChars) flush();
      current = current ? `${current}\n\n${paragraph}` : paragraph;
      continue;
    }
    flush();
    const sentences = paragraph.split(/(?<=[。！？!?；;])|\n/).filter(Boolean);
    for (const sentence of sentences) {
      if (sentence.length > maxChars) {
        for (let start = 0; start < sentence.length; start += maxChars) {
          chunks.push(sentence.slice(start, start + maxChars));
        }
      } else {
        if (current.length + sentence.length > maxChars) flush();
        current += sentence;
      }
    }
  }
  flush();
  return chunks.filter((chunk) => chunk.length >= 8);
}

export function parsePastedText(text: string): Array<{ title: string; text: string; source: string }> {
  const normalized = text.replace(/\r\n/g, "\n").trim();
  if (!normalized) return [];
  let sections: string[];
  if (/^#{1,4}\s+/m.test(normalized)) {
    sections = normalized
      .split(/^#{1,4}\s+/m)
      .map((section) => section.trim())
      .filter(Boolean);
  } else {
    sections = normalized.split(/\n{2,}/).map((section) => section.trim()).filter(Boolean);
  }
  const docs: Array<{ title: string; text: string; source: string }> = [];
  sections.forEach((section, index) => {
    const lines = section.split("\n");
    const firstLine = lines[0]?.trim() ?? "";
    const hasTitle = lines.length > 1 && firstLine.length <= 60;
    const title = hasTitle ? firstLine.replace(/^#+\s*/, "") : `粘贴文本 ${index + 1}`;
    const body = hasTitle ? lines.slice(1).join("\n").trim() : section;
    if (!body) return;
    docs.push({ title, text: body, source: "paste" });
  });
  return docs;
}

export async function parseUploadedFiles(
  files: FileList | File[]
): Promise<Array<{ title: string; text: string; source: string }>> {
  const out: Array<{ title: string; text: string; source: string }> = [];
  for (const file of Array.from(files)) {
    const text = await file.text();
    if (/\.json$/i.test(file.name)) {
      try {
        const parsed = JSON.parse(text);
        const list = Array.isArray(parsed) ? parsed : parsed.docs ?? parsed.corpus ?? [];
        for (const item of list) {
          if (item && typeof item.text === "string") {
            out.push({
              title: String(item.title ?? item.id ?? file.name),
              text: item.text,
              source: file.name,
            });
          }
        }
        continue;
      } catch {
        // fall through as plain text
      }
    }
    out.push({ title: file.name.replace(/\.[^.]+$/, ""), text, source: file.name });
  }
  return out;
}

export function corpusToSources(corpus: CorpusDoc[]): Array<{
  id: string;
  title: string;
  text: string;
  source: string;
}> {
  return corpus.map((doc) => ({
    id: doc.id,
    title: doc.title ?? doc.id,
    text: doc.text,
    source: doc.source ?? "demo",
  }));
}

export function highlight(text: string, query: string): string {
  const safe = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  const tokens = query
    .split(/[\s,，。;；、]+/)
    .map((token) => token.trim())
    .filter((token) => token.length >= 2)
    .slice(0, 8);
  if (tokens.length === 0) return safe;
  const pattern = new RegExp(`(${tokens.map((token) => token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "g");
  return safe.replace(pattern, "<mark>$1</mark>");
}

export function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
