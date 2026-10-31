// Whether the chat should keep following new messages at the bottom.
// Only the person scrolling (wheel, touch, keys) can make the view leave the bottom; scroll events that
// the app causes itself (pinning to the bottom while text streams in) can only bring it back.
export const NEAR_BOTTOM_PX = 120;

export function nextFollow(
  following: boolean,
  distanceFromBottom: number,
  byUser: boolean,
): boolean {
  const near = distanceFromBottom < NEAR_BOTTOM_PX;
  if (near) return true;
  return byUser ? false : following;
}
