-- 0608: V5-J303 client-shared Tours -- one explicit, default-deny client
-- field allowlist.
--
-- Joe's ruling (decision 4ab3933e, 2026-09-24): a client sees exactly what is
-- on today's Tour PDF -- property name, address, suite, space type, size,
-- asking economics, availability and parking. Notes, owner contacts, access
-- notes and every other field stay internal. Source and caveats stay in the
-- internal promotion receipt, not in client output.
--
-- Before this migration the only list was the fact-shape list in
-- ops.tour_public_value_safe and the tour_public_projection_fact check
-- constraint, which also admit access, photos, floor_plan,
-- source_attribution, as_of and caveat. That list says which values are
-- SAFE TO STORE; it was never a statement of what a client may SEE.
--
-- What this adds:
--   1. ops.tour_client_field_keys() -- the allowlist, as one literal array.
--      A field is internal until it is added here (and to
--      CLIENT_TOUR_FIELD_KEYS in mcp-server/src/tour-operations-contract.js,
--      which test/tour-client-share-allowlist.test.mjs binds to this text).
--   2. A BEFORE INSERT trigger on ops.tour_public_projection_fact, so no path
--      -- the seal function or a direct insert -- can put a non-allowlisted
--      fact, an unsafe value (5) or a stop without a client marker (6) into a
--      projection. It is named to fire before the existing
--      tour_projection_fact_guard, so the refusal names the real reason: the
--      field and the rule it broke (never the value).
--   3. ops.read_tour_share_packet and ops.read_tour_packet_for_render select
--      only the eight client columns and no longer name a per-property caveat
--      column. The packet-level 'caveat' key stays an explicit null exactly
--      as 0586 left it.
--   4. ops.tour_public_value_safe: one three-valued-logic fix. For size and
--      asking_economics the 0427 body ends with `and not (<min is number> and
--      <max is number> and min > max)`. When the value has no min/max -- the
--      ordinary {"value":4200,"unit":"SF"} -- that is `not NULL`, NULL, so no
--      size or asking-economics fact without BOTH a numeric min and max could
--      ever be sealed. The range test is now one CASE branch of
--      ops.tour_client_value_violation (5), where a NULL test simply does not
--      fire.
--   5. Client VALUE safety. ops.tour_client_text_violation() is the one value
--      rule for the eight client fields and returns the name of the first
--      rule a text breaks. It first folds the text (NFKC, and the Unicode
--      hyphen U+2010/U+2011 read as "-") and applies a CHARACTER ALLOWLIST:
--      after NFKC a value may hold only printable ASCII, the section,
--      degree and plus-minus signs, Latin-1 and Latin Extended-A letters with
--      the multiplication sign, the en and em dash, curly quotes, the bullet
--      and the ellipsis; anything else (direction overrides, invisible and
--      default-ignorable characters, combining marks, other scripts' digits)
--      is refused as "character:U+XXXX at position N", naming the first
--      such code point and its 1-based position in the value.
--      The stored value is checked first against the same set plus the
--      Unicode spaces and full-width ASCII, the only characters NFKC is
--      trusted to fold, so a character newer than PostgreSQL's Unicode
--      version cannot fold differently here and in JavaScript.
--      It then collapses spaces and trims -- the text a browser shows and the
--      PDF prints -- and applies: email (a dotted domain after an @, a word
--      against an @, (at)/[at] before a domain), url (including .realty,
--      .health, .co and the other listed endings, a spaced .com, and
--      "dot com", ftp://, landlord[.]com, the newer and shortener endings
--      (.xyz, .estate, .in, .to, .ly ...), and ANY dotted name followed by a
--      path such as linktr.ee/bob, except a unit abbreviation before the
--      slash: "$24.00/sq.ft/yr" passes), phone (ten digits however grouped:
--      251 555 01 00, 251 # 555 # 0100, 251@555@0100, any run of up to six
--      non-alphanumerics or x between the groups, (251)5550100; only
--      the 3-3-4 shape joins across wide separators, so "Renovated 2021 -
--      2026 (12 suites)" passes), local_phone (555-0100, 555 - 0100; and
--      any 3-digit and 4-digit group split by up to three
--      non-alphanumerics, spaces included: "Contact 555 0100", "555/0100",
--      unless an area or count unit directly follows ("Office 555 1200
--      SF") or a place word comes before ("Suites 200 1500");
--      a dash range after a suite word whose second number has no leading
--      zero, such as "Suites 100-1200", is set aside; "Unit 555-0100" is
--      not), international_phone (+44 ..., + 44 ..., 011 44 ...),
--      access_code (gate/door/key/entry/alarm/
--      keypad/lock + code/combo/PIN/password, as whole words; or a
--      trigger word (gate, code, PIN, combo, key, entry, access, call box,
--      entrance, password, passcode, security, box, lockbox, keypad, alarm,
--      supra, garage, door, lock; plurals too) before three or more digits,
--      joined by up to six non-alphanumerics or up to two short filler
--      words ("Gate is now 4411"), the digits possibly split by up to three
--      non-alphanumerics other than a comma ("Gate: 44 - 11"). Each trigger
--      has its own plural ("Pines" is not "pin" + "es"). A number directly
--      followed by a measure unit is a measure ("Garage 250 spaces",
--      "Door-to-door 250 ft"); door, unit and dock are places, not units,
--      and a code followed by a count is still a code ("Rear gate 4411; 25
--      spaces"). A preposition or compass word before a street address is
--      not a filler ("Gate at 2200 Airport Blvd"), nor is a money word; a
--      19xx/20xx year followed by a building-event word is a year; a bare year
--      is a code except after a code-book name ("Building code 2021"); a
--      date and exactly "24/7" are not codes; "area code" passes
--      only before exactly three plain digits and "zip code" only before a
--      real zip), lockbox,
--      internal_note, too_long (120), control_character, empty. The rules
--      are whole-word and number-aware so ordinary listing text ("Westgate
--      Pines", "4,200 RSF @ $28.50/SF", "Fire alarm system upgraded 2025",
--      "Suites 101-1050") passes; a shared corpus
--      (mcp-server/test/fixtures/tour-client-text-corpus.json) is run through
--      this function and its JavaScript copy (mcp-server/src/
--      tour-client-value-safety.js) alike, and a sweep of every BMP code
--      point and a sample of the astral planes proves the two agree.
--      Documented residuals, left to the human review before a seal and
--      pinned in the corpus: digits spelled out or swapped for look-alike
--      letters ("two five one five five five zero one zero zero", "251-555-O1OO") and "Suite
--      555-1234".
--      ops.tour_client_value_violation() applies it to a field value,
--      including every part of size / asking_economics, and
--      ops.tour_public_value_safe uses it for the client fields.
--   6. The stop marker. A client sees route_label on every stop; it must be
--      a 1-3 character letter/digit marker (A, B, 12). Route acceptance now
--      refuses any other label (a trigger on ops.tour_property_membership,
--      which only accept_tour_route_version writes), so a label that could
--      never be sealed is refused when the route is accepted, not later.
--   7. Legacy parity. A projection sealed BEFORE this migration may hold a
--      now-internal fact, an unsafe value or a free-text stop label. Rather
--      than each client surface trimming it differently, the whole share
--      fails closed: ops.tour_public_projection_client_safe() is one
--      predicate, and the share list, the PDF render read and the map share
--      all return nothing for a projection that fails it, so the three can
--      never disagree about which stops or values a client sees. The broker
--      reseals a fresh projection, which (2) holds to the rule.
--
-- The existing check constraint is left alone: internal facts may still be
-- recorded; they just cannot be sealed for a client. Signatures and grants of
-- the replaced functions are unchanged; only their bodies change.

create or replace function ops.tour_client_text_max_chars()
returns integer language sql immutable parallel safe as $$ select 120 $$;

create or replace function ops.tour_client_route_label_pattern()
returns text language sql immutable parallel safe as $$ select '^[A-Za-z0-9]{1,3}$'::text $$;

-- CHARACTER ALLOWLIST. After the fold (NFKC, then the Unicode hyphen U+2010
-- read as "-") a client value may hold only printable ASCII, the
-- section/degree/plus-minus signs, Latin-1 and Latin Extended-A letters with
-- the multiplication sign, the en and em dash, curly quotes, the bullet and
-- the ellipsis. The pattern matches the first character OUTSIDE that set.
create or replace function ops.tour_client_text_disallowed_pattern()
returns text language sql immutable parallel safe as $$ select '[^ -~\u00a7\u00b0\u00b1\u00c0-\u00f6\u00f8-\u017f\u2013\u2014\u2018\u2019\u201c\u201d\u2022\u2026]'::text $$;

-- The same set checked on the value AS STORED, before NFKC, plus the only
-- characters the fold is trusted to map into it: the no-break and other
-- Unicode spaces, full-width ASCII (U+FF01-U+FF5E) and the Unicode hyphen and
-- non-breaking hyphen (U+2010, U+2011). Everything else is refused before NFKC
-- runs, so the database (PostgreSQL's Unicode version) and the JavaScript copy
-- (Node's, which is newer) cannot disagree about a character assigned after
-- the older version. The three Latin Extended-A letters whose NFKC form leaves
-- the allowlist (U+013F, U+0140, U+0149) are left out, so a refusal's position
-- is a position in the value as stored.
create or replace function ops.tour_client_text_disallowed_source_pattern()
returns text language sql immutable parallel safe as $$ select '[^ -~\u00a0\u00a7\u00b0\u00b1\u00c0-\u00f6\u00f8-\u013e\u0141-\u0148\u014a-\u017f\u2000-\u200a\u2010\u2011\u2013\u2014\u2018\u2019\u201c\u201d\u2022\u2026\u202f\u205f\u3000\uff01-\uff5e]'::text $$;

-- Before the phone rules read a value, a number shaped 3-3-4 is joined across
-- any run of up to six characters that are not a letter or a digit, and the
-- letter x: a class, not a list (251 # 555 # 0100, 251@555@0100,
-- 251 x 555 x 0100). Six, because the fold makes a doubled ellipsis six dots.
-- A run starting with a comma and a space is a list and does not join
-- ("Suites 201-204, 1200 SF"); 251,555,0100 and 251 , 555 , 0100 do. A
-- 3-3-4 group followed by a unit is a measure ("Suites 250 - 300 - 4500 SF").
-- Only
-- that shape joins across wide separators, so year and count ranges
-- ("Renovated 2021 - 2026 (12 suites)") and thousands (120,000 SF) do not.
create or replace function ops.tour_client_text_phone_join_pattern()
returns text language sql immutable parallel safe as $$ select '(^|[^0-9])([0-9]{3})(?!, )[^A-WYZa-wyz0-9]{0,6}([0-9]{3})(?!, )[^A-WYZa-wyz0-9]{0,6}([0-9]{4})(?![0-9])(?![ -]?(([pP][aA][rR][kK][iI][nN][gG]|[cC][oO][vV][eE][rR][eE][dD]|[sS][uU][rR][fF][aA][cC][eE]|[rR][eE][sS][eE][rR][vV][eE][dD]) )?([sS][fF]|[rR][sS][fF]|[uU][sS][fF]|[sS][qQ]|[sS][qQ][fF][tT]|[sS][qQ][uU][aA][rR][eE]|[fF][tT]|[fF][eE][eE][tT]|[fF][oO][oO][tT]|[sS][pP][aA][cC][eE][sS]?|[sS][tT][aA][lL][lL][sS]?|[aA][cC][rR][eE][sS]?|[aA][cC]|[sS][eE][aA][tT][sS]?|[pP][sS][fF]|[mM][oO]|[yY][rR]|[hH][oO][uU][rR][sS]|[hH][rR][sS])([^A-Za-z]|$))'::text $$;

-- Then digits separated by one or two of space . and a dash are joined
-- (251 555 01 00, 1-251-555-0100).
create or replace function ops.tour_client_text_digit_join_pattern()
returns text language sql immutable parallel safe as $$ select '([0-9])[ .\u2013\u2014-]{1,2}(?=[0-9])'::text $$;

-- A suite/unit/room RANGE is set aside before the seven-digit local-number
-- rule: two numbers of 1-4 digits joined by a dash, the second without a
-- leading zero. A dot or a leading-zero second number is not a range, so
-- "Unit 555-0100" and "Ste #555.0100" are still read as phones.
create or replace function ops.tour_client_text_suite_range_pattern()
returns text language sql immutable parallel safe as $$
  select '(^|[^A-Za-z])(suites?|ste|units?|rooms?)[.:#]?[ ]?#?[0-9]{1,4}[ ]?[\u2013\u2014-][ ]?[1-9][0-9]{0,3}(?![0-9])'::text
$$;

-- Case-insensitive, checked in ordinal order; the first match names the
-- refusal. target: raw (normalized value), digits (digit groups joined),
-- nosuite (suite ranges set aside).
create or replace function ops.tour_client_text_rules()
returns table(ordinal integer, rule text, target text, pattern text)
language sql immutable parallel safe as $$
  values
    (1, 'email', 'raw', '[A-Za-z0-9._%+-] ?@ ?[A-Za-z0-9_-]+([.][A-Za-z0-9_-]+)*[.][A-Za-z]{2,}|[A-Za-z0-9._%+-]( @|@ ?)[A-Za-z0-9_-]*[A-Za-z]|[A-Za-z0-9._%+-] ?[(\[] ?at ?[)\]] ?[A-Za-z0-9_-]+([.][A-Za-z0-9_-]+)*[.][A-Za-z]{2,}'),
    (2, 'url', 'raw', '(https?|ftp)://|www[.]|[A-Za-z0-9-]*[A-Za-z][A-Za-z0-9-]*[.](com|net|org|io|biz|info|us|co|ai|app|realty|health|properties|homes|law|care|clinic|gov|edu|me|xyz|site|online|link|page|tv|ca|uk|de|estate|land|group|llc|pro|ee|to|in|at|do|id|ly|gl|gd|cc|gy|be|realtor|realestate|broker|house|rentals|apartments|property|agency|bio|live|shop|club|tech|one|mx)([^A-Za-z0-9]|$)|[A-Za-z0-9-]*[A-Za-z][A-Za-z0-9-]* ?[.] ?(com|net|org)([^A-Za-z0-9]|$)|[A-Za-z0-9-]*[A-Za-z][A-Za-z0-9-]* [.] ?(com|net|org|io|biz|info|us|co|ai|app|realty|health|properties|homes|law|care|clinic|gov|edu|me|xyz|site|online|link|page|tv|ca|uk|de|estate|land|group|llc|pro|ee|to|in|at|do|id|ly|gl|gd|cc|gy|be|realtor|realestate|broker|house|rentals|apartments|property|agency|bio|live|shop|club|tech|one|mx)([^A-Za-z0-9]|$)|(^|[^A-Za-z])[(\[]? ?dot ?[)\]]? ?(com|net|org|co)([^A-Za-z]|$)|[A-Za-z0-9-]*[A-Za-z][A-Za-z0-9-]* ?[(\[] ?[.] ?[)\]] ?[A-Za-z]{2,}|[A-Za-z0-9-]*[A-Za-z][A-Za-z0-9-]*[.](?!(ft|yr|mo|sf|ac|mi|yd)/)[A-Za-z]{2,}/[A-Za-z0-9]'),
    (3, 'phone', 'digits', '(^|[^0-9])1?[2-9][0-9]{9}([^0-9]|$)'),
    (4, 'local_phone', 'nosuite', '(^|[^0-9])[2-9][0-9]{2} ?[.\u2013\u2014-] ?[0-9]{4}([^0-9]|$)|(^|[^0-9$])(?<!(^|[^A-Za-z])(suites?|ste|bldgs?|building|units?|floors?|lots?|hwy|exit|route)[^A-Za-z0-9]{1,3})[0-9]{3}(?![^A-Za-z0-9]{0,2},)[^A-Za-z0-9]{0,3}[0-9]{4}(?![0-9])(?![ -]?(([pP][aA][rR][kK][iI][nN][gG]|[cC][oO][vV][eE][rR][eE][dD]|[sS][uU][rR][fF][aA][cC][eE]|[rR][eE][sS][eE][rR][vV][eE][dD]) )?([sS][fF]|[rR][sS][fF]|[uU][sS][fF]|[sS][qQ]|[sS][qQ][fF][tT]|[sS][qQ][uU][aA][rR][eE]|[fF][tT]|[fF][eE][eE][tT]|[fF][oO][oO][tT]|[sS][pP][aA][cC][eE][sS]?|[sS][tT][aA][lL][lL][sS]?|[aA][cC][rR][eE][sS]?|[aA][cC]|[sS][eE][aA][tT][sS]?)([^A-Za-z]|$))'),
    (5, 'international_phone', 'digits', '[+] ?[0-9]{8,}|(^|[^0-9])(011|00)[1-9][0-9]{6,}([^0-9]|$)'),
    (6, 'access_code', 'raw', '(^|[^A-Za-z])(gate|door|key|entry|garage|alarm|keypad|access|lock)[ -]?(codes?|combos?|combination|pins?|passwords?)([^A-Za-z]|$)|(^|[^A-Za-z])(gates?|pins?|combos?|combinations?|keys?|entry|entries|access(es)?|call ?box(es)?|entrances?|passwords?|passcodes?|security|securities|lock ?box(es)?|box(es)?|key ?pads?|key-pads?|key ?safes?|padlocks?|fobs?|buzzers?|intercoms?|sentri ?locks?|alarms?|supras?|garages?|doors?|locks?)([^A-Za-z0-9]{1,6}(number|(?!(suites?|ste|units?|bldgs?|floors?|rooms?|levels?|lots?|bays?|phase|pads?|hwy|exit|route|rt|road|i|us|sr|cr|st|ave|blvd|dr|rd|ln|way|pkwy|miles?|dep|rent|fee|fees|month|price|cost)[^A-Za-z0-9])(?!(at|from|to|on|off|near|via|faces|west|east|north|south)[^A-Za-z0-9]+[0-9]+( [A-Za-z]+){0,3} (st|street|ave|avenue|blvd|rd|road|dr|drive|hwy|highway|pkwy|parkway|ln|lane|way|ct|court|pl|place|block)([^A-Za-z]|$))[A-Za-z]{1,5})){0,2}[^A-Za-z0-9]{0,6}(?!(19|20)[0-9]{2}[^A-Za-z0-9]{1,3}(upgrad|renovat|remodel|retrofit|replac|install|updat|built|build|construct|lease|deliver|complet|edition|standard|complian|expan|refresh|addition|rebuil|conver|inspect|certif|vintage|budget|sign))(?!24/7([^0-9]|$))(?![0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}([^0-9]|$))(?![0-9]+[ -]?(([pP][aA][rR][kK][iI][nN][gG]|[cC][oO][vV][eE][rR][eE][dD]|[sS][uU][rR][fF][aA][cC][eE]|[rR][eE][sS][eE][rR][vV][eE][dD]) )?([sS][fF]|[rR][sS][fF]|[uU][sS][fF]|[sS][qQ]|[sS][qQ][fF][tT]|[sS][qQ][uU][aA][rR][eE]|[fF][tT]|[fF][eE][eE][tT]|[fF][oO][oO][tT]|[sS][pP][aA][cC][eE][sS]?|[sS][tT][aA][lL][lL][sS]?|[aA][cC][rR][eE][sS]?|[aA][cC]|[sS][eE][aA][tT][sS]?|[pP][sS][fF]|[mM][oO]|[yY][rR]|[hH][oO][uU][rR][sS]|[hH][rR][sS])([^A-Za-z]|$))[0-9]([^A-Za-z0-9,]{0,3}[0-9]){2,}|(^|[^A-Za-z])(?<!zip )(?<!zip-)(?<!area )(?<!area-)(?<!building )(?<!fire )(?<!electrical )(?<!plumbing )(?<!mechanical )(?<!energy )(?<!zoning )(?<!safety )(?<!health )codes?([^A-Za-z0-9]{1,6}(number|(?!(suites?|ste|units?|bldgs?|floors?|rooms?|levels?|lots?|bays?|phase|pads?|hwy|exit|route|rt|road|i|us|sr|cr|st|ave|blvd|dr|rd|ln|way|pkwy|miles?|dep|rent|fee|fees|month|price|cost)[^A-Za-z0-9])(?!(at|from|to|on|off|near|via|faces|west|east|north|south)[^A-Za-z0-9]+[0-9]+( [A-Za-z]+){0,3} (st|street|ave|avenue|blvd|rd|road|dr|drive|hwy|highway|pkwy|parkway|ln|lane|way|ct|court|pl|place|block)([^A-Za-z]|$))[A-Za-z]{1,5})){0,2}[^A-Za-z0-9]{0,6}(?!(19|20)[0-9]{2}[^A-Za-z0-9]{1,3}(upgrad|renovat|remodel|retrofit|replac|install|updat|built|build|construct|lease|deliver|complet|edition|standard|complian|expan|refresh|addition|rebuil|conver|inspect|certif|vintage|budget|sign))(?!24/7([^0-9]|$))(?![0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}([^0-9]|$))(?![0-9]+[ -]?(([pP][aA][rR][kK][iI][nN][gG]|[cC][oO][vV][eE][rR][eE][dD]|[sS][uU][rR][fF][aA][cC][eE]|[rR][eE][sS][eE][rR][vV][eE][dD]) )?([sS][fF]|[rR][sS][fF]|[uU][sS][fF]|[sS][qQ]|[sS][qQ][fF][tT]|[sS][qQ][uU][aA][rR][eE]|[fF][tT]|[fF][eE][eE][tT]|[fF][oO][oO][tT]|[sS][pP][aA][cC][eE][sS]?|[sS][tT][aA][lL][lL][sS]?|[aA][cC][rR][eE][sS]?|[aA][cC]|[sS][eE][aA][tT][sS]?|[pP][sS][fF]|[mM][oO]|[yY][rR]|[hH][oO][uU][rR][sS]|[hH][rR][sS])([^A-Za-z]|$))[0-9]([^A-Za-z0-9,]{0,3}[0-9]){2,}|(^|[^A-Za-z])(building|fire|electrical|plumbing|mechanical|energy|zoning|safety|health) codes?([^A-Za-z0-9]{1,6}(number|(?!(suites?|ste|units?|bldgs?|floors?|rooms?|levels?|lots?|bays?|phase|pads?|hwy|exit|route|rt|road|i|us|sr|cr|st|ave|blvd|dr|rd|ln|way|pkwy|miles?|dep|rent|fee|fees|month|price|cost)[^A-Za-z0-9])(?!(at|from|to|on|off|near|via|faces|west|east|north|south)[^A-Za-z0-9]+[0-9]+( [A-Za-z]+){0,3} (st|street|ave|avenue|blvd|rd|road|dr|drive|hwy|highway|pkwy|parkway|ln|lane|way|ct|court|pl|place|block)([^A-Za-z]|$))[A-Za-z]{1,5})){0,2}[^A-Za-z0-9]{0,6}(?!(19|20)[0-9]{2}([^0-9]|$))(?!(19|20)[0-9]{2}[^A-Za-z0-9]{1,3}(upgrad|renovat|remodel|retrofit|replac|install|updat|built|build|construct|lease|deliver|complet|edition|standard|complian|expan|refresh|addition|rebuil|conver|inspect|certif|vintage|budget|sign))(?!24/7([^0-9]|$))(?![0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}([^0-9]|$))(?![0-9]+[ -]?(([pP][aA][rR][kK][iI][nN][gG]|[cC][oO][vV][eE][rR][eE][dD]|[sS][uU][rR][fF][aA][cC][eE]|[rR][eE][sS][eE][rR][vV][eE][dD]) )?([sS][fF]|[rR][sS][fF]|[uU][sS][fF]|[sS][qQ]|[sS][qQ][fF][tT]|[sS][qQ][uU][aA][rR][eE]|[fF][tT]|[fF][eE][eE][tT]|[fF][oO][oO][tT]|[sS][pP][aA][cC][eE][sS]?|[sS][tT][aA][lL][lL][sS]?|[aA][cC][rR][eE][sS]?|[aA][cC]|[sS][eE][aA][tT][sS]?|[pP][sS][fF]|[mM][oO]|[yY][rR]|[hH][oO][uU][rR][sS]|[hH][rR][sS])([^A-Za-z]|$))[0-9]([^A-Za-z0-9,]{0,3}[0-9]){2,}|(^|[^A-Za-z])zip[ -]?codes?([^A-Za-z0-9]{1,6}(number|(?!(suites?|ste|units?|bldgs?|floors?|rooms?|levels?|lots?|bays?|phase|pads?|hwy|exit|route|rt|road|i|us|sr|cr|st|ave|blvd|dr|rd|ln|way|pkwy|miles?|dep|rent|fee|fees|month|price|cost)[^A-Za-z0-9])(?!(at|from|to|on|off|near|via|faces|west|east|north|south)[^A-Za-z0-9]+[0-9]+( [A-Za-z]+){0,3} (st|street|ave|avenue|blvd|rd|road|dr|drive|hwy|highway|pkwy|parkway|ln|lane|way|ct|court|pl|place|block)([^A-Za-z]|$))[A-Za-z]{1,5})){0,2}[^A-Za-z0-9]{0,6}(?![0-9]{5}(-[0-9]{4})?([^0-9]|$))[0-9]([^A-Za-z0-9,]{0,3}[0-9]){2,}|(^|[^A-Za-z])area[ -]?codes?([^A-Za-z0-9]{1,6}(number|(?!(suites?|ste|units?|bldgs?|floors?|rooms?|levels?|lots?|bays?|phase|pads?|hwy|exit|route|rt|road|i|us|sr|cr|st|ave|blvd|dr|rd|ln|way|pkwy|miles?|dep|rent|fee|fees|month|price|cost)[^A-Za-z0-9])(?!(at|from|to|on|off|near|via|faces|west|east|north|south)[^A-Za-z0-9]+[0-9]+( [A-Za-z]+){0,3} (st|street|ave|avenue|blvd|rd|road|dr|drive|hwy|highway|pkwy|parkway|ln|lane|way|ct|court|pl|place|block)([^A-Za-z]|$))[A-Za-z]{1,5})){0,2}[^A-Za-z0-9]{0,6}([0-9]{4}|[0-9]{1,3}[^A-Za-z0-9][0-9])'),
    (7, 'lockbox', 'raw', '(^|[^A-Za-z])(lock[ -]?box(es)?|passcodes?)([^A-Za-z]|$)'),
    (8, 'internal_note', 'raw', '(^|[^A-Za-z])(internal[ -]?(notes?|only|use)|confidential|do not (share|disclose)|broker[ -]only|not for (the )?clients?)([^A-Za-z]|$)')
$$;

-- The fold: NFKC, then the Unicode hyphen (U+2010, which NFKC also makes of
-- the non-breaking hyphen U+2011) read as the ASCII hyphen.
create or replace function ops.tour_client_text_fold(p_text text)
returns text language sql immutable parallel safe as $$
  select replace(normalize(p_text, NFKC), chr(8208), '-')
$$;

-- The text a client reads: the fold, space runs collapsed, ends trimmed.
-- Only meaningful for a value the allowlist admits.
create or replace function ops.tour_client_text_normalize(p_text text)
returns text language sql immutable parallel safe as $$
  select regexp_replace(regexp_replace(ops.tour_client_text_fold(p_text), ' +', ' ', 'g'), '^ | $', '', 'g')
$$;

create or replace function ops.tour_client_text_violation(p_text text)
returns text language sql immutable parallel safe as $$
  select case
    when p_text is null then 'not_text'
    -- C0 and C1 controls and DEL, as explicit code points: [[:cntrl:]]
    -- follows the database locale, and the JavaScript copy has none.
    when p_text ~ '[\u0001-\u001f\u007f-\u009f]' then 'control_character'
    else (
      select case
        -- the first character outside the allowlist, with its 1-based
        -- position: in the value as stored, or (for a character only the
        -- fold produces) in the folded text
        when c.sb is not null then 'character:U+' || upper(lpad(to_hex(ascii(c.sb)), greatest(4, length(to_hex(ascii(c.sb)))), '0')) || ' at position ' || strpos(p_text, c.sb)
        when c.fb is not null then 'character:U+' || upper(lpad(to_hex(ascii(c.fb)), greatest(4, length(to_hex(ascii(c.fb)))), '0')) || ' at position ' || strpos(c.f, c.fb)
        when n.t = '' then 'empty'
        when char_length(n.t) > ops.tour_client_text_max_chars() then 'too_long'
        else (
          select r.rule
            from ops.tour_client_text_rules() r
           where (case r.target
                    when 'digits' then regexp_replace(regexp_replace(n.t, ops.tour_client_text_phone_join_pattern(), '\1\2\3\4', 'g'),
                                                    ops.tour_client_text_digit_join_pattern(), '\1', 'g')
                    when 'nosuite' then regexp_replace(n.t, ops.tour_client_text_suite_range_pattern(), '\1 ', 'gi')
                    else n.t
                  end) ~* r.pattern
           order by r.ordinal
           limit 1)
      end
      from (select substring(p_text from ops.tour_client_text_disallowed_source_pattern()) sb,
                   substring(f.f from ops.tour_client_text_disallowed_pattern()) fb, f.f
              from (select ops.tour_client_text_fold(p_text) f) f) c,
           (select ops.tour_client_text_normalize(p_text) t) n)
  end
$$;

create or replace function ops.tour_client_text_safe(p_text text)
returns boolean language sql immutable parallel safe as $$
  select ops.tour_client_text_violation(p_text) is null
$$;

create or replace function ops.tour_client_field_keys()
returns text[] language sql immutable parallel safe as $$
  select array['display.name','display.address','suite','property_type','size','asking_economics','availability','parking']::text[]
$$;

create or replace function ops.tour_client_field_allowed(p_field_key text)
returns boolean language sql immutable parallel safe as $$
  select coalesce(p_field_key = any(ops.tour_client_field_keys()), false)
$$;

-- The reason a client may not see `p_value` under `p_field_key`, or null.
-- A metric (size, asking_economics) is refused whole when any part fails;
-- the reason names the part (size.label: phone).
create or replace function ops.tour_client_value_violation(p_field_key text, p_value jsonb)
returns text language sql immutable parallel safe as $$
  select case
    when not ops.tour_client_field_allowed(p_field_key) then 'field_not_client_allowlisted'
    when p_value is null then 'missing'
    when p_field_key in ('size','asking_economics') then (
      case
        when jsonb_typeof(p_value) <> 'object' or not (p_value ? 'value' or p_value ? 'min' or p_value ? 'max') then 'metric_shape'
        when exists (select 1 from jsonb_object_keys(p_value) k where k not in ('value','unit','min','max','currency','period','label')) then 'metric_shape'
        when jsonb_typeof(p_value->'min')='number' and jsonb_typeof(p_value->'max')='number'
          and (p_value->>'min')::numeric > (p_value->>'max')::numeric then 'metric_range'
        else (
          select e.key || '.' || v.violation
            from jsonb_each(p_value) e
           cross join lateral (select case
                   when e.key in ('value','min','max') and jsonb_typeof(e.value) = 'number' then null
                   when jsonb_typeof(e.value) <> 'string' then 'not_text'
                   else ops.tour_client_text_violation(e.value #>> '{}')
                 end violation) v
           where v.violation is not null
           order by e.key
           limit 1)
      end)
    when jsonb_typeof(p_value) <> 'string' then 'not_text'
    else ops.tour_client_text_violation(p_value #>> '{}')
  end
$$;

create or replace function ops.tour_public_value_safe(p_field_key text, p_value jsonb)
returns boolean language sql immutable as $$
  select case
    when p_field_key in ('display.name','display.address','suite','property_type','size','asking_economics','availability','parking') then
      ops.tour_client_value_violation(p_field_key, p_value) is null
    when p_field_key in ('access','source_attribution','as_of','caveat') then jsonb_typeof(p_value) = 'string'
    when p_field_key in ('photos','floor_plan') then jsonb_typeof(p_value) = 'array' and not exists (
      select 1 from jsonb_array_elements(p_value) item
       where jsonb_typeof(item) <> 'object'
          or not (item ? 'asset_ref')
          or (item->>'asset_ref') !~ '^asset:public:[A-Za-z0-9_-]+$'
          or char_length(item->>'asset_ref') not between 29 and 269
          or exists (select 1 from jsonb_each(item) e where e.key not in ('asset_ref','alt','caption','source') or jsonb_typeof(e.value) <> 'string')
    )
    else false end;
$$;

create or replace function ops.tour_projection_fact_client_allowlist_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_value jsonb; v_violation text;
begin
  if not ops.tour_client_field_allowed(new.display_field_key) then
    raise exception 'projection fact field is not client-allowlisted';
  end if;
  select a.value into v_value from ops.tour_field_assertion a
   where a.organization_tenant_id=new.organization_tenant_id and a.id=new.field_assertion_id;
  if found then
    v_violation := ops.tour_client_value_violation(new.display_field_key, v_value);
    if v_violation is not null then
      raise exception 'projection fact % is not client-safe: %', new.display_field_key, v_violation;
    end if;
  end if;
  if exists (
    select 1 from ops.tour_public_projection p
    join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id
      and m.tour_id=p.tour_id and m.route_version=p.route_version and m.property_id=new.property_id
    where p.organization_tenant_id=new.organization_tenant_id and p.id=new.projection_id
      and m.route_label !~ ops.tour_client_route_label_pattern()
  ) then
    raise exception 'projection stop marker is not a client-safe route label';
  end if;
  return new;
end $$;

drop trigger if exists tour_projection_fact_client_allowlist on ops.tour_public_projection_fact;
create trigger tour_projection_fact_client_allowlist
  before insert on ops.tour_public_projection_fact
  for each row execute function ops.tour_projection_fact_client_allowlist_guard();

-- (6) Route acceptance is the only writer of ops.tour_property_membership,
-- which is append-only (0318), so an insert guard covers every path.
create or replace function ops.tour_property_membership_client_label_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  if new.route_label is null or new.route_label !~ ops.tour_client_route_label_pattern() then
    raise exception 'route stop label must be a 1-3 letter or digit client marker';
  end if;
  return new;
end $$;

drop trigger if exists tour_property_membership_client_label on ops.tour_property_membership;
create trigger tour_property_membership_client_label
  before insert on ops.tour_property_membership
  for each row execute function ops.tour_property_membership_client_label_guard();

-- (7) One predicate for every client surface. True only when every sealed
-- fact is client-allowlisted and client-safe and every stop of the route
-- version carries a client marker.
create or replace function ops.tour_public_projection_client_safe(p_tenant text, p_projection_id uuid)
returns boolean language sql stable set search_path=pg_catalog,ops,public,pg_temp as $$
  select not exists (
      select 1 from ops.tour_public_projection_fact f
      left join ops.tour_field_assertion a on a.organization_tenant_id=f.organization_tenant_id and a.id=f.field_assertion_id
      where f.organization_tenant_id=p_tenant and f.projection_id=p_projection_id
        and (a.id is null or a.field_key is distinct from f.display_field_key
             or ops.tour_client_value_violation(f.display_field_key, a.value) is not null))
    and not exists (
      select 1 from ops.tour_public_projection p
      join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version
      where p.organization_tenant_id=p_tenant and p.id=p_projection_id
        and (m.route_label is null or m.route_label !~ ops.tour_client_route_label_pattern()))
$$;

revoke all on function ops.tour_client_text_max_chars(), ops.tour_client_route_label_pattern(),
  ops.tour_client_text_disallowed_pattern(), ops.tour_client_text_disallowed_source_pattern(),
  ops.tour_client_text_fold(text),
  ops.tour_client_text_normalize(text), ops.tour_client_text_phone_join_pattern(),
  ops.tour_client_text_digit_join_pattern(), ops.tour_client_text_suite_range_pattern(),
  ops.tour_client_text_rules(), ops.tour_client_text_violation(text), ops.tour_client_text_safe(text),
  ops.tour_client_field_keys(), ops.tour_client_field_allowed(text), ops.tour_client_value_violation(text,jsonb),
  ops.tour_projection_fact_client_allowlist_guard(), ops.tour_property_membership_client_label_guard(),
  ops.tour_public_projection_client_safe(text,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create or replace function ops.read_tour_share_packet(p_session_digest text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  with grant_row as (select (ops.tour_share_session_grant(p_session_digest,'view_packet')).*), projection as (
    select p.* from grant_row g join ops.tour_public_projection p on p.organization_tenant_id=g.organization_tenant_id and p.id=g.projection_id
    where p.status='approved' and exists(select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=p.organization_tenant_id and s.projection_id=p.id and s.canonical_projection_digest=p.projection_digest)
      and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null
      and ops.tour_public_projection_client_safe(p.organization_tenant_id,p.id)
  ), stops as (
    select m.route_sequence,m.route_label,'property:public:'||substr(encode(public.digest(p.organization_tenant_id||':'||p.id::text||':'||m.property_id::text,'sha256'),'hex'),1,32) property_ref,
      max(a.value#>>'{}') filter(where f.display_field_key='display.name') name,
      max(a.value#>>'{}') filter(where f.display_field_key='display.address') address,
      max(a.value#>>'{}') filter(where f.display_field_key='suite') suite,
      max(a.value#>>'{}') filter(where f.display_field_key='property_type') property_type,
      (jsonb_agg(a.value order by f.id) filter(where f.display_field_key='size'))->0 size,
      (jsonb_agg(a.value order by f.id) filter(where f.display_field_key='asking_economics'))->0 asking_economics,
      max(a.value#>>'{}') filter(where f.display_field_key='availability') availability,
      max(a.value#>>'{}') filter(where f.display_field_key='parking') parking
    from projection p join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version
    join ops.tour_public_projection_fact f on f.organization_tenant_id=p.organization_tenant_id and f.projection_id=p.id and f.property_id=m.property_id
    join ops.tour_field_assertion a on a.organization_tenant_id=f.organization_tenant_id and a.id=f.field_assertion_id
    group by p.organization_tenant_id,p.id,m.property_id,m.route_sequence,m.route_label
  ) select jsonb_build_object('as_of',p.as_of,'caveat',null,'stops',coalesce((select jsonb_agg(to_jsonb(stops) order by route_sequence) from stops),'[]'::jsonb)) from projection p;
$$;

create or replace function ops.read_tour_packet_for_render(p_tenant text,p_projection_id uuid,p_actor_id text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  with projection as (
    select p.* from ops.tour_public_projection p
    where p.organization_tenant_id=p_tenant and p.id=p_projection_id and nullif(btrim(p_actor_id),'') is not null and p.status='approved'
      and exists(select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=p.organization_tenant_id and s.projection_id=p.id and s.canonical_projection_digest=p.projection_digest)
      and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null
      and ops.tour_public_projection_client_safe(p.organization_tenant_id,p.id)
  ), properties as (
    select m.route_sequence,m.route_label,'property:public:'||substr(encode(public.digest(p.organization_tenant_id||':'||p.id::text||':'||m.property_id::text,'sha256'),'hex'),1,32) property_ref,
      max(a.value#>>'{}') filter(where f.display_field_key='display.name') name,
      max(a.value#>>'{}') filter(where f.display_field_key='display.address') address,
      max(a.value#>>'{}') filter(where f.display_field_key='suite') suite,
      max(a.value#>>'{}') filter(where f.display_field_key='property_type') property_type,
      (jsonb_agg(a.value order by f.id) filter(where f.display_field_key='size'))->0 size,
      (jsonb_agg(a.value order by f.id) filter(where f.display_field_key='asking_economics'))->0 asking_economics,
      max(a.value#>>'{}') filter(where f.display_field_key='availability') availability,
      max(a.value#>>'{}') filter(where f.display_field_key='parking') parking
    from projection p join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version
    join ops.tour_public_projection_fact f on f.organization_tenant_id=p.organization_tenant_id and f.projection_id=p.id and f.property_id=m.property_id
    join ops.tour_field_assertion a on a.organization_tenant_id=f.organization_tenant_id and a.id=f.field_assertion_id
    group by p.organization_tenant_id,p.id,m.property_id,m.route_sequence,m.route_label
  ) select jsonb_build_object(
    'projection_digest',p.projection_digest,
    'packet',jsonb_build_object('as_of',p.as_of,'caveat',null,'properties',coalesce((select jsonb_agg(to_jsonb(properties) order by route_sequence) from properties),'[]'::jsonb))
  ) from projection p;
$$;

-- The client map share (view_map) sends each stop's human-verified entrance /
-- driveway / parking-access coordinate, the opaque property ref and the stop
-- marker -- what a client needs to drive to the stop (map-architecture
-- contract carr-workspace-market-map-route-planning 1.2.0, entrance
-- verification + native-navigation privacy rule). The only change from 0430
-- is the (7) predicate: a legacy projection the list and PDF refuse is
-- refused here too.
create or replace function ops.read_tour_share_map(p_session_digest text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  with grant_row as (select (ops.tour_share_session_grant(p_session_digest,'view_map')).*), projection as (
    select p.* from grant_row g join ops.tour_public_projection p on p.organization_tenant_id=g.organization_tenant_id and p.id=g.projection_id
    where p.status='approved' and exists(select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=p.organization_tenant_id and s.projection_id=p.id and s.canonical_projection_digest=p.projection_digest)
      and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null
      and ops.tour_public_map_projection_ready(p.organization_tenant_id,p.id)
      and ops.tour_public_projection_client_safe(p.organization_tenant_id,p.id)
  ) select jsonb_build_object('as_of',p.as_of,'points',coalesce(jsonb_agg(jsonb_build_object(
    'property_ref','property:public:'||substr(encode(public.digest(p.organization_tenant_id||':'||p.id::text||':'||m.property_id::text,'sha256'),'hex'),1,32),
    'route_sequence',m.route_sequence,'route_label',m.route_label,'latitude',c.latitude::double precision,'longitude',c.longitude::double precision
  ) order by m.route_sequence),'[]'::jsonb))
  from projection p join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version
  join ops.tour_public_projection_map_point mp on mp.organization_tenant_id=p.organization_tenant_id and mp.projection_id=p.id and mp.property_id=m.property_id and mp.route_version=p.route_version
  join ops.tour_coordinate_entrance_verification_receipt er on er.organization_tenant_id=mp.organization_tenant_id and er.id=mp.entrance_verification_receipt_id and er.property_id=mp.property_id and er.coordinate_candidate_id=mp.coordinate_candidate_id
  join ops.tour_property_coordinate_candidate c on c.organization_tenant_id=mp.organization_tenant_id and c.id=mp.coordinate_candidate_id and c.coordinate_role in ('entrance','driveway','parking_access') and c.review_state='reviewed'
  group by p.as_of;
$$;
