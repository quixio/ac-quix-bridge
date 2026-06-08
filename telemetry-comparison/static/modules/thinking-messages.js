/**
 * Friendly status-label pool, ported verbatim from Quix.AI's native chat
 * (frontend/src/utils/thinkingMessages.ts) so the Telemetry Explorer chat shows
 * the same waiting experience: raw SSE status keys ("generating", "rate_limited",
 * …) map to a random human-friendly line via formatStatus().
 */

// Base messages (no trailing "..." — formatStatus appends it).
export const thinkingMessages = [
  'Thinking',
  'Working my magic',
  'Crunching the data',
  'On it',
  'Cooking up an answer',
  'Connecting the dots',
  'Brewing something good',
  'Assembling the pieces',
  'Let me figure this out',
  'Doing the heavy lifting',
  'Almost there, probably',
  'Give me a sec',
  'Chewing on that',
  'Processing',
  'Mulling it over',
  'Noodling on this',
  'Digging in',
  'Untangling this',
  'Running the numbers',
  'Turning the gears',
  'Mapping it out',
  'Tinkering',
  'Bear with me',
  'Hold that thought',
  'Sifting through the options',
  'Warming up the engines',
  'Poking around',
  'Working through it',
  'Juggling some ideas',
  'Pulling a few threads',
  'Sketching it out',
  'Rifling through the docs',
  'Wrangling some code',
  'Cracking this open',
  'Down the rabbit hole',
  'Spinning up',
  'Doing science',
  'Loading brain cells',
  'Asking the rubber duck',
  'Staring at the ceiling productively',
  'Hold my coffee',
  'Summoning the right neurons',
  'Borrowing some brain power',
  'Rummaging through the toolbox',
  'Trust the process',
  'Shaking the magic 8-ball',
  'Reading the tea leaves',
  'Plot twist incoming',
  'Channeling my inner wizard',
];

export const rateLimitedMessages = [
  'Taking a breather',
  'Bit of a queue, hang on',
  "In line, won't be long",
  'Traffic jam, sitting tight',
  'Waiting my turn',
  'Patience is a virtue, right',
  'Grabbing a ticket, waiting in line',
  "Queued up, won't be long",
];

export const reconnectingMessages = [
  'Reconnecting...',
  'Connection interrupted, reconnecting',
  'Re-establishing connection',
];

export const statusMessages = {
  generating: thinkingMessages,
  compacting: ['Archiving conversation'],
  rate_limited: rateLimitedMessages,
  reconnecting: reconnectingMessages,
  merging: ['Merging changes into branch'],
  merged: ['Changes merged'],
  merge_failed: ['Merge failed - you can ask to retry'],
};

export function pickRandom(messages) {
  return messages[Math.floor(Math.random() * messages.length)];
}

/** Map a raw status key to a friendly label; unknown keys pass through. */
export function formatStatus(status) {
  const pool = statusMessages[status];
  return pool ? pickRandom(pool) + '...' : status;
}
