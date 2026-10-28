# Recipients, threads and subjectless records (WR-000440)

Store-only study, 2026-10-28, card WR440.S2.J1. Outlook UI ground truth was not available: background
control could not select the TEST account, full-screen control was declined, and the coordinator then
withdrew the UI turn. No mail was sent. Every count below comes from private research copies on Studio,
decoded by `hxprobe db` from this branch. No mailbox values are recorded here.

Main copy: `snap-20261027T213027-629673000Z` (1,925 message rows, 604 message objects). The 2026-10-26
and 2026-10-28 copies give the same results within a few messages.

## Layout

`hxstore-atlas.json` holds the offsets. In short:

- **Message object, class `00c9`, envelope tag `060f`.** The fixed part holds
  `(offset, length | 0x80000000)` descriptor pairs at fixed object offsets. The values come after a
  variable-length identity string, so the value base is found from the ItemClass descriptor, whose value
  begins `IPM.`. Located fields: ConversationIndex, In-Reply-To, InternetMessageId, ItemClass, Topic,
  sender address and subject.
- **Recipient object, class `0055`, envelope tag `015e`.** There is one object per recipient. A typed
  reference at +10 to +46 gives the recipient's own key, its kind and the key of the message that owns it.
  It holds a display name and an address. Kind 204 is To and kind 205 is Cc. Kind 413 appears on 16
  messages; its role is unproven and it is exported with a NULL kind. Within a kind, ascending object
  keys give the displayed order.
- **ConversationIndex.** This is the binary PidTagConversationIndex: 0x01, five time bytes, a 16-byte
  GUID, then five bytes per reply level. Its GUID is the thread ID.

`hxprobe db` writes the `message_objects` and `recipients` tables and a `threads` view. They join to
`messages` through `message_keys`. The intake reads none of them yet.

## Recipients: To and Cc

**How truth was established.** When Outlook writes a reply, it quotes the parent's header
(`From: … Sent: … To: … Cc: … Subject:`) in the reply body. That quote is Outlook's own rendering of the
parent's recipient lists, in order. To pair each reply with its parent:

1. The reply row must link to exactly one message object.
2. That object's In-Reply-To must resolve to exactly one parent object through the parent's
   InternetMessageId.
3. The quoted From address must equal the parent object's sender.
4. The parent must have recipient objects.

Then the decoded To and Cc address lists are compared with the quoted lists, entry by entry and in
order.

| Population (2026-10-27 copy) | Count |
| --- | --- |
| Rows whose body quotes an Outlook header | 452 |
| Excluded: parent not resolvable | 136 |
| Excluded: quoted From is not the parent's sender, so the quote is from another message | 31 |
| Excluded: row links to more than one key | 10 |
| Excluded: parent has no recipient objects | 5 |
| Validated reply–parent pairs | 270 (155 distinct parents) |

| Field | Exact ordered list match | Rate |
| --- | --- | --- |
| To | 268 / 270 | 99.3 % |
| Cc | 267 / 270 | 98.9 % |
| To and Cc both | 265 / 270 | 98.1 % |
| Distinct parents, all quotes agree | 152 / 155 | 98.1 % |
| To, per position | 385 / 391 | 98.5 % |
| Cc, per position | 364 / 372 | 97.8 % |

There were no order errors. In every pair where the two address sets matched, key order matched the
quoted order. All five mismatches come from three parent messages. In each, the decoded list contains
the quoted addresses in the same order plus extra addresses that the quote omits. The cause is not
established.

TEST-account items from the 2026-10-21 campaign, whose operations were UI-verified at the time, match
4 of 4:

- Two received welcome emails: To is the TEST address.
- The unsent reply draft: To is the vendor address.
- The subject-only draft: no recipients.

**Bcc is unproven.** No third recipient kind appears in any of the ten copies examined, so there is no
evidence of how Bcc is stored. Proving it needs a TEST-account message that has a Bcc recipient.

## Threads

Every message object has a valid ConversationIndex: 604 of 604. Each object also holds a typed reference
(kind 322) to a class `00bf` object. One `00bf` object exists per conversation, and it holds the same
GUID. That reference is Outlook's own conversation membership, so it is compared here with the exported
`conversation_id`.

| Copy | Message objects grouped identically | Threads | Split | Merged |
| --- | --- | --- | --- | --- |
| 2026-10-26 14:19Z | 564 / 564 | 247 | 0 | 0 |
| 2026-10-27 21:30Z | 604 / 604 | 247 (89 with more than one message) | 0 | 0 |
| 2026-10-28 13:03Z | 617 / 617 | 258 | 0 | 0 |

On the TEST account, the reply draft carries its parent's GUID and a 27-byte index, which is one reply
level. Its In-Reply-To names the parent. The second welcome email has identical sender, subject and
preview, but it keeps its own thread. The `threads` view gives each conversation's message count and
latest message: the latest send time, then reply depth.

The grouping has not been compared with Outlook's on-screen conversation view.

## Subjectless records

On the 2026-10-27 copy, 596 of 1,925 rows have no subject of their own. Of those, 469 have no subject
and 127 have one inherited from the conversation.

| Outcome | Rows |
| --- | --- |
| Subject recovered from the row's own message object | 582 |
| No stored subject, but a thread link (`conversation_id`) | 10 |
| Not recoverable: the row links to no message object | 4 |

That is 592 of 596 recovered (99.3 %).

To check the recovered subjects, the object subject was compared with the subject in the quoted header,
across the same validated reply–parent pairs. 266 of 275 match exactly as a prefix. The other 9 cases:

- 5 quotes are truncated.
- 2 have a mail-gateway tag added in front.
- 2 differ only in the reply prefix.

The existing heuristic subject on `messages` agreed with the quote in 125 of 500 own-subject comparisons.
Moving `messages.subject` to the object subject is left for a later PR, because it changes intake input.

## Not covered

- No Outlook UI comparison for any field.
- Bcc storage.
- The role of kind 413.
- A thread's "latest state" beyond its latest message.
- How these values behave across moves and store rebuilds. WR-000502 records the same limits for the
  message key.
