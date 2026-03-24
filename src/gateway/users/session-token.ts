import { createHash, randomBytes } from "node:crypto";

/** Generate a cryptographically random session token (64 hex chars). */
export function generateSessionToken(): string {
  return randomBytes(32).toString("hex");
}

/** Hash a session token with SHA-256 for server-side storage. */
export function hashSessionToken(token: string): string {
  return createHash("sha256").update(token).digest("hex");
}
