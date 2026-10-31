import { describe, expect, it } from 'vitest';
import { displayTitle } from '../src/displayTitle';
import type { Conversation, Message } from '../src/types';

const conversation: Conversation = { id: 'c1', title: '', created_at: '' };
const message = (author: string, text: string) => ({ author, text }) as Message;

describe('displayTitle', () => {
  it('uses New chat when the conversation has no title or user message', () => {
    expect(displayTitle(conversation, [])).toBe('New chat');
  });
  it('treats a null title (a new conversation from the API) as untitled', () => {
    expect(displayTitle({ ...conversation, title: null }, [])).toBe('New chat');
    expect(displayTitle({ ...conversation, title: null }, [message('user', 'Hi team')])).toBe(
      'Hi team',
    );
  });
  it('uses the first user message, truncated to 40 characters', () => {
    expect(
      displayTitle(conversation, [message('agent', 'later'), message('user', 'x'.repeat(41))]),
    ).toBe('x'.repeat(40));
  });
  it('prefers an existing conversation title', () => {
    expect(displayTitle({ ...conversation, title: 'Existing' }, [message('user', 'Hello')])).toBe(
      'Existing',
    );
  });
});
