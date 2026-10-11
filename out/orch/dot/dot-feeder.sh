#!/bin/zsh
export PATH=/Users/booko/carr-system/out/orch/bin:$PATH   # gh limiter + call log (2026-10-06 secondary rate limit)
# dot-feeder.sh — keep the Dot busy with no orchestrator turn in the loop.
# Takes the oldest brief in queue/ (NN-name.md), posts it to #dot-jobs with bin/dot-relay, relays its
# thread until a DOT-REPORT-END reply, files the report under reports/, then sends the next brief.
# A brief already in sent/ with no report (an interrupted run) is resumed first, from feeder.log.
# Why: the Dot sat idle for long stretches on 2026-10-01 because each next job waited on the
# orchestrator noticing a finished report, and the screen-reader watcher went blind.
# Watchdog (Joe 2026-10-01: alert well before 6 hours is wasted), read from Slack every minute by
# dot-thread-age.py: no Dot reply 10 min after the brief = not picked up; a Dot reply then 30 min of
# silence = stalled; 90 min without a report = overdue; 5 Slack read errors in a row = blind.
# Any of those, or a send failure, stops the relay, moves the brief to failed/, writes
# FEEDER-STALLED with the reason and exits nonzero, which wakes the orchestrator.
# LANES (Joe 2026-10-01: "squeeze as much out of dot as possible while its free"): run several feeders at once,
# LANE=1..N. Each claims a brief with an atomic mv into claim/, so no two lanes send the same job; only lane 1
# resumes interrupted jobs. Run ONE lane: the Dot works one job at a time and takes the newest brief, so a second lane's queued job starves (measured 2026-10-01).
D=${0:A:h}; R=${D:h:h:h};
export CARR_ORCH_DIR=${D:h}
 Q=$D/queue; LANE=${LANE:-1}
mkdir -p $Q $D/sent $D/failed $D/reports $D/claim
LOG=$D/feeder.log; STATE=$HOME/.local/state/dot-relay
PICKUP=1800; SILENT=1800; OVERDUE=5400   # 30 min pickup: the Dot runs one job at a time and queues the next (measured 2026-10-01), so a queued job waits for the current one
log(){ print -r -- "$(date '+%Y-%m-%dT%H:%M:%S%z') [lane $LANE] $*" >> $LOG; }
# The Dot drives this Mac through ChatGPT computer use, and macOS blanks every screen capture while the console
# is locked, so the Dot goes deaf and a job looks "never picked up" (2026-10-02 triage-06). While locked, send
# nothing and pause the watchdog clocks; the lane resumes on its own at unlock.
locked(){ ioreg -n Root -d1 -a 2>/dev/null | grep -A1 IOConsoleLocked | grep -q true; }
wait_unlock(){ locked || return 0; log "screen locked: Dot cannot see; holding"; print -r -- "screen locked since $(date '+%H:%M'): unlock this Mac" > $D/FEEDER-LOCKED
  while locked; do sleep 60; done; rm -f $D/FEEDER-LOCKED; log "screen unlocked: resuming"; }
stall(){ log "STALLED $1: $2"; print -r -- "$1: $2" > $D/FEEDER-STALLED; mv $D/sent/$1.md $D/failed/ 2>/dev/null; exit ${3:-2}; }
# file_report <name>: file a saved report into the record layer (bin/dot-file-reports, PR 1475). A filing
# failure never stops the Dot: the report stays on disk and the next filer run picks it up.
file_report(){ [ -x $R/bin/dot-file-reports ] || { log "filing skipped $1: filer not on main yet"; return 0; }
  $R/bin/dot-file-reports $D/reports/$1.txt >> $LOG 2>&1 || log "filing FAILED $1 (report kept)"; }
