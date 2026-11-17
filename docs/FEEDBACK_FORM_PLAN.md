# Feedback in the agreement UI: a form and post-it notes

First written 2026-11-04 as the plan for the form. Updated 2026-11-13 (MAC-487) to describe what was built: the form, now with an optional name, and post-it notes left on pages, both reached from a Feedback button at the bottom right of every page. Updated 2026-11-17 after review: authors can delete their notes, notes can go beside the main column, and notifications go to Slack by email instead of a webhook. Scope: the agreement UI in `ui/` (FastAPI and Jinja). The plan builder iframe in `ui/playground/` is out of scope. The form and the notes are part of the page around it, so they work on the plan page too, but a note cannot be placed inside the builder.

## Goal

Anyone logged in can send feedback from any screen in one step, either as a form or as a note stuck on the spot it is about. The server records the account and the page, so the user types only the text and, if they want, their name. The demo uses shared accounts (audit1–audit10, lab, auditor), so the account alone does not say who wrote something; the optional name does. The lab reads everything on one page.

## Decisions

1. **Storage: append-only JSONL files on disk.** The UI keeps no database, and its state is folders on disk. `AIAB_FEEDBACK_DIR` (default `ui/feedback/`, git-ignored; `$DATA/feedback` on Render via `ui/docker-entrypoint.sh`, so it survives redeploys) holds one file per kind:
   - `feedback.jsonl`: one line per form submission.
   - `notes.jsonl`: one line per note (`"kind": "note"`), plus one line each time the lab resolves or reopens a note (`"kind": "resolve"`, the note's id, `resolved`, who, when), one line each time its author edits it (`"kind": "edit"`, the note's id, the new text, who, when), and one line when its author deletes it (`"kind": "delete"`, the note's id, who, when). Readers fold the resolve and edit lines into the notes (only edits by the note's author count) and leave deleted notes out. Nothing is rewritten, so earlier and deleted text stays in the file.
   Not GitHub issues: the repository is public and feedback is internal.
