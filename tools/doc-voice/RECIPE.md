# Doc's voice — FROZEN 2026-08-07 (Joe's freeze, same night as the bake-off)

Doc's voice is a designed synthetic voice with NO real-person referent (the legal
line, dr-cre-voice-doctrine §3). It was chosen by Joe's ear through ~30 iterations
on 2026-08-07 and is permanent: a new voice is a new being. Target ruled by Joe:
a soothing, natural ROBOT — JARVIS-class machine intelligence, never a human
impersonation. Architecture ruled by Joe ("we want 3. full jarvis"): directed
delivery on a locked identity.

## The frozen assets (reference/)

- `doc-identity-reference.wav` — THE identity anchor. Qwen3-TTS VoiceDesign render
  ("take 2"), from the v8 character brief (preserved in
  `CARR AI/DNA/Deal Management/record-layer/fable-order-voice-bakeoff-2026-08-01.md`
  execution log). Every future render clones from this file. Never regenerate it.
- `doc-master-intro-read.wav` — the approved master read of the self-introduction
  (Chatterbox clone of the identity, exaggeration 0.40, cfg_weight 0.60, corrected
  pronunciation text). Raw, pre-mastering.
- `doc-master-intro-FINAL.wav` — the same read through the frozen mastering chain:
  what Doc actually sounds like.

## The mastering chain (Joe's dialed settings, final)

ffmpeg -af "asetrate=24000*0.97,aresample=24000,atempo=0.965, highpass=f=90,
lowpass=f=7400, aresample=16000, aresample=24000, acrusher=bits=14:mode=log:mix=0.08,
equalizer=f=185:t=q:w=1.4:g=6.5, equalizer=f=4400:t=q:w=1.2:g=3,
acompressor=threshold=-16dB:ratio=2.4:attack=8:release=115:makeup=2dB,
aecho=0.8:0.9:21|36:0.10|0.07, alimiter=limit=0.9"

In words: pitch down 3%, pace eased ~3.5%, clean-machine texture (light 16k
round-trip + trace bit-crush), warm chest (+6.5dB @ 185Hz) with bright presence
(+3dB @ 4.4kHz), balanced punch (2.4:1), moderate room. Punch is a PER-SURFACE
dial: desk can run softer, truck/phone punchier — the chain runs at render time.

ROOM GAIN CORRECTED 2026-08-10 (Joe: "approved") — the line above now reads
aecho=0.8:0.9, not the original 0.75:0.23. Everything else is untouched.
ffmpeg's signature is aecho=in_gain:out_gain:delays:decays, and the original
out_gain of 0.23 scaled the WHOLE mix to 23% after the echo was folded in.
Measured
stage by stage on one render: band -22.8, 16k round-trip -22.8, crush -22.8,
chest -20.5, presence -20.5, punch -21.0, then room -36.2. One stage costs 15dB;
nothing else costs anything. Raising out_gain to 0.9 restores mean level to
-23.3 against a -22.5 raw reference and leaves the echo CHARACTER identical,
because the dry-to-echo ratio is set by the decays, not by out_gain.
It hid because 13dB at a desk is a volume-knob turn — it would have surfaced in
the truck, as Doc being inaudible rather than as Doc sounding wrong. It also
means the compressor (threshold -16dB) and limiter (0.9) have never engaged, so
the punch setting has effectively never been heard.
CONFIRMED ON BOTH ENGINES, so it is the chain and not the engine. On the
Chatterbox path, the same read through the same chain with only out_gain changed:
-36.2 dB mean before, -23.8 after. Joe approved the fix on both paths the same
day ("E is best" for ElevenLabs, "approved" for Chatterbox).

`doc-master-intro-FINAL.wav` WAS REGENERATED with the corrected chain, because
this file is the reference every future render is matched against and it had been
12.4 dB low — a quiet reference propagates to everything downstream. The original
is preserved as `doc-master-intro-FINAL.pre-roomfix-2026-08-10.wav`; nothing was
destroyed. `doc-master-intro-read.wav` (raw, pre-mastering) is untouched and is
what the regeneration was made from.

## Speakable forms (grows with every correction from Joe's ear)