# run_job <name> <thread-ts>: relay in the background, watchdog in the foreground
run_job(){
  local name=$1 ts=$2 rp errs=0 out silent=$SILENT
  # research sweeps (lead signals, research papers, e2e) think for a long time between replies (2026-10-05: Mobile sweep stalled at 30 min)
  [[ $name == *-LS-* || $name == *-RP-* || $name == *-E2E-* ]] && silent=2700
  $R/bin/dot-relay relay $ts --max-polls 200 >> $LOG 2>&1 &
  rp=$!
  while kill -0 $rp 2>/dev/null; do
    sleep 60
    kill -0 $rp 2>/dev/null || break
    if locked; then wait_unlock; RESTART=1; break; fi
    out=($(/opt/homebrew/bin/python3 $D/dot-thread-age.py $ts $D/reports/$name.txt 2>>$LOG))
    if [ "$out[1]" = err ]; then
      errs=$((errs+1)); [ $errs -ge 5 ] && { kill $rp; stall $name "Slack unreadable 5 min ($out[2]), thread $ts" 3; }
      continue
    fi
    errs=0
    # the Dot ended its report (any marker form; see dot-thread-age.py): file it and move on
    if [ "$out[4]" = 1 ]; then wait $rp; local verdict_rc=$?; [ $verdict_rc -eq 0 ] || stall $name "authenticated relay publication failed rc=$verdict_rc, thread $ts" 2; chmod 600 $D/reports/$name.txt; log "report $name saved by thread check ($(wc -l < $D/reports/$name.txt) lines)"; file_report $name; return 0; fi
    if [ "$out[1]" = 0 ] && [ "$out[2]" -ge $PICKUP ]; then kill $rp
      # The Dot occasionally never sees a job (2026-10-01 sleep-medicine p4, 2026-10-02 triage-06). Re-post it once as a
      # fresh thread at the front of the queue; a second miss stalls as before.
      if [ ! -e $D/sent/$name.reposted ]; then log "no pickup on $name in $((PICKUP/60)) min, thread $ts; re-posting once"
        touch $D/sent/$name.reposted; mv $D/sent/$name.md $Q/$name.md; return 0; fi
      stall $name "Dot never picked it up twice (no reply in $((PICKUP/60)) min), thread $ts" 4; fi
    if [ "$out[1]" -gt 0 ] && [ "$out[2]" -ge $silent ]; then kill $rp; stall $name "Dot silent $((silent/60)) min after $out[1] replies, thread $ts" 5; fi
    if [ "$out[3]" -ge $OVERDUE ]; then kill $rp; stall $name "no report after $((OVERDUE/60)) min, thread $ts" 6; fi
  done
  if [ "${RESTART:-0}" = 1 ]; then RESTART=0; kill $rp 2>/dev/null; wait $rp 2>/dev/null
    rm -f $D/sent/$name.reposted; mv $D/sent/$name.md $Q/$name.md; log "re-posting $name after unlock (thread $ts unseen)"; return 0; fi
  wait $rp; local rc=$?
  # The relay's own report.txt holds only the Dot's last message, so a multi-part report lost every
  # part but the last (2026-10-01). File the whole thread instead, once the final part has arrived.
  if [ $rc -eq 0 ] && [ -s $STATE/$ts/report.txt ]; then
    local n=0
    while [ $n -lt 30 ]; do
      out=($(/opt/homebrew/bin/python3 $D/dot-thread-age.py $ts $D/reports/$name.txt 2>>$LOG))
      [ "$out[4]" = 1 ] && break
      n=$((n+1)); sleep 60
    done
    [ "$out[4]" = 1 ] || cp $STATE/$ts/report.txt $D/reports/$name.txt
    chmod 600 $D/reports/$name.txt
    log "report $name saved ($(wc -l < $D/reports/$name.txt) lines)"
    file_report $name
  elif [ $rc -eq 1 ] && [ ${RETRY:-0} -lt 3 ]; then
    # the relay folds every Slack/network error into rc=1; one blip stopped the lane at 02:01 on 2026-10-02.
    # Re-attach to the same thread (never re-post) up to 3 times, a minute apart, before stalling.
    RETRY=$(( ${RETRY:-0} + 1 )); log "relay rc=1 on $name; re-attach $RETRY/3 in 60s, thread $ts"; sleep 60
    run_job $name $ts; local r2=$?; RETRY=0; return $r2
  else
    RETRY=0; stall $name "relay ended rc=$rc with no report, thread $ts" 2
  fi
}
for helper in $D/dot-thread-age.py $D/dot-autofill.py $R/bin/dot-review.py $R/bin/dot-relay; do
  [ -f "$helper" ] || { print -u2 -- "feeder dependency missing: $helper"; exit 9; }
done
log "feeder start pid $$"; rm -f $D/FEEDER-STALLED
# resume any sent brief that has no report yet
[ $LANE = 1 ] && for b in $D/sent/*.md(N); do   # glob, never ls: an empty (N) glob makes ls list the cwd (2026-10-02 it claimed _to_delete)
  name=${b:t:r}; [ -e $D/reports/$name.txt ] && continue
  ts=$(grep -E "sent $name thread " $LOG | tail -1 | awk '{print $NF}')
  [ -n "$ts" ] && { log "resume $name thread $ts"; run_job $name $ts; }
done
while true; do
  python3 $R/bin/dot-review.py --orch ${D:h} drain 2>>$LOG || { log "review submission pending recovery"; sleep 60; continue; }
  nf=$(python3 $R/bin/dot-review.py --orch ${D:h} adopt 2>>$LOG) || { log "Dot adoption failed"; sleep 60; continue; }
  # Paid-token-saving support briefs precede idle review filler.
  support=( $Q/SUPPORT-*.md(N) )
  b=( $Q/*.md(N) ); b=${support[1]:-${b[1]}}   # empty queue gives ""
  if [ -z "$b" ]; then
    # refill with reviews of open PR heads the Dot has not seen; if none, wake the orchestrator
    nf=$(/opt/homebrew/bin/python3 $D/dot-autofill.py 2>>$LOG)
    log "queue empty; autofill queued ${nf:-0}"
    [ "${nf:-0}" -gt 0 ] 2>/dev/null && continue
    # Wait for new PR heads instead of exiting: an exit left the Dot idle until someone restarted the lane
    # (2026-10-02 10:32). QUEUE-EMPTY stays visible while it waits; a brief dropped into queue/ is picked up too.
    print -r -- "$(date '+%Y-%m-%dT%H:%M:%S%z') queue empty and no unreviewed PR heads; rechecking every 10 min" > $D/QUEUE-EMPTY
    sleep 600; continue
  fi
  rm -f $D/QUEUE-EMPTY; name=${b:t:r}
  wait_unlock
  # market jobs (NNN-M-<market>) must name a territory market; others are refused, never sent (Joe 2026-10-02)
  if [[ $name == [0-9][0-9][0-9]-M-* ]] && ! grep -v '^#' $D/territory.txt | grep -qxF "${name#*-M-}"; then
    mkdir -p $D/refused; mv "$b" $D/refused/; log "REFUSED $name: market not in territory.txt"; continue
  fi
  mv "$b" $D/claim/ 2>/dev/null || continue   # another lane took it
  b=$D/claim/$name.md
  ts=$($R/bin/dot-relay send-job "$b" 2>>$LOG | tail -1)
  [[ $ts =~ ^[0-9]+\.[0-9]+$ ]] || { log "SEND FAILED $name: $ts"; mv "$b" $Q/; print -r -- "send failed: $name" > $D/FEEDER-STALLED; exit 1; }
  mv "$b" $D/sent/; log "sent $name thread $ts"
  run_job $name $ts
done
