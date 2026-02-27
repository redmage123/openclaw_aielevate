import { describe, expect, it } from "vitest";
import { hashPassword, verifyPassword } from "./password.js";

describe("hashPassword", () => {
  it("returns salt:derived hex format", () => {
    const hash = hashPassword("test-password");
    expect(hash).toContain(":");
    const [salt, derived] = hash.split(":");
    // Salt is 32 bytes = 64 hex chars
    expect(salt).toHaveLength(64);
    // Derived key is 64 bytes = 128 hex chars
    expect(derived).toHaveLength(128);
  });

  it("produces different hashes for the same password (random salt)", () => {
    const hash1 = hashPassword("identical-password");
    const hash2 = hashPassword("identical-password");
    expect(hash1).not.toBe(hash2);
  });

  it("produces valid hex characters only", () => {
    const hash = hashPassword("check-hex");
    expect(hash).toMatch(/^[0-9a-f]+:[0-9a-f]+$/);
  });
});

describe("verifyPassword", () => {
  it("returns true for correct password", () => {
    const hash = hashPassword("my-secret");
    expect(verifyPassword("my-secret", hash)).toBe(true);
  });

  it("returns false for wrong password", () => {
    const hash = hashPassword("my-secret");
    expect(verifyPassword("wrong-secret", hash)).toBe(false);
  });

  it("returns false for empty password against valid hash", () => {
    const hash = hashPassword("real-password");
    expect(verifyPassword("", hash)).toBe(false);
  });

  it("returns false for malformed hash (no colon)", () => {
    expect(verifyPassword("test", "no-colon-here")).toBe(false);
  });

  it("returns false for malformed hash (wrong salt length)", () => {
    // Salt too short (only 4 hex chars instead of 64)
    expect(verifyPassword("test", "abcd:" + "a".repeat(128))).toBe(false);
  });

  it("returns false for malformed hash (wrong derived length)", () => {
    expect(verifyPassword("test", "a".repeat(64) + ":abcd")).toBe(false);
  });

  it("handles unicode passwords", () => {
    const hash = hashPassword("p@$$w0rd-🔑");
    expect(verifyPassword("p@$$w0rd-🔑", hash)).toBe(true);
    expect(verifyPassword("p@$$w0rd", hash)).toBe(false);
  });
});
