import { TFunction } from "i18next";
import { Conversation } from "@/services/conversation";
import { randomSentencesGenerator } from "@/lib/lorem";

export const randomConversationName = () => randomSentencesGenerator(3, 10);

/*
Generate a random number of conversations
 */
export const generateRandomConversations = (max = 40) => {
    const conversations: Conversation[] = [];
    const numberOfConversations = Math.floor(Math.random() * max) + 1;
    for (let i = 0; i < numberOfConversations; i++) {
        conversations.push({
            id: Math.random().toString(36).substring(2),
            title: randomConversationName(),
            // random date in the last 30 days
            update_timestamp: Date.now() - Math.floor(Math.random() * 30) * 24 * 60 * 60 * 1000,
        });
    }
    return conversations;
};

export interface PeriodLabels {
    today: string;
    yesterday: string;
    previous7Days: string;
    previous30Days: string;
}

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Groups conversations into today, yesterday, the previous 7 and 30 days, then
 * months of this year, then years — newest group first, empty groups left out.
 * Days are calendar days in local time.
 */
export const groupConversationsByPeriod = (
    conversations: Conversation[],
    labels: PeriodLabels,
    locale: string,
    now: Date = new Date(),
): { label: string; conversations: Conversation[] }[] => {
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const monthName = new Intl.DateTimeFormat(locale, { month: "long" });

    const periodOf = (timestamp: number): string => {
        const date = new Date(timestamp);
        const startOfDay = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
        const daysAgo = Math.round((startOfToday - startOfDay) / DAY_MS);
        if (daysAgo <= 0) return labels.today;
        if (daysAgo === 1) return labels.yesterday;
        if (daysAgo <= 7) return labels.previous7Days;
        if (daysAgo <= 30) return labels.previous30Days;
        if (date.getFullYear() === now.getFullYear()) return monthName.format(date);
        return String(date.getFullYear());
    };

    const groups = new Map<string, Conversation[]>();
    for (const conversation of [...conversations].sort((a, b) => b.update_timestamp - a.update_timestamp)) {
        const label = periodOf(conversation.update_timestamp);
        groups.set(label, [...(groups.get(label) ?? []), conversation]);
    }
    return [...groups.entries()].map(([label, items]) => ({ label, conversations: items }));
};

export const groupConversationsByDate =(conversations: Conversation[], translate: TFunction): { date: string; conversations: Conversation[] }[] => {
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const lastWeek = new Date(today);
    lastWeek.setDate(lastWeek.getDate() - 7);
    const twoWeeksAgo = new Date(today);
    twoWeeksAgo.setDate(twoWeeksAgo.getDate() - 14);

    return conversations
        .sort((a, b) => b.update_timestamp - a.update_timestamp)
        .reduce(
            (acc, conversation) => {
                const conversationDate = new Date(conversation.update_timestamp);
                if (conversationDate.toDateString() === today.toDateString()) {
                    acc[0].conversations.push(conversation);
                } else if (conversationDate.toDateString() === yesterday.toDateString()) {
                    acc[1].conversations.push(conversation);
                } else if (conversationDate > lastWeek) {
                    acc[2].conversations.push(conversation);
                } else if (conversationDate > twoWeeksAgo) {
                    acc[3].conversations.push(conversation);
                } else {
                    const year = conversationDate.getFullYear();
                    const yearGroup = acc.find((group) => group.date === year.toString());
                    if (yearGroup) {
                        yearGroup.conversations.push(conversation);
                    } else {
                        acc.push({ date: year.toString(), conversations: [conversation] });
                    }
                }
                return acc;
            },
            [
                { date: translate("dates.today"), conversations: [] },
                { date: translate("dates.yesterday"), conversations: [] },
                { date: translate("dates.lastWeek"), conversations: [] },
                { date: translate("dates.twoWeeksAgo"), conversations: [] },
            ] as { date: string; conversations: Conversation[] }[],
        );
};
