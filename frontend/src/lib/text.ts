// Plain-text conversion for text that may carry HTML.
//
// WelcomeHome's email activities arrive as HTML fragments (332 of 431 emails in
// the corpus), and we deliberately do NOT render them as rich text: an event
// timeline is a record of what happened, not a mail client, and rendering
// attacker-influenced markup from an external CRM is a risk with no upside here.
// So markup becomes readable plain text — no sanitizer dependency, no innerHTML.
//
// Idempotent by construction: text with no tags and no entities passes through
// unchanged, so it is safe to apply to every value whether or not it is HTML.

// Block-level tags that mean "line break" to a reader. Closing forms mostly,
// plus the self-closing <br>.
const BLOCK_BREAK =
  /<\s*(?:br\s*\/?|\/\s*(?:p|div|li|tr|h[1-6]|blockquote|section|article))\s*>/gi;

const NAMED_ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  "#39": "'",
  nbsp: " ",
};

function decodeEntities(text: string): string {
  return text.replace(/&(#x?[0-9a-f]+|[a-z]+);/gi, (whole, ref: string) => {
    const named = NAMED_ENTITIES[ref.toLowerCase()];
    if (named !== undefined) return named;
    // Numeric references: &#8217; (decimal) and &#x2019; (hex).
    const numeric = /^#x([0-9a-f]+)$/i.exec(ref) ?? /^#(\d+)$/.exec(ref);
    if (numeric) {
      const code = parseInt(numeric[1], /^#x/i.test(ref) ? 16 : 10);
      if (Number.isFinite(code) && code > 0 && code <= 0x10ffff) {
        try {
          return String.fromCodePoint(code);
        } catch {
          return whole; // out-of-range surrogate — leave it as written
        }
      }
    }
    return whole; // unknown entity: better to show it than to eat it
  });
}

/**
 * Convert a possibly-HTML string to readable plain text.
 *
 * Block tags become newlines, remaining tags are stripped, entities are decoded,
 * and runs of 3+ newlines collapse to a paragraph break. Returns "" for
 * null/undefined so callers never have to null-check first.
 */
export function htmlToText(input: string | null | undefined): string {
  if (!input) return "";

  return decodeEntities(
    input
      // Script/style content is markup plumbing, not prose — drop it whole
      // rather than leaving its body behind as text.
      .replace(/<\s*(script|style)\b[^>]*>[\s\S]*?<\s*\/\s*\1\s*>/gi, "")
      .replace(BLOCK_BREAK, "\n")
      .replace(/<[^>]*>/g, ""),
  )
    .replace(/\r\n?/g, "\n")
    // Trailing spaces before a newline read as ragged indentation once the tags
    // that produced them are gone.
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
