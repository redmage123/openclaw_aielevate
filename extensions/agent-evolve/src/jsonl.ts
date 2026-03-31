// ---------------------------------------------------------------------------
// Agent Evolve — JSONL read/append/rotate utilities
// ---------------------------------------------------------------------------

import { appendFile, readFile, writeFile, stat as fsStat, mkdir } from "node:fs/promises";
import { dirname } from "node:path";

/** Append a single JSON record as a newline-delimited line. */
export async function appendJsonl<T>(filePath: string, record: T): Promise<void> {
  await mkdir(dirname(filePath), { recursive: true });
  const line = JSON.stringify(record) + "\n";
  await appendFile(filePath, line, "utf-8");
}

/** Read and parse all JSONL records, optionally filtering by timestamp. */
export async function readJsonl<T extends { timestamp?: number }>(
  filePath: string,
  opts?: { limit?: number; since?: number },
): Promise<T[]> {
  let raw: string;
  try {
    raw = await readFile(filePath, "utf-8");
  } catch {
    return [];
  }

  const lines = raw.split("\n").filter((l) => l.trim().length > 0);
  const records: T[] = [];

  for (const line of lines) {
    try {
      const parsed = JSON.parse(line) as T;
      if (opts?.since && parsed.timestamp && parsed.timestamp < opts.since) continue;
      records.push(parsed);
    } catch {
      // skip malformed lines
    }
  }

  if (opts?.limit && records.length > opts.limit) {
    return records.slice(-opts.limit);
  }
  return records;
}

/** Count the number of lines in a JSONL file. */
export async function countJsonlLines(filePath: string): Promise<number> {
  try {
    const raw = await readFile(filePath, "utf-8");
    return raw.split("\n").filter((l) => l.trim().length > 0).length;
  } catch {
    return 0;
  }
}

/** Rotate a JSONL file when it exceeds maxBytes, keeping the newest half. */
export async function rotateJsonl(filePath: string, maxBytes: number): Promise<void> {
  let fileStat;
  try {
    fileStat = await fsStat(filePath);
  } catch {
    return;
  }

  if (fileStat.size <= maxBytes) return;

  const raw = await readFile(filePath, "utf-8");
  const lines = raw.split("\n").filter((l) => l.trim().length > 0);
  // Keep the newest half
  const keepFrom = Math.floor(lines.length / 2);
  const kept = lines.slice(keepFrom).join("\n") + "\n";
  await writeFile(filePath, kept, "utf-8");
}
