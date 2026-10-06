// A GENERATED adversarial corpus for the client text rule (V5-J303).
//
// Hand-picked examples closed one string at a time and left the class around
// them open, round after round. This module sweeps the classes instead: every
// access-code trigger word against every ASCII punctuation mark and the common
// Unicode dashes, quotes and bullets as the connector; the same characters as
// phone separators, spaced and unspaced; and the link forms a reviewer found.
// tour-client-share-allowlist.test.mjs runs every string through the
// JavaScript rule, and the same strings are pinned in the Postgres proof
// (tour-client-share-allowlist-postgres.sql), so both engines must agree on
// each one. The lists are deterministic: regenerating gives the same strings.

// Every printable ASCII character that is not a letter or a digit.
export const ASCII_PUNCTUATION = Array.from({ length: 95 }, (_, i) => String.fromCharCode(32 + i))
  .filter(ch => !/[A-Za-z0-9]/.test(ch));
// The allowlisted Unicode dashes, quotes, bullet, ellipsis and symbols.
export const UNICODE_PUNCTUATION = ["\u2013", "\u2014", "\u2018", "\u2019", "\u201c", "\u201d", "\u2022", "\u2026", "\u00a7", "\u00b0", "\u00b1", "\u00d7"];
const CONNECTORS = [...ASCII_PUNCTUATION, ...UNICODE_PUNCTUATION];

// Each trigger word as a writer would type it, and a plural.
export const ACCESS_TRIGGERS = [
  ["Gate", "Gates"], ["Code", "Codes"], ["PIN", "PINs"], ["Combo", "Combos"], ["Combination", "Combinations"],
  ["Key", "Keys"], ["Entry", "Entries"], ["Access", "Accesses"], ["Callbox", "Callboxes"], ["Call box", "Call boxes"],
  ["Entrance", "Entrances"], ["Password", "Passwords"], ["Passcode", "Passcodes"], ["Security", "Securities"],
  ["Box", "Boxes"], ["Lockbox", "Lockboxes"], ["Lock box", "Lock boxes"], ["Keypad", "Keypads"], ["Alarm", "Alarms"],
  ["Supra", "Supras"], ["Garage", "Garages"], ["Door", "Doors"], ["Lock", "Locks"],
  ["Key pad", "Key pads"], ["Key-pad", "Key-pads"], ["Key safe", "Key safes"], ["Keysafe", "Keysafes"], ["Padlock", "Padlocks"],
  ["Fob", "Fobs"], ["Buzzer", "Buzzers"], ["Intercom", "Intercoms"], ["Sentrilock", "Sentrilocks"], ["Sentri lock", "Sentri locks"],
];

