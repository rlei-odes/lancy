#!/usr/bin/env node
// Generate a PBKDF2 hash for use as APP_PASSWORD_HASH or ADMIN_PASSWORD_HASH in frontend/.env.
// Usage:
//   node scripts/hash-password.mjs 'mypassword'
//   node scripts/hash-password.mjs   (reads from stdin, avoids shell history)

import { webcrypto as crypto } from "node:crypto";
import { createInterface } from "node:readline";

const ITERATIONS = 210_000;
const SALT_BYTES = 16;
const HASH_BYTES = 32;

function toB64Url(bytes) {
    return Buffer.from(bytes).toString("base64url");
}

async function pbkdf2(password, salt, iterations, bytes) {
    const key = await crypto.subtle.importKey(
        "raw",
        new TextEncoder().encode(password),
        "PBKDF2",
        false,
        ["deriveBits"],
    );
    const derived = await crypto.subtle.deriveBits(
        { name: "PBKDF2", salt, iterations, hash: "SHA-256" },
        key,
        bytes * 8,
    );
    return new Uint8Array(derived);
}

async function hashPassword(plaintext) {
    const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
    const hash = await pbkdf2(plaintext, salt, ITERATIONS, HASH_BYTES);
    return `pbkdf2$${ITERATIONS}$${toB64Url(salt)}$${toB64Url(hash)}`;
}

async function readStdinPassword() {
    process.stderr.write("Password: ");
    const rl = createInterface({ input: process.stdin });
    for await (const line of rl) return line;
    return "";
}

const arg = process.argv[2];
const password = arg ?? (await readStdinPassword());

if (!password) {
    process.stderr.write("Error: empty password\n");
    process.exit(1);
}

process.stdout.write((await hashPassword(password)) + "\n");
