# Multi-Step Copilot Loop implementation

The **Multi-Step Copilot Loop (Upgrade 3)** allows the conversational assistant to independently determine if it needs to execute multiple steps and request additional tools before formulating a final answer to the user. 

## Changes Made
- **LLM Loop Orchestration:** Updated `handle_message` in `copilot.py` to support up to 3 turns of LLM interaction per request. The planner's output now flows into the loop logic to append the context dynamically and trigger subsequent calls to itself if required.
- **Planner Context Injection:** The `PLANNER_SYSTEM` and context builders in `copilot_agent.py` were adjusted to parse the multi-turn prompt sequence. The history of recently run tools within the conversation turn (`turn_history`) is now injected into the prompt.
- **Log Parsing Tool (`explain_bot_events`):** Created a new tool that queries the backend `bot_logs` database (looking up `ERROR`, `WARN`, `INFO` messages) and returns the JSON events and reasoning structure.
- **Direct Reply Bypass:** Allowed the planner to emit a `direct_reply` output when it finishes reasoning, which is returned directly to the user (bypassing redundant formatting by the narrator), while still appending structural data tables if necessary.

## Testing and Verification
- The full backend test suite (`python -m pytest`) was executed and all 49 related tests passed successfully without regression.
- Tested the syntax and block flow to verify there are no compilation or unhandled runtime syntax errors.
