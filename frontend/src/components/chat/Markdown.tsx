import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Assistant messages are markdown-authored; render them as GFM (tables, lists,
// task lists, strikethrough) with prose styling tuned in index.css (.prose-chat).
// react-markdown does not render raw HTML unless rehype-raw is added, so model
// output can't inject markup. User bubbles stay plain text — their input isn't
// markdown, and rendering it would surprise. Memoized so a stream that only grows
// the *last* message doesn't reparse already-settled ones.
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="prose prose-sm prose-chat max-w-none break-words dark:prose-invert">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
});
