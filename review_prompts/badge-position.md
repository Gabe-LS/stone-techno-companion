# Fix spec: unread badge pill must be the FIRST right-side element in every row

You are an IMPLEMENTATION agent. Tools: Read, Glob, Grep, Edit, Write. You CANNOT
run anything -- the orchestrator executes all verification. Implement exactly
this spec; flag concerns in your final report instead of deviating.

## Change

File: `server/chat/chat.html` only.

In every sidebar/menu row that renders the unread count pill (`.room-badge`
containing `.unread-badge`), the badge must become the FIRST element of the
right-side element group -- i.e. it must appear BEFORE the members icon
(`.room-member-icon`) in room rows and BEFORE the country flag
(`.member-flag`) in DM rows. Concretely:

1. `_renderRoomItemsHtml()` (~line 1631): current order is
   `room-info | room-member-icon | room-badge | bellHtml`.
   New order: `room-info | room-badge | room-member-icon | bellHtml`.
2. DM rows in `loadDMs()` (~line 1865): current order is
   `avatar | member-name | member-flag | room-badge`.
   New order: `avatar | member-name | room-badge | member-flag`.
3. Search the WHOLE file for OTHER templates rendering `.room-badge` or
   `.unread-badge` in rows (there are desktop/menu variants around line
   2650-2690, and meetup rows in the meetups list ~line 1804). Apply the same
   rule everywhere: badge first among the right-side elements. Meetup rows
   (`room-info | room-badge | bell`) already satisfy the rule -- leave them
   unchanged unless you find a variant that does not.

## CSS caution (this is the part that can silently break layout)

These rows are flex containers. Right-side grouping is typically achieved with
`margin-left: auto` on the first right-side element. Check the CSS for
`.room-member-icon`, `.room-badge`, `.member-flag` (and the row containers):
if the auto-margin (or equivalent spacer) lives on an element you are moving
the badge in front of, MOVE that auto-margin to `.room-badge`... but note the
badge is often EMPTY (no unread): an empty span carrying `margin-left: auto`
still works as the flex spacer, but verify the empty badge span has no
padding/min-width/gap that would add visible dead space when empty. Adjust the
CSS minimally so that:
- with unread > 0: pill renders first in the right group, correct spacing;
- with unread == 0: layout is IDENTICAL to today (no phantom gap).
State in your report exactly which CSS rules you touched and why, or why none
were needed.

## Hard rules

- Do not change any JS logic (only markup order and, if needed, CSS).
- `updateBadge()` locates the pill via `item.querySelector('.room-badge')` --
  the reorder must not break that (it will not, unless you rename classes; do
  not rename anything).
- No emojis. Match surrounding style.

## Required final report

```
# Implementation report
## Changes
- <each template changed, with before/after element order>
## CSS touched
- <rules changed and why, or "none needed" with the reason>
## Deviations / concerns
- <none, or details>
```
