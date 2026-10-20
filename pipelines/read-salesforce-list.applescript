on run argv
    set operation to item 1 of argv
    if application "Google Chrome" is not running then error "Chrome is not running"
    tell application "Google Chrome"
        if operation is "open" then
            if (count windows) is 0 then error "Chrome has no signed-in window"
            set priorIndex to active tab index of window 1
            set ownedTab to make new tab at end of tabs of window 1 with properties {URL:item 2 of argv}
            set ownedId to id of ownedTab
            set active tab index of window 1 to priorIndex
            return ownedId as text
        end if
        set tabId to (item 2 of argv) as integer
        repeat with browserWindow in windows
            repeat with browserTab in tabs of browserWindow
                if id of browserTab is tabId then
                    if operation is "close" then
                        close browserTab
                        return "closed"
                    end if
                    if operation is not "evaluate" then error "Unsupported operation"
                    set expectedURL to item 3 of argv
                    set actualURL to URL of browserTab
                    if actualURL is "about:blank" then return "{\"state\":\"loading\"}"
                    if actualURL is not expectedURL then
                        if actualURL contains "/secur/" or actualURL contains "/login" or actualURL contains "login.salesforce.com" then
                            return "{\"state\":\"failed\",\"reason\":\"Salesforce sign-in needed\"}"
                        end if
                        return "{\"state\":\"failed\",\"reason\":\"Salesforce page shape changed\"}"
                    end if
                    return execute browserTab javascript (item 4 of argv)
                end if
            end repeat
        end repeat
        error "Owned Salesforce tab is unavailable"
    end tell
end run
