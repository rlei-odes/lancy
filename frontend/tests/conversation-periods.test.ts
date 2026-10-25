/**
 * Intent: the chat list groups conversations into periods that stay useful as they age.
 *
 * Recent chats are grouped relative to today (today, yesterday, the previous 7
 * and 30 days), older ones of this year by month, and anything earlier by year.
 * Grouping by calendar day, not elapsed hours: a chat from 23:00 yesterday is
 * "yesterday" at 08:00 today.
 */

import { describe, expect, it } from "vitest";

import { groupConversationsByPeriod } from "@/lib/conversation";
import { Conversation } from "@/services/conversation";

const NOW = new Date(2026, 9, 25, 8, 0); // 25 Oct 2026, 08:00 local
const LABELS = { today: "Today", yesterday: "Yesterday", previous7Days: "Previous 7 days", previous30Days: "Previous 30 days" };

const at = (id: string, date: Date): Conversation => ({ id, title: id, update_timestamp: date.getTime() });

describe("groupConversationsByPeriod", () => {
    it("puts each conversation in the right period, newest group first", () => {
        const groups = groupConversationsByPeriod(
            [
                at("2024", new Date(2024, 4, 1)),
                at("today", new Date(2026, 9, 25, 7, 0)),
                at("yesterday-late", new Date(2026, 9, 24, 23, 0)),
                at("five-days", new Date(2026, 9, 20)),
                at("twenty-days", new Date(2026, 9, 5)),
                at("august", new Date(2026, 7, 10)),
                at("march", new Date(2026, 2, 3)),
            ],
            LABELS,
            "en",
            NOW,
        );

        expect(groups.map((g) => [g.label, g.conversations.map((c) => c.id)])).toEqual([
            ["Today", ["today"]],
            ["Yesterday", ["yesterday-late"]],
            ["Previous 7 days", ["five-days"]],
            ["Previous 30 days", ["twenty-days"]],
            ["August", ["august"]],
            ["March", ["march"]],
            ["2024", ["2024"]],
        ]);
    });

    it("leaves out empty periods and does not reorder the caller's list", () => {
        const input = [at("old", new Date(2025, 0, 1)), at("new", new Date(2026, 9, 25, 6, 0))];

        const groups = groupConversationsByPeriod(input, LABELS, "en", NOW);

        expect(groups.map((g) => g.label)).toEqual(["Today", "2025"]);
        expect(input.map((c) => c.id)).toEqual(["old", "new"]);
    });

    it("names months in the interface language", () => {
        const [group] = groupConversationsByPeriod([at("a", new Date(2026, 7, 1))], LABELS, "de", NOW);

        expect(group.label).toBe("August");
        expect(groupConversationsByPeriod([at("b", new Date(2026, 2, 1))], LABELS, "fr", NOW)[0].label).toBe("mars");
    });
});
