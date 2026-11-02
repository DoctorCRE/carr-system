#!/usr/bin/env python3
"""Keep marked orchestrators draining instead of waiting for human answers."""
import json
import sys

from orchestrator_session import transcript_snapshot

QUESTION_TOOLS = {'AskUserQuestion', 'EnterPlanMode', 'ExitPlanMode'}
REASON = ('Orchestrator sessions never block on a question (rule d1a7973a, standing order 24). '
          'Park the call as a gate with a default, apply the default or route work around it, '
          'keep draining, and put the question as plain text in your report.')

PLAN_REASON = ('Orchestrator sessions stay in default mode (rule d1a7973a, standing order 24). '
               'Keep working in default mode and write any plan into your reply or the program store.')


def decide(event):
    if (event.get('hook_event_name') != 'PreToolUse'
            or event.get('tool_name') not in QUESTION_TOOLS or event.get('agent_id')):
        return
    session = event['session_id']
    if not isinstance(session, str) or not session:
        return
    marked, _ = transcript_snapshot(event['transcript_path'], session)
    if marked:
        return dict(hookSpecificOutput=dict(hookEventName='PreToolUse',
                    permissionDecision='deny', permissionDecisionReason=PLAN_REASON
                    if event['tool_name'] == 'EnterPlanMode' else REASON))


def main():
    try:
        result = decide(json.load(sys.stdin))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return 0
    if result:
        print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
