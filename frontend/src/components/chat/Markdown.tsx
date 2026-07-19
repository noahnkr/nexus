import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// Assistant messages are markdown-authored; render them as GFM (tables, lists,
// task lists, strikethrough) with prose styling tuned in index.css (.prose-chat).
// react-markdown does not render raw HTML unless rehype-raw is added, so model
// output can't inject markup. User bubbles stay plain text — their input isn't
// markdown, and rendering it would surprise. Memoized so a stream that only grows
// the *last* message doesn't reparse already-settled ones.
// Tables scroll inside their own container rather than widening the chat column:
// a document-style answer (M15a) can ask for more columns than the bubble has room
// for, and the bubble must never push the page into horizontal scroll.
const components: Components = {
  table: ({ node: _node, ...props }) => (
    <div className="my-3 overflow-x-auto rounded-md border">
      <table {...props} />
    </div>
  ),
};

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="prose prose-sm prose-chat max-w-none break-words dark:prose-invert">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
});