// A realistic Gulf Coast (Mobile / Baldwin / Pensacola / Destin) feature-list
// set, written as listing agents write client-visible fields: size, parking,
// suite, availability, amenities. Written before the round-10 tuning and
// measured against it (14.3% refused on the round-9 rule, 0% after). None
// carries contact or access information, so every one must pass.
export const GULF_FEATURE_LIST = [
  // size
  "1,200 SF medical office", "2,400 RSF, 2,150 USF", "Up to 10,000 SF contiguous", "3,500 SF divisible to 1,750 SF",
  "Suites from 900 SF to 4,800 SF", "12,000 SF single-story building", "Total building 45,000 SF", "Approx. 1,850 SF",
  "1.2 acres, 5,600 SF building", "0.85 ac pad site", "Warehouse 8,000 SF with 1,000 SF office", "Mezzanine 600 SF",
  "Lobby 400 SF shared", "Floor plate 15,500 SF", "Two floors, 6,000 SF each",
  // parking
  "Parking 5/1000 SF", "4.5/1,000 parking ratio", "120 surface spaces", "Covered parking, 40 spaces", "Garage parking 300 spaces",
  "Structured garage, 450 stalls", "Ample free parking", "ADA parking at front entrance", "Shared lot, 85 spaces",
  "Reserved parking: 6 spaces", "Garage 250 spaces", "Parking garage with 1,100 spaces", "Gated parking, 60 spaces",
  "Key-card garage, 400 spaces", "Covered garage 150 spaces", "Garage, 400 spaces",
  // suite / layout
  "Suite 100, private entrance, 1,200 SF", "Suite 210, 2nd floor", "Suites 301-305, 5,000 SF", "Suite 4B, end cap",
  "End-cap suite with drive-thru", "Separate entrance 1,500 SF", "Private entrance and restroom", "Rear door access, 3,000 SF",
  "Two roll-up doors, 2,500 SF", "Dock door: 1,000 SF", "Grade-level door, 1,200 SF warehouse", "Two dock-high doors",
  "Glass entry, 1,100 SF lobby", "Lobby entrance / 900 SF", "Double-door entry", "Door-to-door 250 ft",
  "6 exam rooms, 2 restrooms, 1 lab", "10 exam rooms and 3 offices", "Open plan with 4 private offices", "Break room and conference room",
  "Elevator served, 3rd floor", "Ground-floor suite, 1,400 SF", "Corner suite with windows on 2 sides", "Second-generation dental space",
  "Former urgent care, 3,200 SF", "Built out for physical therapy", "Turnkey medical suite",
  // availability / terms
  "Available now", "Available 10/1/2026", "Available Q1 2027", "Delivery January 2027", "Lease-up 2027", "Occupancy 90 days after signing",
  "Available 12/15/2026, 2,000 SF", "NNN $6.50/SF", "$18/SF/yr", "$24.00/sq.ft/yr NNN", "$1.25/sq.ft/mo", "$22.50/SF full service",
  "CAM $4.25/SF (2026 est.)", "TI allowance $25/SF", "5-year term preferred", "3% annual escalations", "Asking $28/SF modified gross",
  "Sale price $1,850,000", "Cap rate 7.1%", "Suite 200/210", "Floors 2/3", "3/1000 SF parking ratio",
  // amenities / location
  "Signage; 24/7 access; 2,000 SF", "24/7 access", "Highway access, 300 ft frontage", "Direct access to I-10", "Access from Airport Blvd",
  "Easy access to I-65 and US 98", "Frontage on US 98, 250 ft", "Pylon signage on Hwy 59", "Monument sign on Gulf Breeze Pkwy",
  "Traffic count 32,000 VPD", "Near Mobile Infirmary", "Across from Thomas Hospital", "Minutes to Sacred Heart Pensacola",
  "Close to Ascension St. Vincent's", "Walk to Baptist Hospital", "Near USA Health University Hospital", "1 mile to Destin Commons",
  "Fire alarm system upgraded 2025", "Alarm system; 1,800 SF", "Security system and cameras", "Keyless entry system", "Card access at main entrance",
  "Generator backup, 150 kW", "New roof 2023", "HVAC replaced 2022", "Fiber available", "Sprinklered", "Flood zone X",
  "The Pines, 1,200 SF", "Twin Pines, 2,400 SF available", "Pines Plaza 1,500 SF", "Florida Keys 1,200 SF", "Gates Medical, 1,500 SF",
  "Gateway 2000 Building", "Lock & Key Plaza, Suite 200", "Westgate Pines", "Eastgate 1200 SF", "Northgate Professional Center",
  "Gated community adjacent", "Gated, 150 spaces", "Keys at signing", "Key money none", "Security deposit $2,500",
  "Code-compliant build-out", "Building code 2021", "Zip code 36608", "Daphne, AL 36526", "Orange Beach, AL 36561",
  "Fairhope, AL 36532", "Pensacola, FL 32502", "Destin, FL 32541", "Gulf Breeze, FL 32561", "Mobile, AL 36695",
];

// The reviewer's round-9 feature-list false positives; every one must pass.
export const REVIEWER_FEATURE_LIST = [
  "Covered garage 150 spaces", "Garage, 400 spaces", "Suite 100, private entrance, 1,200 SF", "Separate entrance 1,500 SF",
  "Rear door access, 3,000 SF", "Two roll-up doors, 2,500 SF", "Dock door: 1,000 SF", "Glass entry, 1,100 SF lobby",
  "Highway access, 300 ft frontage", "Signage; 24/7 access; 2,000 SF", "Key-card garage, 400 spaces", "Door-to-door 250 ft",
  "The Pines, 1,200 SF", "Florida Keys 1,200 SF", "garage 250 spaces", "Suites 201-204 + 1200 SF mezz", "Suites 250 - 300 - 4500 SF",
];

