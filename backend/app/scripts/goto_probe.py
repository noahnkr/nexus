"""GoTo Connect capability probe (v1.2.0, Task A2 — the transcript gate).

    python -m app.scripts.goto_probe [--history DAYS] [--listen SECONDS] [--show-excerpt]

**ANSWERED 2026-07-21 — the gate FAILED, and calls now ship as metadata.** This
account produces no call recordings, so there is nothing to transcribe. Keep this
script for the day someone enables recording in GoTo Admin and wants to re-test;
do not re-run it expecting a different answer from the same configuration.

The evidence, all reproducible with `--history 90`:

  * 100 real calls over 90 days carry ZERO recording or transcript fields. The
    record shape is exactly `legId, originatorId, caller{name,number},
    callee{name,number}, direction, startTime, answerTime, duration,
    hangupCause, ownerPhoneNumber` — nothing more.
  * `/recording/v1/recordings/search` exists (GET-only: 405 on POST) but rejects
    all 15 query grammars tried with a contentless 400. Every other recording
    path 404s; there is no `/recording/v1/settings` to interrogate.
  * The account's live subscription list carries no recording/transcript event
    types at all.
  * **403 and 400 are distinguishable here, which is what makes the conclusion
    safe.** `/voicemail/v1/voicemails/search` and `.../transcriptions` answer 403
    `AUTHZ_INSUFFICIENT_SCOPE` — real endpoints, scope not granted — while the
    recording surface answers 400. So `recording.v1.read` IS granted and working;
    there is simply nothing recorded to read.

Conclusion: this is an Admin setting or plan-tier question, not an API one.
(Open lead, if transcripts are ever wanted: voicemail transcription is reachable
and only scope-blocked. Voicemail ≠ calls, so it does not satisfy the original
requirement on its own.)

Originally this ran before any inbound machinery was written, to decide whether
Parts B–E were worth building.

Why a probe rather than reading the docs: the developer portal documents the
notification-channel, call-events and messaging surfaces well, but the
recording/transcript surface is barely documented (as of build time the changelog
mentions only a voicemail transcription endpoint, a call-control start-recording
endpoint, and a `recordings` field on call-event participants). The account is
the authority on what the account can do.

WHAT IT PRINTS: endpoint paths, HTTP statuses, and structural facts (counts, key
names, transcript lengths). It does NOT print call contents by default — these
are real client conversations and an operator's scrollback is not a place for
them. `--show-excerpt` opts into a short redacted excerpt when you need to prove
the text is genuinely there.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from ..services.connectors.goto_client import GoToClient, GoToError, credentials_configured

# Candidate read surfaces, in the order worth trying. The portal does not publish
# a single index of these, so the probe reports what the account actually answers
# rather than asserting what "should" exist.
CANDIDATES = (
    ("identity", "/identity/v1/Users/me"),
    ("call-events subscriptions", "/call-events/v1/subscriptions"),
    ("recordings (recording/v1)", "/recording/v1/recordings"),
    ("recordings (recordings/v1)", "/recordings/v1/recordings"),
    ("call history", "/call-history/v1/calls"),
    ("call reports", "/call-reports/v1/reports/user-activity"),
    ("voice admin lines", "/voice-admin/v1/lines"),
    ("voice admin extensions", "/voice-admin/v1/extensions"),
    ("voice admin phone numbers", "/voice-admin/v1/phone-numbers"),
    ("messaging", "/messaging/v1/messages"),
)

# Keys whose presence anywhere in a response says "transcript text lives here".
_TRANSCRIPT_KEYS = ("transcript", "transcripturl", "transcription", "transcriptionurl")


def _find_keys(obj, needles, path="") -> list[str]:
    """Every dotted path in a decoded JSON body whose key matches a needle."""
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f"{path}.{key}" if path else key
            if key.lower() in needles:
                found.append(here)
            found.extend(_find_keys(value, needles, here))
    elif isinstance(obj, list):
        for i, value in enumerate(obj[:3]):  # a few items is enough to learn the shape
            found.extend(_find_keys(value, needles, f"{path}[{i}]"))
    return found


def _shape(body) -> str:
    """A one-line structural description — key names and counts, never values."""
    if isinstance(body, dict):
        keys = list(body.keys())
        return f"object, keys: {', '.join(keys[:12])}{' …' if len(keys) > 12 else ''}"
    if isinstance(body, list):
        return f"array of {len(body)}"
    return type(body).__name__


async def probe(show_excerpt: bool) -> int:
    if not credentials_configured():
        print(
            "GOTO_CONNECT_CLIENT_ID / _CLIENT_SECRET / _REFRESH_TOKEN are not all "
            "set in .env. Run `python -m app.scripts.goto_oauth` first.",
            file=sys.stderr,
        )
        return 2

    reachable: dict[str, object] = {}
    transcript_paths: list[tuple[str, str]] = []

    async with GoToClient() as goto:
        print("== endpoint reachability ==")
        for label, path in CANDIDATES:
            try:
                resp = await goto.request("GET", path)
            except GoToError as exc:
                # The message carries the status; keep it to one line.
                detail = str(exc).split(" — ", 1)[-1][:120]
                print(f"  {path:<44} {detail}")
                continue
            try:
                body = resp.json() if resp.content else {}
            except ValueError:
                print(f"  {path:<44} {resp.status_code} (non-JSON body)")
                continue
            reachable[path] = body
            hits = _find_keys(body, _TRANSCRIPT_KEYS)
            transcript_paths.extend((path, h) for h in hits)
            note = f"  ⟵ transcript keys: {', '.join(hits[:4])}" if hits else ""
            print(f"  {path:<44} {resp.status_code} — {_shape(body)}{note}")

    print("\n== verdict ==")
    if transcript_paths:
        print("Transcript-bearing fields found:")
        for path, key in transcript_paths[:20]:
            print(f"  {path} → {key}")
        print("\nGATE: transcript surface EXISTS — follow the URLs above to fetch text.")
    else:
        print(
            "GATE: no transcript-bearing field on any reachable read endpoint.\n"
            "This does NOT yet prove the account cannot deliver transcripts — the\n"
            "Recording API may only push them over a notification channel, which\n"
            "this probe does not subscribe to. Next step before declaring the gate\n"
            "failed: place a recorded call, then re-run; and check whether the\n"
            "account's plan includes call recording + transcription at all."
        )

    if show_excerpt and reachable:
        print("\n== raw first-object excerpt (contains real data) ==")
        for path, body in list(reachable.items())[:3]:
            print(f"--- {path}")
            print(json.dumps(body, indent=2)[:1200])

    return 0 if transcript_paths else 1


# --------------------------------------------------------------------------- #
# listen mode — the decisive test when the read surface won't answer
# --------------------------------------------------------------------------- #
async def listen(seconds: float, account_key: str | None) -> int:
    """Create a WebSocket notification channel, subscribe the recording and
    call-event surfaces to it, and print whatever GoTo pushes.

    This exists because `/recording/v1/recordings/search` rejects every query
    grammar tried (see the module docstring) while `/recording/v1/subscriptions`
    answers 200 — i.e. on this account the recording surface is reachable by
    SUBSCRIPTION, not by search. What arrives here is the authority on the
    notification shape that `gt_map` will translate in Part B.

    Frames are printed truncated: they carry real call data.
    """
    import websockets

    async with GoToClient() as goto:
        if account_key is None:
            lines = await goto.get_json("/users/v1/lines")
            items = lines.get("items") or []
            account_key = items[0].get("accountKey") if items else None
            print(f"discovered accountKey: {account_key}")
        if not account_key:
            print("could not discover an accountKey", file=sys.stderr)
            return 2

        channel = await goto.post_json(
            "/notification-channel/v1/channels/nexus-probe",
            {"channelType": "WebSockets", "applicationTag": "nexus-probe"},
        )
        channel_id = channel.get("channelId")
        # The portal's example shows channelURL at the top level; the live API
        # nests it under `channelData` (verified 2026-07-21). Accept both.
        channel_url = channel.get("channelURL") or (
            (channel.get("channelData") or {}).get("channelURL")
        )
        print(
            f"channel created: lifetime={channel.get('channelLifetime')}s "
            f"url={'yes' if channel_url else 'MISSING'}"
        )
        if not channel_url:
            print(f"unexpected channel response: {json.dumps(channel)[:400]}", file=sys.stderr)
            return 2

        # Recording subscription. The event-type vocabulary is undocumented; an
        # empty/duff list is itself informative, since the error usually names the
        # accepted values.
        for body in (
            {"channelId": channel_id, "accountKey": account_key},
            {"channelId": channel_id, "accountKey": account_key,
             "eventTypes": ["RECORDING_READY", "TRANSCRIPT_READY"]},
        ):
            try:
                resp = await goto.request("POST", "/recording/v1/subscriptions", json=body)
                print(f"recording subscription: {resp.status_code} {resp.text[:300]}")
                break
            except GoToError as exc:
                print(f"recording subscription rejected — {str(exc)[:300]}")

        try:
            resp = await goto.request(
                "POST", "/call-events/v1/subscriptions",
                json={"channelId": channel_id,
                      "accountKeys": [{"id": account_key, "events": ["STARTING", "ENDING"]}]},
            )
            print(f"call-events subscription: {resp.status_code} {resp.text[:200]}")
        except GoToError as exc:
            print(f"call-events subscription rejected — {str(exc)[:300]}")

        print(f"\n== listening {seconds:.0f}s — place a call to the business line now ==")
        count = 0
        try:
            async with websockets.connect(channel_url) as ws:
                while True:
                    try:
                        frame = await asyncio.wait_for(ws.recv(), timeout=seconds)
                    except asyncio.TimeoutError:
                        break
                    count += 1
                    text = (
                        frame if isinstance(frame, str)
                        else bytes(frame).decode("utf-8", "replace")
                    )
                    print(f"\n--- frame {count} ---")
                    print(text[:1500])
        except Exception as exc:  # noqa: BLE001 — a probe reports, it doesn't trace
            print(f"websocket error: {type(exc).__name__}: {exc}", file=sys.stderr)

    print(f"\n{count} frame(s) received.")
    return 0 if count else 1


# --------------------------------------------------------------------------- #
# history mode — the decisive test that needs no live call
# --------------------------------------------------------------------------- #

# The account facts A2 established on 2026-07-21. Encoded rather than re-probed;
# `--account-key` overrides if the account ever changes.
ACCOUNT_KEY = "6327799820468129299"

# Candidate ways to ask the recording surface for a specific call's recording.
# `/recording/v1/recordings/search` is known to EXIST (400 on a bare GET, 405 on
# POST) but its parameter contract is undocumented; these are the grammars worth
# trying once a real call id is in hand. Each entry is (label, path, params-fn).
_RECORDING_QUERIES = (
    ("search?callId", "/recording/v1/recordings/search", lambda c: {"callId": c}),
    ("search?legId", "/recording/v1/recordings/search", lambda c: {"legId": c}),
    ("search?conversationId", "/recording/v1/recordings/search",
     lambda c: {"conversationId": c}),
    ("search?callIds", "/recording/v1/recordings/search", lambda c: {"callIds": c}),
    ("search?sessionId", "/recording/v1/recordings/search", lambda c: {"sessionId": c}),
    ("by-id", "/recording/v1/recordings/{c}", None),
)

# Voicemail is the other place GoTo is documented to produce transcription text.
# If calls cannot be transcribed but voicemails can, that is a materially
# different gate answer and worth establishing in the same pass.
_VOICEMAIL_CANDIDATES = (
    "/voicemail/v1/voicemails",
    "/voicemail/v1/messages",
    "/voice-admin/v1/voicemails",
    "/call-history/v1/voicemails",
)


def _iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ids(call: dict) -> list[tuple[str, str]]:
    """Every plausible identifier on a call-history record, as (key, value)."""
    out = []
    for key, value in call.items():
        if isinstance(value, str) and value and (
            key.lower().endswith("id") or key.lower() in {"callid", "legid"}
        ):
            out.append((key, value))
    return out


async def history(days: float, account_key: str | None, show_excerpt: bool) -> int:
    """Answer the transcript gate from calls that ALREADY happened.

    A live call-and-listen walk is the cleanest evidence, but it is not the only
    evidence: if this account records and transcribes, months of completed calls
    already carry recordings, and the call-history record for one of them is
    where the link has to surface. This mode pulls a real window of history,
    reports what identifiers and recording-shaped fields the records carry, then
    tries the recording surface against a real call id.

    It doubles as fixture capture for Part B — `gt_map` needs real record shapes,
    and these are real.
    """
    from datetime import datetime, timedelta, timezone

    key = account_key or ACCOUNT_KEY
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    async with GoToClient() as goto:
        print(f"== call history: {days:g} days, accountKey={key} ==")
        try:
            body = await goto.get_json(
                "/call-history/v1/calls",
                {"accountKey": key, "startTime": _iso(start), "endTime": _iso(end)},
            )
        except GoToError as exc:
            print(f"call-history failed — {exc}", file=sys.stderr)
            return 2

        calls = body.get("items") or body.get("data") or body.get("calls") or []
        if isinstance(calls, dict):
            calls = list(calls.values())
        print(f"  {len(calls)} call record(s); response keys: {_shape(body)}")
        if not calls:
            print("  no calls in this window — widen --history and re-run.")
            return 1

        sample = calls[0]
        print(f"  record keys: {', '.join(sorted(sample.keys()))}")

        hits = _find_keys(body, _TRANSCRIPT_KEYS)
        rec_hits = _find_keys(body, ("recording", "recordings", "recordingid", "recordingurl"))
        print(f"  transcript-shaped fields in history: {hits or 'none'}")
        print(f"  recording-shaped fields in history:  {rec_hits or 'none'}")

        # How many records carry anything recording-ish at all — one call with a
        # recording is enough to prove the capability exists on this plan.
        with_rec = [c for c in calls if _find_keys(c, ("recording", "recordings", "recordingid"))]
        print(f"  {len(with_rec)}/{len(calls)} record(s) carry a recording field")

        print("\n== recording surface, against a real call id ==")
        probed: set[tuple[str, str]] = set()
        for call in (with_rec or calls)[:3]:
            for id_key, id_value in _ids(call)[:4]:
                for label, path, params_fn in _RECORDING_QUERIES:
                    target = path.format(c=id_value)
                    if (target, label) in probed:
                        continue
                    probed.add((target, label))
                    try:
                        resp = await goto.request(
                            "GET", target, params=params_fn(id_value) if params_fn else None
                        )
                    except GoToError as exc:
                        detail = str(exc).split(" — ", 1)[-1][:100]
                        print(f"  {id_key}={id_value[:18]:<20} {label:<22} {detail}")
                        continue
                    try:
                        rbody = resp.json() if resp.content else {}
                    except ValueError:
                        rbody = {}
                    rhits = _find_keys(rbody, _TRANSCRIPT_KEYS)
                    print(
                        f"  {id_key}={id_value[:18]:<20} {label:<22} "
                        f"{resp.status_code} — {_shape(rbody)}"
                        f"{'  ⟵ TRANSCRIPT: ' + ', '.join(rhits) if rhits else ''}"
                    )
                    if rhits:
                        print("\nGATE: transcript field reachable from call history. PROCEED.")
                        if show_excerpt:
                            print(json.dumps(rbody, indent=2)[:1500])
                        return 0

        print("\n== voicemail surface ==")
        for path in _VOICEMAIL_CANDIDATES:
            try:
                resp = await goto.request("GET", path, params={"accountKey": key})
            except GoToError as exc:
                print(f"  {path:<36} {str(exc).split(' — ', 1)[-1][:90]}")
                continue
            try:
                vbody = resp.json() if resp.content else {}
            except ValueError:
                vbody = {}
            vhits = _find_keys(vbody, _TRANSCRIPT_KEYS)
            print(f"  {path:<36} {resp.status_code} — {_shape(vbody)}"
                  f"{'  ⟵ TRANSCRIPT: ' + ', '.join(vhits) if vhits else ''}")

        print("\n== messaging history (Task 7 evidence, free with this pass) ==")
        for params in ({"accountKey": key}, {"ownerPhoneNumber": "+16303602784"}, {}):
            try:
                resp = await goto.request("GET", "/messaging/v1/messages", params=params)
            except GoToError as exc:
                print(f"  params={params or '{}'} -> {str(exc).split(' — ', 1)[-1][:110]}")
                continue
            try:
                mbody = resp.json() if resp.content else {}
            except ValueError:
                mbody = {}
            print(f"  params={params or '{}'} -> {resp.status_code} — {_shape(mbody)}")

        if show_excerpt:
            print("\n== one raw call record (REAL DATA) ==")
            print(json.dumps(sample, indent=2)[:2000])

    print(
        "\nGATE: no transcript reachable from historical calls.\n"
        "Combined with A2's earlier findings (search endpoint rejects every query\n"
        "grammar; the account's own subscription list carries no recording event\n"
        "types), the remaining explanation is that this account is not recording —\n"
        "which is an Admin setting / plan-tier question, not an API one."
    )
    return 1


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GoTo transcript-availability probe")
    parser.add_argument(
        "--show-excerpt", action="store_true",
        help="print a truncated raw excerpt (REAL CALL DATA — use deliberately)",
    )
    parser.add_argument(
        "--listen", type=float, metavar="SECONDS", default=None,
        help="create a channel, subscribe recordings+call events, print pushed frames",
    )
    parser.add_argument(
        "--history", type=float, metavar="DAYS", default=None,
        help="answer the gate from calls that already happened (no live call needed)",
    )
    parser.add_argument("--account-key", default=None, help="skip account-key discovery")
    args = parser.parse_args(argv)
    if args.history is not None:
        return await history(args.history, args.account_key, args.show_excerpt)
    if args.listen is not None:
        return await listen(args.listen, args.account_key)
    return await probe(args.show_excerpt)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