- "real estate" → "realestate" (one word, stress on real)
- "CARR" → "car"
- "LOI" → "Ell-Oeye" (CHOSEN by Joe's ear, 2026-08-10, A/B/C/D/E on the API).
  Hyphen, capital E on the first half. Joe's target: "it should sound like
  'ell oh eye' except said as 'elloheye' like it rolls off the tongue fluidly
  and smoothly ... similar to how L M N O P in the alphabet song gets pushed
  together LMNOP."
  THE SIX DEAD ENDS, recorded so nobody walks them again:
    "L O I"     spaces  -> the engine drawls the letters apart
    "loy"       word    -> simply wrong, not the term
    "L.O.I."    periods -> says the letters, but too SLOW, and Joe's finding is
                           the important one: the slowness "really exposes that
                           the voice is not real". A correct-but-slow acronym is
                           an uncanny-valley tell, not a cosmetic issue.
    "elloheye"  h       -> intrusive H, lands as "ell oh HIGH"
    "elloeye"   joined  -> "the emphasis is just weird"
    "Ell Oeye"  space   -> right shape, but "the gap is too much between the
                           words"
  THE LESSON UNDER IT: the two halves need BINDING, not JOINING. A space leaves
  a gap, full joining re-stresses the syllables; the hyphen binds them without
  a pause. "ElOeye" (camel, no separator) was also tested and lost.
  IT IS HIGH-VARIANCE MID-SENTENCE, and that is a working constraint, not a
  reason to re-pick it. Chosen on a 4-second fragment where the acronym was the
  FINAL word and carried the phrase-ending fall; in a full sentence it sits
  mid-utterance with no such support. Across three takes of one sentence Joe
  rated take1 good, take2 "landed but wasn't as good ... still pretty good",
  and an earlier roll bad outright — so roughly two in three land.
  THE ENDING SCREEN CANNOT SEE THIS. terminal_slope() reads the last ~0.8s;
  the acronym is in the middle. All three takes above passed the gate at
  -38/-46/-74 Hz/s while differing audibly on the word. So: ROLL TAKES on any
  line containing L.O.I. and let the ear pick — the gate protects the ending
  and nothing else.
  THE DURABLE FIX, UNTESTED: ElevenLabs pronunciation dictionaries would pin the
  pronunciation deterministically instead of relying on the speller guessing
  right two times in three. Two things block it and both are cheap to resolve
  when someone picks this up: the render key is scoped Pronunciation
  Dictionaries = No Access, and the docs pages read on 2026-08-10 did not state
  whether PHONEME (IPA/CMU) rules work on eleven_multilingual_v2 or only ALIAS
  rules — and an alias just re-spells it, which is the problem we already have.
  Settle it by live test, per the taught rule that a capability claim becomes
  doctrine only after a test from the surface itself.
- "close" in the transaction sense → "closing". Joe, 2026-08-10: "this should be
  the version that is 'close the door' not 'we are close to finishing'". In
  "close by September thirtieth" the engine picks the adjective (nearby) rather
  than the verb. Prefer "closing by"; where a noun is unavoidable, rewrite the
  sentence rather than trusting the engine to guess.

## Delivery screening (mandatory on every rendered phrase)

Roll N takes per sentence, score terminal f0 slope
(see render-doc.py:terminal_slope), reject
rising finals. Falling, settled endings are Doc's signature (and doctrine law —
no upspeak on statements).

## Render paths

- TODAY (proven): Chatterbox clone from the identity reference
  (audio_prompt_path), exaggeration ~0.40 / cfg 0.60, then screening, then the
  mastering chain.
- TARGET ("full jarvis", Joe 2026-08-07): base Qwen3-TTS ICL — identity from
  ref_audio + per-line delivery instructions ("with quiet concern") for the four
  doctrine registers. Test result lands in the bake-off order's execution log;
  adopt as primary render path if identity holds.
- ELEVENLABS IS NOT PROMOTED. RULED 2026-08-10 at the end of the build session.
  It works, it is fast enough, and it is NOT Doc's production voice. Chatterbox
  remains the voice of record.
    THE EVIDENCE FOR IT: identity survived the clone (Joe: "yea thats him"), and
    latency is fine — 5 full renders of a 63-char line measured 0.90/1.13/1.16/
    1.18/1.62s, median 1.16s. The doctrine's 300ms acknowledgment is served by a
    pre-rendered earcon rather than TTS, so the spoken answer has its own budget
    and 1.2s sits inside it. Speed was the whole reason for going to ElevenLabs
    and ElevenLabs delivers it.
    THE EVIDENCE AGAINST IT, which wins: Joe on the mastered ElevenLabs render —
    "it sounded more masculine and a little more like jarvis [on Chatterbox].
    after the master it sounded too human". Character, not level or pace.
    WHY THAT SETTLES IT: this doctrine says the voice IS the character and a new
    voice is a new being, never changed casually. Running both engines in
    production means two Docs, which is precisely what that rule prevents. A
    1.2-second speed gain does not buy back an audible character change.
  WHAT WOULD REOPEN IT: closing the character gap (a re-clone from a different
  corpus slice, or settings that recover the lower, drier read), or a live need
  where local render speed genuinely blocks the product. Everything built for it
  stays — the clone, render-doc.py, the mastering chain, the measured latency —
  so promoting it later is a ruling, not a rebuild.