function unique(values) {
  return [...new Set(values)];
}

/** Strings that must be refused by both engines. */
export function adversarialRefused() {
  const out = [];
  for (const [word, plural] of ACCESS_TRIGGERS) {
    // Every connector character, tight and spaced ("Gate#4411", "Gate # 4411").
    for (const c of CONNECTORS) {
      out.push(`${word}${c}4411`);
      if (c !== " ") out.push(`${word} ${c} 4411`);
    }
    // Arrows and doubled connectors.
    out.push(`${word} -> 4411`, `${word}: #4411`, `${word}#: 4411`, `${word} (#4411)`, `${word} \u2014 4411`);
    // The plural, and the filler words.
    out.push(`${plural} 4411`, `${plural}: 4411`);
    for (const filler of ["is", "no.", "no", "number", "num", "num.", "is now", "was", "will be", "set to", "w/", "it's"]) out.push(`${word} ${filler} 4411`);
    // Digits split by up to three non-alphanumerics (not a comma).
    for (const digits of ["44 11", "4-4-1-1", "4.4.1.1", "4 4 1 1", "441-1", "4/411", "44 - 11", "44--11", "4 . 4 . 1 . 1"]) out.push(`${word}: ${digits}`);
    // 24/7 is a schedule only when written exactly; 247 is a code.
    out.push(`${word} 247`);
    // A bare year, or a year followed by something that is not a building event, is a code.
    out.push(`${word} 2014 at the gate`, `${word}: 2021`, `${word} 1998`, `${word} #2020`);
  }
  // Area and zip codes: exactly three plain digits, and a real zip, are the only exemptions.
  out.push("Area code 4411", "Area code 44 11", "Area code 441-1", "Zip code 4411", "ZIP code 4411 at gate", "Zip code 3660");
  // A unit word must be a whole word to make a measure.
  out.push("Gate 4411 access road", "Code 4411 sfx", "Door 4411 feeding", "Key 4411 spacer");
  // A place word or a count after the code does not make it a measure: only a
  // number directly followed by a measure unit is one.
  out.push("Rear gate 4411; 25 spaces", "Garage gate 4411 - 40 spaces", "Gated lot, gate 4411 - 40 spaces", "Garage: gate 4411 / 300 spaces",
    "Enter at gate 4411 - 60 spaces", "Keypad 4411 door on left", "Code 4411 - units B & C", "Gate 4411, door 5566", "Keypad: 4411 doors unlock 7am",
    "Gate 4411 - 2 doors down", "Gate: 4411 (spaces in back)", "Door 4411 / units 3-4", "Gate at 4411", "Gate at 4411 today");
  for (const place of ["units", "unit", "doors", "door", "docks", "dock"]) out.push(`Gate 4411 ${place}`);
  // A seven-digit number, with or without a contact word before it, across
  // every short separator, and followed by a time or rate word that is not
  // an area or count unit.
  const PHONE_CONTEXTS = ["", "Call ", "Cell ", "Text ", "Phone ", "Tel ", "Mobile ", "Office ", "Fax ", "Ph ", "Call: ", "Contact ", "Contact: ",
    "Leasing: ", "Questions? ", "Call Joe at ", "Owner ", "Joe cell: ", "Info - ", "Showings ", "(", "Pilot "];
  for (const context of PHONE_CONTEXTS) {
    for (const sep of [" ", "/", " # ", "_", ".", " - ", "", " : ", "*", " / "]) out.push(`${context}555${sep}0100`);
    for (const tail of [" hours 8-5", " hours", " hrs", " Mo-Fr", " mo", " yr", " psf", " ext 12", " (cell)", " rooms"]) out.push(`${context}555 0100${tail}`);
  }
  out.push("Contact 555 0100", "Contact: 555 0100", "Leasing: 555 0100", "Questions? 555 0100", "Call Joe at 555 0100", "Call 555 0100 hours 8-5",
    "Office 555 0100 hours M-F 8-5", "Call 555 0100 Mo-Fr", "Suite 200 555 0100", "Suite 20 555 0100", "Near Hotel 250 1500 rooms", "Owner 555 # 0100", "Room 200 555 0100", "Call 100 0100", "Info 155 0100");
  // A comma-ended place number does not make the next group a place number.
  out.push("Suite 200, 555 0100", "Ste 110, 555 0100", "Unit 4, 555 0100", "Floor 2, 555 0100", "Lot 3, 555 0100", "Hwy 98, 555 0100",
    "Suites 101, 102, 555 0100", "Bayside Plaza, Suite 200, 555 0100", "Suite 200 - 555 0100", "Bldg 3; 555 0100");
  // A code-book name exempts only a bare year, never a code.
  out.push("Building code 4411", "Fire code: 4411", "Zoning code #20145");
  // Phones: every separator between 3-3-4 groups, tight, spaced and doubled.
  for (const c of CONNECTORS) {
    if (c === " ") continue;
    out.push(`251${c}555${c}0100`, `251 ${c} 555 ${c} 0100`, `251${c}${c}555${c}${c}0100`);
  }
  out.push("251 , 555 , 0100", "251 ,555 ,0100", "Call 251x555x0100", "251 x 555 x 0100");
  // Links: the reviewer's shorteners and link pages, bare and with a path, and
  // a bare name under each newer ending.
  // A spaced dot before any ending.
  out.push("bit . ly/abc", "joe . realtor", "carr .realtor", "linktr .ee/bob", "landlord . estate");
  for (const link of ["lnkd.in", "shorturl.at", "bit.do", "s.id", "amzn.to", "lnk.to", "linktr.ee", "qr.link", "t.co", "ow.ly",
    "buff.ly", "rb.gy", "cutt.ly", "is.gd", "tiny.cc", "youtu.be", "bit.ly", "goo.gl"]) {
    out.push(link, `${link}/bob`, `See ${link}/x7Q`);
  }
  for (const ending of ["xyz", "site", "online", "link", "page", "tv", "ca", "uk", "de", "estate", "land", "group", "llc", "pro", "ee", "to", "in", "at", "do", "id",
    "realtor", "realestate", "broker", "house", "rentals", "apartments", "property", "agency", "bio", "live", "shop", "club", "tech", "one", "mx"]) {
    out.push(`landlord.${ending}`, `Tour at landlord.${ending} today`);
  }
  out.push("mysite.example/tour", "calendly.com/bob");
  // An ending that only starts like a unit is still a link.
  out.push("landlord.mobi/tour", "visit.miami/tour", "our.sfo/x", "go.acme/tour");
  return unique(out);
}

