/**
 * Intent: conversations are a tree, and the UI has to walk it three ways.
 *
 * Messages carry `parent_id`, so a regenerated answer is a sibling rather than a
 * replacement. `getMessageThread` walks up to the root to build the visible
 * conversation, `getBrothers` finds the alternatives at one point (the "2 / 3"
 * pager under a message), and `getLastChild` follows a branch to its tip.
 *
 * All three take an id that may not exist — a message deleted while it was on
 * screen — and must return an empty result instead of throwing.
 */

import { describe, expect, it } from "vitest";

import { Message } from "@/services/message";
import { getBrothers, getLastChild, getMessageThread } from "@/lib/thread";

function msg(id: string, parent_id: string | undefined, ts: number): Message {
    return {
        id,
        content: id,
        role: id.startsWith("q") ? "user" : "assistant",
        create_timestamp: ts,
        conversation_id: "c1",
        ...(parent_id ? { parent_id } : {}),
    };
}

//  q1 ── a1 ── q2 ── a2a
//                └── a2b   (regenerated answer, sibling of a2a)
//                     └── q3 ── a3
const TREE: Message[] = [
    msg("q1", undefined, 10),
    msg("a1", "q1", 20),
    msg("q2", "a1", 30),
    msg("a2a", "q2", 40),
    msg("a2b", "q2", 50),
    msg("q3", "a2b", 60),
    msg("a3", "q3", 70),
];

const ids = (messages: Message[]) => messages.map((m) => m.id);

describe("getMessageThread", () => {
    it("walks up to the root and returns the branch oldest-first", () => {
        expect(ids(getMessageThread(TREE, "a3"))).toEqual(["q1", "a1", "q2", "a2b", "q3", "a3"]);
    });

    it("excludes the sibling branch that was not selected", () => {
        expect(ids(getMessageThread(TREE, "a3"))).not.toContain("a2a");
    });

    it("returns just the root when asked for the root", () => {
        expect(ids(getMessageThread(TREE, "q1"))).toEqual(["q1"]);
    });

    it.each([
        ["an unknown id", "does-not-exist"],
        ["no id at all", undefined],
    ])("returns an empty thread for %s", (_label, id) => {
        expect(getMessageThread(TREE, id)).toEqual([]);
    });

    it("returns an empty thread when the message list is empty", () => {
        expect(getMessageThread([], "a3")).toEqual([]);
    });
});

describe("getBrothers", () => {
    it("returns every alternative at that point, in creation order", () => {
        expect(ids(getBrothers(TREE, "a2a"))).toEqual(["a2a", "a2b"]);
        expect(ids(getBrothers(TREE, "a2b"))).toEqual(["a2a", "a2b"]);
    });

    it("includes the message itself when it is the only one", () => {
        expect(ids(getBrothers(TREE, "a1"))).toEqual(["a1"]);
    });

    it.each([
        ["an unknown id", "does-not-exist"],
        ["no id at all", undefined],
    ])("returns nothing for %s", (_label, id) => {
        expect(getBrothers(TREE, id)).toEqual([]);
    });
});

describe("getLastChild", () => {
    it("follows the branch to its tip", () => {
        expect(getLastChild([...TREE], "q3")?.id).toBe("a3");
    });

    it("returns the message itself when it has no children", () => {
        expect(getLastChild([...TREE], "a3")?.id).toBe("a3");
        expect(getLastChild([...TREE], "a2a")?.id).toBe("a2a");
    });

    it("returns null for an unknown id", () => {
        expect(getLastChild([...TREE], "does-not-exist")).toBeNull();
        expect(getLastChild([...TREE], undefined)).toBeNull();
    });
});
