import { describe, expect, it } from "vitest";
import {
  ClipboardCheck,
  FileText,
  Mail,
  MessageSquare,
  Phone,
  ShieldCheck,
  StickyNote,
  UserRound,
} from "lucide-react";
import { eventDisplay, eventIcon, fallbackSummary } from "./events";

// A real dev-corpus email activity: HTML in the notes, and a stored summary that
// was truncated at the 120-char write-time cap with its tags baked in.
const EMAIL_NOTES =
  "<b>Come See Us at the DuPage County Fair This Week!</b><br><br>" +
  "Hi Margaret! We'll have a booth all week and would love to say hello. " +
  "Stop by anytime between 9 and 5 — we'll have information about in-home " +
  "care options, and someone on hand to answer questions.";

const emailEvent = {
  event_type: "lead.activity_logged",
  payload: {
    summary:
      "Email (outbound): <b>Come See Us at the DuPage County Fair This Week!</b><br><br>Hi Margaret! We'll have a booth…",
    detail: {
      notes: EMAIL_NOTES,
      direction: "outbound",
      activity_type: "Email",
      wh_activity_id: "wh:activity:5301",
    },
  },
};

describe("eventDisplay", () => {
  it("fixes both defects on a stored email activity", () => {
    const { title, body } = eventDisplay(emailEvent);

    expect(title).toBe("Email (outbound)");
    // Neither defect survives: no markup, and not clipped at the 120-char cap.
    expect(body).not.toBeNull();
    expect(body).not.toContain("<");
    expect(body!.length).toBeGreaterThan(120);
    expect(body).toContain("Come See Us at the DuPage County Fair This Week!");
    expect(body).toContain("answer questions.");
    // The stored summary is deliberately NOT the source of the title.
    expect(title).not.toContain("…");
  });

  it("handles an unmapped activity type with null notes without throwing", () => {
    const { title, body } = eventDisplay({
      event_type: "lead.activity_logged",
      payload: {
        summary: "Thank You Note logged in WelcomeHome",
        detail: { notes: null, direction: null, activity_type: "Thank You Note" },
      },
    });

    expect(title).toBe("Thank You Note");
    expect(body).toBeNull();
  });

  it("omits the direction when there isn't one", () => {
    expect(
      eventDisplay({
        event_type: "lead.activity_logged",
        payload: { detail: { activity_type: "Home Visit", notes: "Toured the home." } },
      }).title,
    ).toBe("Home Visit");
  });

  it("falls back to the server summary for non-activity events", () => {
    const stageChange = {
      event_type: "lead.stage_changed",
      payload: {
        summary: "Lead 'Margaret Ellison' moved from New to Contacted",
        from: "new",
        to: "contacted",
      },
    };
    expect(eventDisplay(stageChange)).toEqual({
      title: "Lead 'Margaret Ellison' moved from New to Contacted",
      body: null,
    });
  });

  it("survives malformed payloads", () => {
    for (const payload of [
      null,
      undefined,
      {},
      { detail: null },
      { detail: "not an object" },
      { detail: [] },
      { detail: { activity_type: "" } },
      { detail: { activity_type: 42 } },
    ]) {
      const out = eventDisplay({ event_type: "lead.activity_logged", payload: payload as never });
      expect(typeof out.title).toBe("string");
      expect(out.title.length).toBeGreaterThan(0);
    }
  });
});

describe("eventIcon", () => {
  it("picks the icon from the activity type for CRM activities", () => {
    const icon = (activity_type: string) =>
      eventIcon("lead.activity_logged", { detail: { activity_type } });

    expect(icon("Email")).toBe(Mail);
    expect(icon("Call")).toBe(Phone);
    expect(icon("Text")).toBe(MessageSquare);
    expect(icon("Note")).toBe(StickyNote);
    expect(icon("Assessment")).toBe(ClipboardCheck);
    // An unmapped activity type gets a neutral document icon, never the alert.
    expect(icon("Thank You Note")).toBe(FileText);
  });

  it("fills the prefix gaps that used to hit the alert fallback", () => {
    expect(eventIcon("call.completed")).toBe(Phone);
    expect(eventIcon("credential.added")).toBe(ShieldCheck);
    expect(eventIcon("credential.expiring")).toBe(ShieldCheck);
  });

  it("keeps working with a single argument", () => {
    expect(eventIcon("lead.created")).toBe(UserRound);
    expect(eventIcon("lead.activity_logged")).toBe(UserRound);
  });
});

describe("fallbackSummary", () => {
  it("humanizes an event type when no summary is present", () => {
    expect(fallbackSummary({ event_type: "lead.stage_changed" })).toBe("Lead stage changed");
  });
});