/** Strings that must pass both engines. */
export function adversarialAllowed() {
  const out = [];
  // (passcode and lockbox are refused as words by the lockbox rule, digits or not)
  for (const [word] of ACCESS_TRIGGERS.filter(([w]) => !/^(passcode|lock ?box)$/i.test(w))) {
    // A year followed by a building-event word is a year, for every trigger.
    out.push(`${word} 2021 upgrade`, `${word} 1998 renovation`, `${word} 2027 lease-up`, `${word} 2019 replacement`);
    // One or two digits are a door, gate or suite number, not a code.
    out.push(`${word} 2`, `${word} 12`);
  }
  out.push("Building code 2021", "Fire code 2018", "Area code 251", "Zip code 36602", "Zip code 36602-1234", "Access 24/7",
    "Security 24/7 on site", "Access 24x7", "Key dates: 1/1/2027", "Separate entrance to Suite 200", "Access off Hwy 181", "Access Road 1200",
    "Access fee $1,500 per month", "Key money $10,000", "Pines 250 Medical Park", "Door to Unit 1200", "Entry at Bldg 300",
    "Gate on Route 181", "Access via I 165", "Access at Exit 353",
    // a comma-space list, words between numbers, a six-letter word before a time, a trigger inside a name
    "Suites 250, 300, 4500 total", "Suites 25 , 300 , 4500 total", "Suites 250, 300 -,4500 total", "Suites 250 to 300 of 1200 total", "Garage closes 2200 nightly", "Westgate 2100 Building");
  // Every unit, after a code-shaped number, makes a measure.
  for (const unit of ["SF", "RSF", "USF", "sq ft", "sqft", "square feet", "ft", "feet", "foot", "spaces", "space", "stalls", "stall", "acres", "acre", "ac",
    "seats", "seat", "psf", "mo", "yr", "hours", "hrs"]) out.push(`Gate 4411 ${unit}`, `Gate 4411-${unit}`, `Suites 250 - 300 - 4500 ${unit}`);
  // A street address after a preposition, or a money word, is not a code.
  out.push("Gate at 2200 Airport Blvd", "Access from 3700 Dauphin St", "Main entrance faces 4400 Bayou Blvd", "Keys to 1200 Duval St",
    "Doors open at 1200 Government St", "Access at 3280 Dauphin Island Pkwy", "Pad sites with access to 1600 E Nine Mile Rd", "Entrance on 1200 block of Dauphin",
    "Security dep 2500", "Security: first month 2500", "Gate on 1200 Oak Ave", "Access off 900 Hwy 98", "Entrance near 400 Main St", "Access via 2100 Spring Hill Ave", "Key East 300 Water St", "Gate north 250 Royal St", "Entry south 1400 Beach Blvd", "Garage 400 reserved spaces", "Access to 200 surface spaces", "Access to 1200 parking spaces", "Key West 1200 Duval St", "Garage 300 covered spaces", "Door open 1200 hours", "Available Q1 2027, keys at 2026 signing", "Office 200 1500 SF");
  // A seven-digit shape directly followed by an area or count unit, or after
  // a place word (or a comma list of place numbers), or after a dollar sign,
  // is a measure, a place or a price.
  out.push("Office 555 1200 SF", "Office 250 1500 RSF", "Suites 200 1500 SF", "$1500000", "Price $250 1500", "Traffic 250 32000 VPD",
    "Parcel 1234 5678");
  for (const unit of ["SF", "RSF", "USF", "sq ft", "sqft", "square feet", "ft", "feet", "foot", "spaces", "space", "stalls", "stall", "acres", "acre", "ac",
    "seats", "seat", "parking spaces", "covered spaces"]) out.push(`Office 555 1200 ${unit}`, `Call center 555/1200 ${unit}`);
  for (const place of ["Suite", "Suites", "Ste", "Ste.", "Bldg", "Bldgs", "Building", "Unit", "Units", "Floor", "Floors", "Lot", "Lots", "Hwy", "Exit", "Route"]) {
    out.push(`${place} 200 1500`, `${place} #200 / 1500`, `${place} 201, 202, 1500`);
  }
  // Rates, ratios, dates and lists.
  out.push("$18/SF/yr", "Suite 200/210", "Floors 2/3", "3/1000 SF parking ratio", "Available 10/1/2026", "NNN $6.50/SF",
    "$24.00/sq.ft/yr NNN", "$1.25/sq.ft/mo", "Suites 201-204, 1200 SF", "1,000 SF", "120,000 SF", "Rooms 301, 302 (1200 SF)");
  // Words between the groups are not phone separators.
  out.push("Suites 250 to 300 of 1200 SF", "Floors 200 and 300, 4500 SF");
  // Feature lists: the reviewer's and the Gulf Coast set.
  out.push(...REVIEWER_FEATURE_LIST, ...GULF_FEATURE_LIST);
  // A sentence break before a word that is also a domain ending is not a link.
  out.push("Ample parking. In addition, signage", "Near the hospital. One mile to I-10", "Great visibility. Live oaks on site");
  // Every unit abbreviation before a slash is a rate, not a link.
  for (const unit of ["ft", "yr", "mo", "sf", "ac", "mi", "yd"]) out.push(`$2.50/sq.${unit}/yr`);
  return unique(out);
}
