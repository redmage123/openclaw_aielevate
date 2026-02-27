import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { UserDb } from "./user-db.js";

let tmpDir: string;
let db: UserDb;

function freshDb(opts?: { adminEmails?: string[]; maxSessionsPerUser?: number }) {
  const dbPath = path.join(tmpDir, `user-${Date.now()}-${Math.random().toString(36).slice(2)}.db`);
  return new UserDb({
    dbPath,
    sessionLifetimeHours: 1,
    ...opts,
  });
}

const ALICE = {
  username: "alice",
  email: "alice@example.com",
  password: "password123",
  displayName: "Alice Smith",
};

const BOB = {
  username: "bob",
  email: "bob@example.com",
  password: "bobsecure99",
  displayName: "Bob Jones",
};

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "userdb-test-"));
  db = freshDb();
});

afterEach(() => {
  try {
    db.close();
  } catch {
    // already closed
  }
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

// ── createUser ──────────────────────────────────────────────────────

describe("createUser", () => {
  it("first user gets admin role", () => {
    const user = db.createUser(ALICE);
    expect(user.role).toBe("admin");
    expect(user.username).toBe("alice");
    expect(user.email).toBe("alice@example.com");
    expect(user.displayName).toBe("Alice Smith");
    expect(user.id).toBeTruthy();
  });

  it("second user gets user role", () => {
    db.createUser(ALICE);
    const bob = db.createUser(BOB);
    expect(bob.role).toBe("user");
  });

  it("adminEmails list promotes matching users to admin", () => {
    const adminDb = freshDb({ adminEmails: ["bob@example.com"] });
    adminDb.createUser(ALICE); // first user = admin
    const bob = adminDb.createUser(BOB); // email match
    expect(bob.role).toBe("admin");
    adminDb.close();
  });

  it("adminEmails matching is case-insensitive", () => {
    const adminDb = freshDb({ adminEmails: ["BOB@EXAMPLE.COM"] });
    adminDb.createUser(ALICE);
    const bob = adminDb.createUser(BOB);
    expect(bob.role).toBe("admin");
    adminDb.close();
  });

  it("assigns UUID as user id", () => {
    const user = db.createUser(ALICE);
    expect(user.id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
  });

  it("throws on duplicate username", () => {
    db.createUser(ALICE);
    expect(() => db.createUser({ ...BOB, username: "alice" })).toThrow();
  });

  it("throws on duplicate email", () => {
    db.createUser(ALICE);
    expect(() => db.createUser({ ...BOB, email: "alice@example.com" })).toThrow();
  });
});

// ── find methods ────────────────────────────────────────────────────

describe("find methods", () => {
  it("findByEmail: case-insensitive match", () => {
    db.createUser(ALICE);
    const found = db.findByEmail("ALICE@EXAMPLE.COM");
    expect(found).not.toBeNull();
    expect(found!.username).toBe("alice");
  });

  it("findByEmail: returns null for missing user", () => {
    expect(db.findByEmail("nobody@test.com")).toBeNull();
  });

  it("findByUsername: exact match", () => {
    db.createUser(ALICE);
    const found = db.findByUsername("alice");
    expect(found).not.toBeNull();
    expect(found!.email).toBe("alice@example.com");
  });

  it("findByUsername: returns null for missing user", () => {
    expect(db.findByUsername("nobody")).toBeNull();
  });

  it("findByUsernameOrEmail: finds by email when contains @", () => {
    db.createUser(ALICE);
    const found = db.findByUsernameOrEmail("alice@example.com");
    expect(found).not.toBeNull();
    expect(found!.username).toBe("alice");
  });

  it("findByUsernameOrEmail: finds by username when no @", () => {
    db.createUser(ALICE);
    const found = db.findByUsernameOrEmail("alice");
    expect(found).not.toBeNull();
  });

  it("findById: returns full user", () => {
    const created = db.createUser(ALICE);
    const found = db.findById(created.id);
    expect(found).not.toBeNull();
    expect(found!.id).toBe(created.id);
    expect(found!.passwordHash).toBeTruthy();
  });

  it("findById: returns null for missing id", () => {
    expect(db.findById("00000000-0000-0000-0000-000000000000")).toBeNull();
  });
});

// ── authenticateUser ────────────────────────────────────────────────

describe("authenticateUser", () => {
  it("returns AuthUser with correct password", () => {
    db.createUser(ALICE);
    const result = db.authenticateUser("alice", "password123");
    expect(result).not.toBeNull();
    expect(result!.username).toBe("alice");
    // AuthUser should not contain passwordHash
    expect(result).not.toHaveProperty("passwordHash");
  });

  it("returns null for wrong password", () => {
    db.createUser(ALICE);
    expect(db.authenticateUser("alice", "wrong")).toBeNull();
  });

  it("authenticates by email", () => {
    db.createUser(ALICE);
    const result = db.authenticateUser("alice@example.com", "password123");
    expect(result).not.toBeNull();
  });

  it("returns null for non-existent user", () => {
    expect(db.authenticateUser("ghost", "pass")).toBeNull();
  });
});

// ── sessions ────────────────────────────────────────────────────────

describe("sessions", () => {
  it("createSession returns token and expiresAt", () => {
    const user = db.createUser(ALICE);
    const session = db.createSession(user.id);
    expect(session.token).toBeTruthy();
    expect(typeof session.token).toBe("string");
    expect(session.expiresAt).toBeGreaterThan(Date.now());
  });

  it("validateSession returns user for valid token", () => {
    const user = db.createUser(ALICE);
    const session = db.createSession(user.id);
    const validated = db.validateSession(session.token);
    expect(validated).not.toBeNull();
    expect(validated!.id).toBe(user.id);
    expect(validated!.username).toBe("alice");
  });

  it("validateSession returns null for invalid token", () => {
    expect(db.validateSession("bogus-token")).toBeNull();
  });

  it("revokeSession makes token invalid", () => {
    const user = db.createUser(ALICE);
    const session = db.createSession(user.id);
    expect(db.revokeSession(session.token)).toBe(true);
    expect(db.validateSession(session.token)).toBeNull();
  });

  it("revokeSession returns false for already-revoked token", () => {
    const user = db.createUser(ALICE);
    const session = db.createSession(user.id);
    db.revokeSession(session.token);
    expect(db.revokeSession(session.token)).toBe(false);
  });

  it("enforceMaxSessions revokes oldest when limit reached", () => {
    const limitDb = freshDb({ maxSessionsPerUser: 2 });
    const user = limitDb.createUser(ALICE);
    const s1 = limitDb.createSession(user.id);
    const s2 = limitDb.createSession(user.id);
    // This should evict s1
    const s3 = limitDb.createSession(user.id);
    expect(limitDb.validateSession(s1.token)).toBeNull();
    expect(limitDb.validateSession(s2.token)).not.toBeNull();
    expect(limitDb.validateSession(s3.token)).not.toBeNull();
    limitDb.close();
  });
});

// ── revokeAllUserSessions ───────────────────────────────────────────

describe("revokeAllUserSessions", () => {
  it("revokes all sessions for the given user", () => {
    const alice = db.createUser(ALICE);
    const s1 = db.createSession(alice.id);
    const s2 = db.createSession(alice.id);
    const count = db.revokeAllUserSessions(alice.id);
    expect(count).toBe(2);
    expect(db.validateSession(s1.token)).toBeNull();
    expect(db.validateSession(s2.token)).toBeNull();
  });

  it("does not affect other users sessions", () => {
    const alice = db.createUser(ALICE);
    const bob = db.createUser(BOB);
    const aliceSession = db.createSession(alice.id);
    const bobSession = db.createSession(bob.id);
    db.revokeAllUserSessions(alice.id);
    expect(db.validateSession(aliceSession.token)).toBeNull();
    expect(db.validateSession(bobSession.token)).not.toBeNull();
  });

  it("returns 0 when user has no active sessions", () => {
    const alice = db.createUser(ALICE);
    expect(db.revokeAllUserSessions(alice.id)).toBe(0);
  });
});

// ── admin methods ───────────────────────────────────────────────────

describe("listUsers", () => {
  it("returns all users ordered by creation", () => {
    db.createUser(ALICE);
    db.createUser(BOB);
    const users = db.listUsers();
    expect(users).toHaveLength(2);
    expect(users[0].username).toBe("alice");
    expect(users[1].username).toBe("bob");
  });

  it("returns empty array when no users", () => {
    expect(db.listUsers()).toHaveLength(0);
  });
});

describe("updateUserRole", () => {
  it("changes user role", () => {
    db.createUser(ALICE);
    const bob = db.createUser(BOB);
    expect(bob.role).toBe("user");
    const updated = db.updateUserRole(bob.id, "admin");
    expect(updated).toBe(true);
    const found = db.findById(bob.id);
    expect(found!.role).toBe("admin");
  });

  it("returns false for non-existent user", () => {
    expect(db.updateUserRole("00000000-0000-0000-0000-000000000000", "admin")).toBe(false);
  });
});

describe("changePassword", () => {
  it("changes password successfully", () => {
    const alice = db.createUser(ALICE);
    db.changePassword(alice.id, "new-password-456");
    // Old password should fail
    expect(db.authenticateUser("alice", "password123")).toBeNull();
    // New password should work
    expect(db.authenticateUser("alice", "new-password-456")).not.toBeNull();
  });

  it("returns false for non-existent user", () => {
    expect(db.changePassword("00000000-0000-0000-0000-000000000000", "x")).toBe(false);
  });
});

describe("updateProfile", () => {
  it("updates displayName", () => {
    const alice = db.createUser(ALICE);
    db.updateProfile(alice.id, { displayName: "Alice Wonderland" });
    const found = db.findById(alice.id);
    expect(found!.displayName).toBe("Alice Wonderland");
  });

  it("updates email (lowercased)", () => {
    const alice = db.createUser(ALICE);
    db.updateProfile(alice.id, { email: "Alice@New.COM" });
    const found = db.findById(alice.id);
    expect(found!.email).toBe("alice@new.com");
  });

  it("updates both fields at once", () => {
    const alice = db.createUser(ALICE);
    db.updateProfile(alice.id, { displayName: "New Name", email: "new@test.com" });
    const found = db.findById(alice.id);
    expect(found!.displayName).toBe("New Name");
    expect(found!.email).toBe("new@test.com");
  });

  it("returns false when no fields provided", () => {
    const alice = db.createUser(ALICE);
    expect(db.updateProfile(alice.id, {})).toBe(false);
  });
});

// ── pruneExpiredSessions ────────────────────────────────────────────

describe("pruneExpiredSessions", () => {
  it("removes revoked sessions", () => {
    const alice = db.createUser(ALICE);
    const session = db.createSession(alice.id);
    db.revokeSession(session.token);
    const pruned = db.pruneExpiredSessions();
    expect(pruned).toBeGreaterThanOrEqual(1);
  });
});

// ── countUsers ──────────────────────────────────────────────────────

describe("countUsers", () => {
  it("returns 0 initially", () => {
    expect(db.countUsers()).toBe(0);
  });

  it("increments after createUser", () => {
    db.createUser(ALICE);
    expect(db.countUsers()).toBe(1);
    db.createUser(BOB);
    expect(db.countUsers()).toBe(2);
  });
});
