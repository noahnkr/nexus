import { describe, expect, it } from "vitest";
import { htmlToText } from "./text";

describe("htmlToText", () => {
  it("converts the real WelcomeHome email shape to readable text", () => {
    // Verbatim shape from the dev corpus (332 of 431 emails look like this).
    const raw =
      "<b>Come See Us at the DuPage County Fair This Week!</b><br><br>" +
      "Hi! We&#39;ll have a booth all week &amp; would love to say hello.";

    expect(htmlToText(raw)).toBe(
      "Come See Us at the DuPage County Fair This Week!\n\n" +
        "Hi! We'll have a booth all week & would love to say hello.",
    );
  });

  it("turns block tags into line breaks and strips the rest", () => {
    expect(htmlToText("<p>One</p><p>Two</p>")).toBe("One\nTwo");
    expect(htmlToText("<div>A</div><div>B</div>")).toBe("A\nB");
    expect(htmlToText("<ul><li>x</li><li>y</li></ul>")).toBe("x\ny");
    expect(htmlToText("a<br/>b<br />c")).toBe("a\nb\nc");
    expect(htmlToText('<a href="http://x.test">link</a>')).toBe("link");
  });

  it("decodes named and numeric entities", () => {
    expect(htmlToText("&amp; &lt; &gt; &quot; &#39; &apos;")).toBe("& < > \" ' '");
    expect(htmlToText("a&nbsp;b")).toBe("a b");
    expect(htmlToText("&#8217;")).toBe("’"); // decimal
    expect(htmlToText("&#x2019;")).toBe("’"); // hex
  });

  it("leaves an unknown entity visible rather than eating it", () => {
    expect(htmlToText("50&percnt; off")).toBe("50&percnt; off");
  });

  it("is idempotent on plain text", () => {
    const plain = "Called the family. Voicemail left; will retry Tuesday.";
    expect(htmlToText(plain)).toBe(plain);
    expect(htmlToText(htmlToText(plain))).toBe(plain);
  });

  it("collapses 3+ newlines to a paragraph break", () => {
    expect(htmlToText("a<br><br><br><br>b")).toBe("a\n\nb");
    expect(htmlToText("a\n\n\n\nb")).toBe("a\n\nb");
  });

  it("drops script and style bodies instead of exposing them as text", () => {
    expect(htmlToText("<style>.x{color:red}</style>Hi")).toBe("Hi");
    expect(htmlToText("<script>alert(1)</script>Hi")).toBe("Hi");
  });

  it("returns an empty string for empty input", () => {
    expect(htmlToText(null)).toBe("");
    expect(htmlToText(undefined)).toBe("");
    expect(htmlToText("")).toBe("");
    expect(htmlToText("   ")).toBe("");
  });
});