2. **Slack by email, optional.** Decided at the Snoopy check-in on 2026-11-16: no Slack app or webhook (ops never provided a key); every Slack channel has an email address that posts each email it gets as a message. The channel is `#amber-feedback`. Its address is a secret (anyone holding it can post there), so it lives only in the `AIAB_FEEDBACK_EMAIL_TO` env var on Render, never in the repo, the page or a PR. The server sends through a Google Workspace account over SMTP (`smtp.gmail.com`, port 587, STARTTLS, an app password) with Python's `smtplib`, so no new dependency or vendor; mail from a lucidcomputing.ai address also passes if a Slack admin restricts the channel to certain sender domains.
   - The file is written first. The mail goes out from a background thread with a 10 s timeout, and a failure is logged, never shown, so mail being down cannot lose or block feedback.
   - A form entry is mailed at once. A note is mailed `AIAB_FEEDBACK_NOTE_DELAY` seconds (default 300) after it is saved, with its text as it is then (an edit within the delay is in the email; a later edit is not mailed), and not at all if its author deleted it by then, so a mistyped note that is deleted straight away never reaches Slack. A note deleted later stays in Slack. A server restart inside the delay loses that one email; the note itself is on disk.
   - Plain text, because Slack posts the subject and body as written. Subject: `Note from audit3 (Sam) on /a/bio/sign` (or `Feedback from …`). Body: the text, a full link to the page (a note's ends in `#note=<id>`), then the party and the time.
   - Resolving, editing or deleting a note is not mailed on its own.
3. **One Feedback button, bottom right, on every page.** For everyone logged in, `base.html` puts a white circle (48px, a speech-bubble icon in the stroke style of `_icons.html`) with `Feedback` in small type under it, fixed at the bottom right of the window. Nothing about feedback is in the nav. The button is a `<button>` with `aria-label`, `aria-expanded` and a focus ring. It sits above the notes and below the busy overlay, and is made inert with the page while a key submission is waited on. `main.page` gets 136px of bottom padding when logged in, so the last content on a page can scroll clear of the button (checked on the Sign, plan and Agreements pages from 1440 to 900 px wide, as the lab and as an auditor: no control is under the button at any scroll position). On a page taller than the window, for example with a warning box at the top, a control can sit under the button until the page is scrolled further. The circle is the one exception to `swiss.css`'s no-rounded-corners rule, by request.
4. **The button opens a small menu above it** with two choices, each an icon, a bold label and one line:
   - `Write feedback` / `Tell us anything.`: opens the form, a `<dialog>` defined once in `base.html` (the same `.modal` element and CSS as the confirm dialog). The page's path and query go in a hidden field, filled in by the server.
   - `Stick a note on this page` / `Click anywhere to place a post-it.`: starts note mode (5).
   Below them, in small muted text: `Hide notes on this page (n)` / `Show notes on this page (n)`, only when the page has notes, and for the lab only, a link `All feedback` to the list page. Escape, a click outside, or a click into the builder iframe closes the menu.
   Every string on the form:
   - textarea: no visible label; `aria-label="Feedback"`; no placeholder; `required`
   - `Name` with `optional` beside it, one line
   - buttons: `Cancel` and `Send` (Escape closes the dialog too)
   - after sending: `Thanks, sent to the team` as the page's existing success message
5. **Post-it notes.**
   - Note mode: the cursor over the page's main column is a crosshair, and a dark banner at the top of the window says `Click where you want the note · Esc to cancel`, with `Cancel`. Escape, `Cancel` or the Feedback button leave note mode.
   - The next click inside the main column opens a small yellow form at that spot with the text field focused: the text, `Name (optional)`, then `Cancel` and `Save`. While placing, that click follows no link and presses no button. Escape closes the form.
   - After saving, the banner says `Note saved · the team will see it` for a few seconds.
   - The hide or show choice from the menu is remembered in the browser.
   - The click can be in the main column or in the empty space either side of it. Clicks on the nav, the Feedback button, the banner and dialogs do not place a note.
   Notes show for everyone logged in, each with its text, the name if given, the account, and the date. Resolved notes are hidden. The lab sees `Resolve` on each note and `Reopen` on a resolved one. The author of a note (the same login) sees `Edit`, which turns the note into a text field with `Cancel` and `Save` (Escape cancels; the note then shows `edited` after its date, and so does the lab's list), and `Delete`, which asks `Delete this note?` and then removes it from the page, from the lab's list and from everyone's view; the banner says `Note deleted`. On a shared account (the demo's `auditor`, `audit1`–`audit10`) everyone using that account counts as the author. The lab has no delete; it resolves.
   History: a first version floated note buttons at the bottom right without extra page padding, where they covered the Sign page's Run button; a second put `Feedback`, `Add note` and `Hide notes` in the nav. The current button replaces both.
6. **Where a note sits.** Each note keeps two positions:
   - **Anchored:** the nearest ancestor of the clicked element that has an id, inside `<main>` (for example the builder iframe `#builder`, a run step `#step-2`, or a party's card on the Sign page, `#sign-lab` and `#sign-auditor`, ids added for this). The Sign page shows each party different buttons, so the cards differ in height between the lab and the auditor; anchoring keeps a note on the card it was left on. The note stores that id, x as a fraction of the element's width, and y in pixels below its top.
   - **Fallback:** relative to `<main>`, x as a fraction of its width and y in pixels below its top. It is used when the page has no anchor around the click, or when the anchor is missing or has no size (hidden, folded).
   Why this scheme:
   - The main column is centred and capped in width, so a fraction of its width keeps a note over the same content when the window gets wider or narrower.
   - Vertical drift comes from text reflowing above the note. Measuring from the nearest identified element limits that drift to the content between the element's top and the note.
   - Pixel y (not a fraction of the height) keeps a note fixed when content far below it changes length.
   - **Beside the column:** a click left or right of `<main>` stores `side` (`left` or `right`) and `gap`, the distance in pixels outward from that edge of the column, instead of an anchor. The note keeps that distance from the column as the window changes width, and is kept inside the window; when the window is too narrow for the space beside the column, it overlaps the column.
   Notes are re-placed whenever `<main>` changes size (a `ResizeObserver`). A note in the column is kept inside the column on the right.
   Limits:
   - Few elements have ids today, so most notes use the fallback. On a page whose content above the note changes length (a new run, a flash message), such a note moves with it. Giving the main sections of each page an id would make more notes anchored.
7. **What the server records.**
   - Form entry: `id`, `time` (UTC ISO), `user` (the session's username), `party`, `name` (optional; whitespace collapsed; at most 80 characters), `page`, `text` (at most 4000 characters).
   - Note: the same fields, with text capped at 1000 characters, plus `anchor`, `side`, `gap`, `ax`, `ay`, `x`, `y`.
   - `page` is the path and query of the screen, accepted only as a path on this site. It must start with `/`, must not start with `//`, may hold no backslash or control characters, and is capped at 500 characters; anything else becomes `/`. The one-off `msg` and `err` query parameters are dropped, so a note left after a flash message still belongs to the page.
   - Position numbers must be finite. x is clamped to 0–1, y to 0–200000 and gap to 0–4000. `side` is `left`, `right` or empty. An anchor id must match `^[A-Za-z][A-Za-z0-9_-]{0,63}$`.
8. **Escaping.** Everything a person typed is shown to other people. The list page relies on Jinja's autoescaping, the notes script sets text with `textContent` and never as HTML, and the email is plain text.
9. **Reading it.** `GET /feedback` is for the lab only; auditors get a 403. It lists form entries and notes together, newest first: time, party and account with the name, the page as a link, the text, and whether it is a form entry, a note, or a resolved note. A note's link opens the page with `#note=<id>`. The notes script then scrolls to that note and outlines it, even when it is resolved or notes are hidden.

## Routes

| Route | Who | What |
| --- | --- | --- |
| `POST /feedback` | logged in | Form fields `text`, `name`, `page`. Appends to `feedback.jsonl`, mails the Slack channel if set, redirects to the page with `msg=Thanks, sent to the team`. Empty text redirects back with nothing recorded. |
| `GET /feedback` | lab | The list page. |
| `GET /notes?page=` | logged in | JSON `{"notes": [...]}` for that page, resolved ones included (the script hides them), deleted ones not. Each has `mine` when the viewer wrote it. 401 when logged out. |
| `POST /notes` | logged in | JSON `{page, text, name, anchor, side, gap, ax, ay, x, y}`. Returns the saved note. 400 for empty text. |
| `POST /notes/{id}/resolve` | lab | JSON `{"resolved": true or false}`. 403 for the auditor, 404 for an unknown note. |
| `POST /notes/{id}/edit` | the note's author | JSON `{"text": ...}`. Appends an edit line and returns the note. 400 for empty text, 403 for anyone else, 404 for an unknown or deleted note. |
| `POST /notes/{id}/delete` | the note's author | Appends a delete line. 403 for anyone else, 404 for an unknown or already deleted note. |

The JSON routes rely on the session cookie being `SameSite=Lax`, as the forms do: a cross-site POST carries no cookie.

## Files

| File | What |
| --- | --- |
| `ui/server/config.py` | `feedback_dir` (`AIAB_FEEDBACK_DIR`), the email settings (`AIAB_FEEDBACK_EMAIL_TO`, `AIAB_FEEDBACK_EMAIL_FROM`, `AIAB_FEEDBACK_NOTE_DELAY`, `AIAB_SMTP_HOST`, `AIAB_SMTP_PORT`, `AIAB_SMTP_USER`, `AIAB_SMTP_PASSWORD`) on `Settings`, with defaults, because tests build settings with `replace(load_settings(), ...)`. |
| `ui/server/feedback.py` | Record and read both kinds, `clean_page`, the caps, folding resolve, edit and delete lines, the email and its delayed send. |
| `ui/server/app.py` | The seven routes above. |
| `ui/server/templates/base.html` | The Feedback button and its menu, the note-mode banner, the dialog, and the script tag, all inside `{% if user %}`, because `login.html` extends this layout. |
| `ui/server/templates/_icons.html` | `feedback_icon`: the speech bubble, the pen (write) and the sticky note. |
| `ui/server/templates/feedback.html` | The lab's list. |
| `ui/server/templates/sign.html` | An id on each party's card (`sign-lab`, `sign-auditor`), so notes left on the Sign page anchor to the card. |
| `ui/assets/notes.js` | The button's menu, note mode and its banner, and the notes: placing (in the column or beside it), drawing, hiding, resolving, editing, deleting, highlighting. Plain JS, no framework. |
| `ui/assets/aiab.css` | The button, the menu, the banner, the page's bottom padding, the dialog's name field, the notes. |
| `ui/docker-entrypoint.sh` | `AIAB_FEEDBACK_DIR="$DATA/feedback"`. |
| `ui/.gitignore`, `ui/Dockerfile.dockerignore` | `feedback/`, so local feedback is never committed or built into the image. |
| `render.yaml` | `AIAB_FEEDBACK_EMAIL_TO`, `AIAB_SMTP_HOST`, `AIAB_SMTP_USER`, `AIAB_SMTP_PASSWORD`, `sync: false`. |
| `ui/README.md` | The file table, the list of scripts, the "Does" line. |
| `ui/tests/test_feedback.py` | Tests for the server rules: deleting, editing, the delayed email, the email's content, margin fields. |

`ui/tests/test_feedback.py` covers the server rules: only a note's author can delete it (403 for others, 404 the second time) and `GET /notes` marks it `mine` for them alone; a deleted note is gone from `GET /notes` and the lab's list but kept in `notes.jsonl` with a delete line after it; only the author can edit a note (400 for empty text, 404 once deleted), the list shows the new text and `edited`, and the earlier text stays in the file before an edit line; a note edited within the delay is mailed with its new text; a note deleted within the delay is not mailed, while a kept note and a form entry are; the email's subject, text and link (`#note=<id>` for notes), with `msg` and `err` dropped from the page and another site's address turned into `/`; nothing is sent without the email settings; `side` and `gap` round-trip, an unknown side is stored as `""` and `gap` is clamped to 0..4000. Where the notes script places a note on the page is not unit-tested.

## Email setup (once, by hand)

1. The channel `#amber-feedback` exists. Its email address (channel details, Integrations, Send emails to this channel) is a secret. Channel email needs a paid Slack plan, and admins can turn it off or limit senders by domain.
2. Pick the Workspace account the mail is sent from (best a dedicated one, not a person's). Turn on 2-step verification for it and make an app password (Google account, Security, App passwords).
3. On Render, set `AIAB_FEEDBACK_EMAIL_TO` (the channel address), `AIAB_SMTP_HOST=smtp.gmail.com`, `AIAB_SMTP_USER` (the account) and `AIAB_SMTP_PASSWORD` (the app password), then deploy (restarts do not apply env vars). `AIAB_SMTP_PORT` defaults to 587; 465 uses implicit TLS. `AIAB_FEEDBACK_EMAIL_FROM` defaults to the account.
4. Test by sending the Feedback form from the site: it is mailed at once. Render's free instances block outgoing SMTP; the UI services are on a paid instance type.

## Not built

- Editing or deleting by the lab (it resolves instead). Editing a note's name or moving a note.
- Replies under a note (planned as a follow-up).
- Notes on the nav bar (planned as a follow-up, as a small pin on the bar that opens the note).
- A way for the lab to see resolved notes on a page other than through the list's link.
- Remembering the name typed last in this browser, so it does not have to be typed each time.
- Notes inside the builder iframe.
- A Slack app or webhook, ntfy.
- Removing a note from Slack when it is deleted after its email went out.
- Screenshots or attachments.
- Rate limiting. The form and notes are behind the login and the files are append-only.
