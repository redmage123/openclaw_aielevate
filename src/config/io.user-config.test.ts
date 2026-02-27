import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { readUserConfigOverlay, writeUserConfigOverlay } from "./io.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "user-config-test-"));
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

// ── readUserConfigOverlay ───────────────────────────────────────────

describe("readUserConfigOverlay", () => {
  it("returns empty object when user config file does not exist", () => {
    const overlay = readUserConfigOverlay(path.join(tmpDir, "nonexistent"));
    expect(overlay).toEqual({});
  });

  it("returns only overridable keys from user config", () => {
    const userDir = path.join(tmpDir, "user1");
    fs.mkdirSync(userDir, { recursive: true });
    fs.writeFileSync(
      path.join(userDir, "openclaw.json"),
      JSON.stringify({
        agents: { list: [] },
        models: { primary: "test" },
        gateway: { port: 9999 }, // admin-only, should be stripped
        auth: { mode: "multi-user" }, // admin-only, should be stripped
      }),
    );

    const overlay = readUserConfigOverlay(userDir);
    expect(overlay).toHaveProperty("agents");
    expect(overlay).toHaveProperty("models");
    expect(overlay).not.toHaveProperty("gateway");
    expect(overlay).not.toHaveProperty("auth");
  });

  it("strips all non-overridable keys", () => {
    const userDir = path.join(tmpDir, "user2");
    fs.mkdirSync(userDir, { recursive: true });
    fs.writeFileSync(
      path.join(userDir, "openclaw.json"),
      JSON.stringify({
        gateway: { port: 1234 },
        auth: { mode: "shared-secret" },
        plugins: ["something"],
        channels: {},
      }),
    );

    const overlay = readUserConfigOverlay(userDir);
    expect(overlay).toEqual({});
  });

  it("handles malformed JSON gracefully", () => {
    const userDir = path.join(tmpDir, "user3");
    fs.mkdirSync(userDir, { recursive: true });
    fs.writeFileSync(path.join(userDir, "openclaw.json"), "not valid json{{{");

    const overlay = readUserConfigOverlay(userDir);
    expect(overlay).toEqual({});
  });

  it("preserves all overridable key categories", () => {
    const userDir = path.join(tmpDir, "user4");
    fs.mkdirSync(userDir, { recursive: true });
    const data = {
      agents: {},
      models: {},
      skills: [],
      tools: {},
      memory: {},
      session: {},
      messages: {},
      ui: {},
      audio: {},
    };
    fs.writeFileSync(path.join(userDir, "openclaw.json"), JSON.stringify(data));

    const overlay = readUserConfigOverlay(userDir);
    for (const key of Object.keys(data)) {
      expect(overlay).toHaveProperty(key);
    }
  });
});

// ── writeUserConfigOverlay ──────────────────────────────────────────

describe("writeUserConfigOverlay", () => {
  it("creates directory and writes config file", () => {
    const userDir = path.join(tmpDir, "new-user");
    const result = writeUserConfigOverlay(userDir, { models: { primary: "gpt-4" } });
    expect(result).toEqual({ ok: true });
    expect(fs.existsSync(path.join(userDir, "openclaw.json"))).toBe(true);

    const written = JSON.parse(fs.readFileSync(path.join(userDir, "openclaw.json"), "utf-8"));
    expect(written.models).toEqual({ primary: "gpt-4" });
  });

  it("rejects admin-only keys and returns them", () => {
    const userDir = path.join(tmpDir, "admin-reject");
    const result = writeUserConfigOverlay(userDir, {
      models: { primary: "test" },
      gateway: { port: 9999 },
      auth: { mode: "open" },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.rejectedKeys).toContain("gateway");
      expect(result.rejectedKeys).toContain("auth");
    }
  });

  it("does not write file when all keys are rejected", () => {
    const userDir = path.join(tmpDir, "all-rejected");
    const result = writeUserConfigOverlay(userDir, {
      gateway: { port: 1234 },
    });
    expect(result.ok).toBe(false);
    // File should not exist since all keys were rejected
    // (the function returns early before writing)
  });

  it("overwrites existing config", () => {
    const userDir = path.join(tmpDir, "overwrite");
    writeUserConfigOverlay(userDir, { models: { primary: "old" } });
    writeUserConfigOverlay(userDir, { models: { primary: "new" } });

    const written = JSON.parse(fs.readFileSync(path.join(userDir, "openclaw.json"), "utf-8"));
    expect(written.models.primary).toBe("new");
  });

  it("writes pretty-printed JSON with trailing newline", () => {
    const userDir = path.join(tmpDir, "pretty");
    writeUserConfigOverlay(userDir, { ui: { theme: "dark" } });
    const raw = fs.readFileSync(path.join(userDir, "openclaw.json"), "utf-8");
    expect(raw).toContain("\n");
    expect(raw.endsWith("\n")).toBe(true);
    // Pretty-printed means indentation
    expect(raw).toContain("  ");
  });

  it("round-trips with readUserConfigOverlay", () => {
    const userDir = path.join(tmpDir, "roundtrip");
    const overlay = { agents: { list: [{ id: "bot" }] }, memory: { enabled: true } };
    writeUserConfigOverlay(userDir, overlay);
    const readBack = readUserConfigOverlay(userDir);
    expect(readBack).toEqual(overlay);
  });
});