- ELEVENLABS DETAIL (identity confirmed 2026-08-10, Joe: "yea thats him"). Voice "Doc",
  an Instant Voice Clone of 3m07s of the RAW corpus. Professional Voice Cloning
  is permanently closed to Doc — 30-minute floor against our 15.9, and it demands
  the account holder record a verification read matching the samples, which no
  human can do for a synthetic register. Full reasoning: decision 3ccb57c6.
    Model Eleven Multilingual v2 · Stability 65% · Similarity ~87% ·
    Style Exaggeration 0 · Speaker boost ON · Speed just under the midpoint.
  SPEED IS DOC'S OWN NUMBER, NOT THE DOCTRINE'S. The doctrine's medium-slow
  target overshot — Joe: "it dragged just a little bit, maybe somewhere in
  between the two values". Same 837-character passage: 0:56 default, 1:00
  doctrine-slow, 0:58 adopted.
  Background-noise removal OFF at clone time: the source is a synthetic render
  with no noise, so denoising could only smear the timbre being preserved.
  OUTPUT IS MP3 128kbps AND CANNOT BE BETTER ON STARTER — every lossless format
  is Pro+ ($99/mo).

  THE ELEVENLABS MASTERING CHAIN (adopted 2026-08-10, Joe: "E is best"):

    ffmpeg -af "highpass=f=90,lowpass=f=7400, aresample=16000, aresample=24000,
    acrusher=bits=14:mode=log:mix=0.08, equalizer=f=185:t=q:w=1.4:g=6.5,
    equalizer=f=4400:t=q:w=1.2:g=3,
    acompressor=threshold=-16dB:ratio=2.4:attack=8:release=115:makeup=2dB,
    aecho=0.8:0.9:21|36:0.10|0.07, alimiter=limit=0.9"

  TWO DELIBERATE DIFFERENCES from the Chatterbox chain, each decided by a test:
    NO asetrate/atempo, AND THE REAL REASON IS SAMPLE RATE, NOT PACE.
    `asetrate=24000*0.97` hardcodes a 24kHz source, which is what Chatterbox
    produces. ELEVENLABS DELIVERS 44.1kHz, so that filter reinterprets 44,100Hz
    audio as 23,280 and slows it to roughly HALF SPEED — measured 16.1s against
    an 8.7s source when it was tried on 2026-08-10. Joe: "all of these sound
    ridiculous. they are slowed way down."
    The pace argument (the engine's speed dial already carries his 0:56/1:00/0:58
    number, so the chain's atempo would slow him twice) is also true and variant
    A proved it audibly — but it is the SECOND reason, and stating it alone hid
    a defect that bites anyone porting this chain anywhere. ANY new engine: check
    its output sample rate before reusing a stage that names one.
    aecho out_gain 0.23 -> 0.9. The level bug documented above.
  AND TWO THINGS DELIBERATELY UNCHANGED, because tests said leave them:
    CHEST STAYS AT +6.5dB. A variant at +4dB was indistinguishable to Joe and
    measured only 0.6dB different overall — a narrow-band change is small. Do
    not alter a dialled number the ear cannot separate.
    THE BIT-CRUSH STAYS. Removing it (keeping the 16k round-trip) was "a little
    too much ... slightly less preferred", so the MP3 codec does NOT already
    supply Doc's machine texture, which was the plausible theory going in.

  ELEVEN V3 WAS TESTED AND REJECTED, 2026-08-10 — do not retry it blind.
  It is the obvious candidate, because it takes per-line audio tags and that is
  exactly the "full jarvis" architecture (identity locked, delivery directed) and
  the honest fix for the four doctrine registers. Two things killed it:
    PACE. v3 has NO speed control — the panel offers only Stability on a
    Creative/Robust axis. The same passage ran 0:43-0:44 on v3 against 0:56-0:58
    on v2, roughly 25% faster, and Joe's verdict both times was "way too fast".
    A [slowly] tag PARSED (the editor highlighted it) and changed nothing:
    0:44 untagged, 0:43 tagged. The tag layer controls emotion, not tempo.
    Doc is "never in a hurry, and that is audible" — that is character, not
    preference, so an engine that cannot be slowed cannot carry him.
  Two things worth remembering FOR v3, if it ever gains a speed control:
    [serious] and [slowly] both parse, and v3 returns TWO generations per run —
    native take-rolling, which is the screening step this recipe already wants.
  Also: switching model to v3 SILENTLY SWAPPED THE VOICE to a stock "Liam".
  Check the voice after any model change; a session that did not would have
  rendered a stranger and blamed the clone.
  Corroboration, found after the call was made: ElevenLabs' own model picker
  marks Multilingual v2 "Recommended for Doc".
  NOT yet tested: Eleven Flash v2.5, the ultra-low-latency model. That is the
  one that matters for the live path, since the doctrine requires P95 latency
  measured on a real phone before any provider is adopted.

## Iteration rules (from decision 0e192fcb — do not relearn these)

Character briefs are dead: never re-describe Doc from words (that re-rolls the
actor). Identity comes ONLY from the reference wav. Delivery varies per render —
roll and screen. Mastering dials are deterministic and always safe. Every render
Joe approves gets SAVED before it is played.
