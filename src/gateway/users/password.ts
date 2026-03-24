import { randomBytes, scryptSync, timingSafeEqual } from "node:crypto";

// OWASP scrypt defaults
const SCRYPT_N = 16384;
const SCRYPT_R = 8;
const SCRYPT_P = 1;
const KEY_LEN = 64;
const SALT_LEN = 32;

/**
 * Hash a password with scrypt using a random salt.
 * Returns `<salt_hex>:<derived_hex>`.
 */
export function hashPassword(password: string): string {
  const salt = randomBytes(SALT_LEN);
  const derived = scryptSync(password, salt, KEY_LEN, {
    N: SCRYPT_N,
    r: SCRYPT_R,
    p: SCRYPT_P,
  });
  return `${salt.toString("hex")}:${derived.toString("hex")}`;
}

/**
 * Verify a password against a stored hash (timing-safe).
 * Stored format: `<salt_hex>:<derived_hex>`.
 */
export function verifyPassword(password: string, stored: string): boolean {
  const sepIdx = stored.indexOf(":");
  if (sepIdx === -1) {
    return false;
  }
  const salt = Buffer.from(stored.slice(0, sepIdx), "hex");
  const expected = Buffer.from(stored.slice(sepIdx + 1), "hex");
  if (salt.length !== SALT_LEN || expected.length !== KEY_LEN) {
    return false;
  }
  const derived = scryptSync(password, salt, KEY_LEN, {
    N: SCRYPT_N,
    r: SCRYPT_R,
    p: SCRYPT_P,
  });
  return timingSafeEqual(derived, expected);
}
