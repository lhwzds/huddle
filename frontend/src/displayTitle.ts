import type { Conversation, Message } from './types';

export function displayTitle(conversation: Conversation, messages: Message[]): string {
  if (conversation.title?.trim()) return conversation.title;
  const firstUserMessage = messages.find((message) => message.author === 'user');
  return firstUserMessage ? firstUserMessage.text.slice(0, 40) : 'New chat';
}
