import { Message } from "@/services/message";

export const getMessageThread = (messages: Message[], messageId?: Message["id"]): Message[] => {
    const message = messages.find((msg) => msg.id === messageId);

    if (!message) {
        return [];
    }

    if (!message.parent_id) {
        return [message];
    }

    const parentMessage = getMessageThread(messages, message.parent_id);
    return [message, ...parentMessage].sort((a, b) => a.create_timestamp - b.create_timestamp);
};

/**
 * The thread with `streamed` as its last message, replacing the loading placeholder.
 *
 * The question was shown before the server had saved it, under a local id. The
 * stream names the server's id as the answer's `parent_id`, and the next send
 * rebuilds the thread by walking those ids up from the answer: with the local id
 * still in place the walk stops at the answer, and the question disappears from the
 * chat until the conversation is reloaded. So the question takes the server's id.
 */
export const withStreamedMessage = (thread: Message[], streamed: Message, localQuestionId?: Message["id"]): Message[] => {
    const serverId = streamed.parent_id;
    const earlier = thread.slice(0, -1).map((m) =>
        localQuestionId !== undefined && serverId && m.id === localQuestionId ? { ...m, id: serverId } : m
    );
    return [...earlier, streamed];
};

export const getBrothers = (messages: Message[], messageId?: Message["id"]): Message[] => {
    const message = messages.find((msg) => msg.id === messageId);

    if (!message) {
        return [];
    }

    return messages.filter((m) => m.parent_id === message.parent_id).sort((a, b) => a.create_timestamp - b.create_timestamp);
};

export const getLastChild = (messages: Message[], messageId?: Message["id"]): Message | null => {
    const message = messages.find((msg) => msg.id === messageId);

    if (!message) {
        return null;
    }

    const childMessageId = message.id;

    const child = messages.sort((a, b) => b.create_timestamp - a.create_timestamp).find((msg) => msg.parent_id === childMessageId);

    if (!child) {
        return message;
    }

    return getLastChild(messages, child.id);
};
