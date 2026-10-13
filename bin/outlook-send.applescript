-- outlook-send.applescript — press Send on ONE mailto-spawned New Outlook compose window.
--
-- argv: probe <subject>
--       send  <subject> <window count from the probe> <single-use send token>
--
-- THE SEND STEP, kept SEPARATE from bin/outlook-draft.applescript on purpose.
-- The draft script carries a no-send guarantee and stays untouched; this file is
-- the only place the system presses Send, and it is reached only from
-- lib/system_sender.py, after that module's whole policy chain (config, kill
-- switch, writing lint, subject and recipient allowlist, dedupe, daily cap,
-- shadow mode) has passed. Authority: Joe's autonomy ruling, decision
-- cbe91287-10a2-48d0-bda7-3b1784b4bb93 (2026-10-12), and rule c66dc739 as
-- amended that day.
--
-- The caller (lib/system_sender.py _outlook_send) runs three steps:
--   1. `probe`  count Outlook's windows and the ones already titled with this
--               subject. The caller refuses, opening nothing, if any window
--               already carries the subject (it could be Joe's own draft) or
--               Outlook is not running.
--   2. open -a "Microsoft Outlook" "mailto:..."   exactly as bin/outlook-draft.py
--               does (read its header for the two traps). A non-zero exit is a
--               definite non-send.
--   3. `send`   this script, with the probe's window count and a fresh
--               single-use token the caller wrote to <state_dir>/send-token.
--
-- THE SEND TOKEN. `send` presses nothing without it, so running this script
-- directly skips nothing: it refuses. lib/system_sender.py writes the token
-- file (`<token>` LF `<expiry, epoch seconds>` LF `<subject>`, mode 600) only
-- after the whole policy chain has passed, while it holds the send lock, and
-- passes the state directory in CARR_SYSTEM_SENDER_STATE_DIR (unset means the
-- default ~/carr-local/system-sender). The token is checked FIRST, before any
-- `tell application`, so a refused call never touches Outlook: it must equal
-- the stored token, the subject must equal the stored subject, and the expiry
-- must not have passed. The file is then deleted (consume once) before any
-- window is read or any key pressed. A wrong, missing, reused or expired token
-- answers "REFUSED: no valid send token; nothing pressed".
--
-- WHICH WINDOW. The Send keystroke may only reach the window the policy chain
-- checked. So `send` proceeds only if exactly ONE new window appeared since the
-- probe, and exactly ONE window's title matches the FULL subject exactly
-- ("<subject>" or "<subject> • <account address>"; case-sensitive, never a
-- prefix). Zero or more than one match refuses. There is no fallback to the
-- first or front window. A subject Outlook truncates in the title bar matches
-- nothing and refuses. Every value arrives via argv, so a subject full of
-- quotes is inert data.
--
-- WHERE THE KEYSTROKE GOES. Command+Return goes to the frontmost app, so Outlook
-- is re-activated and `frontmost` is verified, together with the front window
-- being the matched one, immediately before the keystroke. If Outlook is not
-- frontmost after one re-activation, nothing is pressed. New Outlook exposes no
-- mailbox or outgoing message to AppleScript (see bin/outlook-draft.py), so a
-- scripted `send` is not available; the keystroke is the only route.
--
-- Success is read from the window, never assumed: a compose window that closes
-- after Send was sent; one that is still open was not. The answers, and what
-- the caller does with each:
--   COUNT <windows> <matching>   probe answer.
--   NOT_RUNNING                  probe: Outlook is not running.          -> refuse, nothing opened
--   REFUSED: ...                 window not unique; nothing pressed.     -> definite non-send
--   NOT_FRONTMOST: ...           Outlook not frontmost; nothing pressed. -> definite non-send
--   SENT                         the window closed after Send.           -> sent
--   NOT_SENT: ...                Send pressed, window still open.        -> unknown; key held
--
-- LIVE, AND THE FIRST LIVE SEND IS THE PROOF. The sender ships live (Joe,
-- 2026-10-12: no shadow period for an obvious improvement). Until the first
-- live send is recorded, this keystroke path is unproven on a real message, so
-- the first live send is lib.system_sender.send_self_test(), a mail to Joe's
-- own address, before any participant mail.

on run argv
  set mode to item 1 of argv
  set subj to item 2 of argv
  if subj is "" then return "REFUSED: empty subject; nothing pressed"

  if mode is "probe" then
    tell application "System Events"
      if not (exists process "Microsoft Outlook") then return "NOT_RUNNING"
      set total to count of windows of process "Microsoft Outlook"
    end tell
    return "COUNT " & total & " " & (count of my matchingWindows(subj))
  end if
  if mode is not "send" then return "REFUSED: unknown mode " & mode & "; nothing pressed"

  -- The send token, before anything else: no `tell` above this line in `send`.
  if (count of argv) < 4 then return "REFUSED: no valid send token; nothing pressed"
  if not my consumeSendToken(item 4 of argv, subj) then return "REFUSED: no valid send token; nothing pressed"

  set priorCount to (item 3 of argv) as integer

  -- Exactly one NEW window, then exactly one window with this exact title.
  set total to priorCount
  repeat 20 times
    tell application "System Events" to set total to count of windows of process "Microsoft Outlook"
    if total > priorCount + 1 then return "REFUSED: " & (total - priorCount) & " new windows appeared, expected exactly one; nothing pressed"
    if total = priorCount + 1 then exit repeat
    delay 0.5
  end repeat
  if total < priorCount + 1 then return "REFUSED: no new compose window appeared; nothing pressed"

  set matches to {}
  repeat 10 times
    set matches to my matchingWindows(subj)
    if (count of matches) > 0 then exit repeat
    delay 0.5
  end repeat
  if (count of matches) is 0 then return "REFUSED: no window titled exactly with the subject; nothing pressed"
  if (count of matches) > 1 then return "REFUSED: " & (count of matches) & " windows titled with the subject; nothing pressed"
  tell application "System Events" to set total to count of windows of process "Microsoft Outlook"
  if total is not priorCount + 1 then return "REFUSED: window count changed to " & total & " while matching; nothing pressed"

  set found to item 1 of matches
  tell application "System Events" to set targetName to name of found
  tell application "System Events" to perform action "AXRaise" of found

  -- Re-activate and verify, immediately before the keystroke. One retry.
  if not my outlookReady(targetName) then
    if not my outlookReady(targetName) then return "NOT_FRONTMOST: Outlook or its compose window is not frontmost after re-activation; nothing pressed"
  end if
  tell application "System Events"
    tell process "Microsoft Outlook"
      if not frontmost then return "NOT_FRONTMOST: focus moved before the keystroke; nothing pressed"
      keystroke return using {command down}
    end tell
  end tell

  -- Poll for the window to close: that is the only evidence of a send.
  repeat 20 times
    delay 0.5
    if (count of my matchingWindows(subj)) is 0 then return "SENT"
  end repeat
  return "NOT_SENT: compose window still open after Send"
end run

-- Activate Outlook, then true only if it is frontmost AND its front window is
-- the matched compose window.
on outlookReady(targetName)
  tell application "Microsoft Outlook" to activate
  delay 0.5
  tell application "System Events"
    tell process "Microsoft Outlook"
      if not frontmost then return false
      try
        set frontName to name of window 1
      on error
        return false
      end try
    end tell
  end tell
  considering case
    return frontName is targetName
  end considering
end outlookReady

-- Every Outlook window whose title is the subject, or the subject followed by
-- " • " and an account address. Exact and case-sensitive: never a prefix.
on matchingWindows(subj)
  set found to {}
  tell application "System Events"
    if not (exists process "Microsoft Outlook") then return found
    repeat with w in (windows of process "Microsoft Outlook")
      set t to ""
      try
        set t to (name of w) as text
      end try
      if my titleMatches(t, subj) then set end of found to contents of w
    end repeat
  end tell
  return found
end matchingWindows

on titleMatches(t, subj)
  set sep to subj & " " & (character id 8226) & " "
  considering case
    if t is subj then return true
    if (length of t) > (length of sep) and t starts with sep then
      set rest to text ((length of sep) + 1) thru -1 of t
      if rest contains "@" and rest does not contain " " then return true
    end if
  end considering
  return false
end titleMatches

-- True only if `offered` is the token lib/system_sender.py stored for this
-- exact subject and it has not expired; the token file is then deleted before
-- the caller reads a window or presses a key. No `tell application` here.
on consumeSendToken(offered, subj)
  if offered is "" then return false
  set stateDir to system attribute "CARR_SYSTEM_SENDER_STATE_DIR"
  if stateDir is "" then set stateDir to (POSIX path of (path to home folder)) & "carr-local/system-sender"
  set tokenPath to stateDir & "/send-token"
  try
    set stored to read (POSIX file tokenPath) as «class utf8»
  on error
    return false
  end try
  set savedDelims to AppleScript's text item delimiters
  set AppleScript's text item delimiters to linefeed
  set parts to text items of stored
  if (count of parts) < 3 then
    set AppleScript's text item delimiters to savedDelims
    return false
  end if
  set storedToken to item 1 of parts
  set storedExpiry to item 2 of parts
  set storedSubject to (items 3 thru -1 of parts) as text
  set AppleScript's text item delimiters to savedDelims
  considering case
    if storedToken is not offered then return false
    if storedSubject is not subj then return false
  end considering
  try
    set expirySecs to storedExpiry as number
    set nowSecs to (do shell script "/bin/date +%s") as number
  on error
    return false
  end try
  if nowSecs > expirySecs then return false
  -- Consume once: the file is gone before anything is pressed.
  try
    do shell script "/bin/rm " & quoted form of tokenPath & " && test ! -e " & quoted form of tokenPath
  on error
    return false
  end try
  return true
end consumeSendToken
