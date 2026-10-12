-- outlook-send.applescript — press Send on a mailto-spawned New Outlook compose window.
--
-- argv: 1 window-title prefix (the subject)
--
-- THE SEND STEP, kept SEPARATE from bin/outlook-draft.applescript on purpose.
-- The draft script carries a no-send guarantee and stays untouched; this file is
-- the only place the system presses Send, and it is reached only from
-- lib/system_sender.py, after that module's whole policy chain (kill switch,
-- writing lint, recipient allowlist, dedupe, daily cap, shadow mode) has passed.
-- Authority: Joe's autonomy ruling, decision cbe91287-10a2-48d0-bda7-3b1784b4bb93
-- (2026-10-12), and rule c66dc739 as amended that day.
--
-- The compose window is opened by the caller with
--   open -a "Microsoft Outlook" "mailto:..."
-- exactly the way bin/outlook-draft.py does (read its header for the two traps:
-- a bare `open mailto:` composes in the wrong account, and urlencode() breaks
-- spaces). The window is matched by TITLE PREFIX because Outlook renders it as
-- "<subject> • joe.bookout@carr.us". Every value arrives via argv so a subject
-- full of quotes is inert data.
--
-- The send itself is Command+Return, Outlook for Mac's Send shortcut. Success is
-- read from the window, never assumed: a compose window that closes after Send
-- was sent; one that is still open was not. The three answers, and what the
-- caller does with each:
--   SENT                         the window closed after Send.        -> sent
--   ERROR: no compose window ... nothing was found, nothing pressed.  -> definite non-send
--   NOT_SENT: ...                Send pressed, window still open.     -> unknown; key held
--
-- UNPROVEN LIVE: written while the sender ships in shadow mode, so this script
-- has never pressed Send on a real message. Watch the first live send.

on run argv
  set titlePrefix to item 1 of argv

  tell application "Microsoft Outlook" to activate
  delay 1

  tell application "System Events"
    tell process "Microsoft Outlook"
      set found to missing value
      repeat 20 times
        repeat with w in windows
          if (name of w) starts with titlePrefix then
            set found to w
            exit repeat
          end if
        end repeat
        if found is not missing value then exit repeat
        delay 0.5
      end repeat

      if found is missing value then
        return "ERROR: no compose window titled " & titlePrefix
      end if

      perform action "AXRaise" of found
      delay 1

      keystroke return using {command down}

      -- Poll for the window to close: that is the only evidence of a send.
      repeat 20 times
        delay 0.5
        set stillOpen to false
        repeat with w in windows
          if (name of w) starts with titlePrefix then
            set stillOpen to true
            exit repeat
          end if
        end repeat
        if not stillOpen then return "SENT"
      end repeat

      return "NOT_SENT: compose window still open after Send"
    end tell
  end tell
end run
