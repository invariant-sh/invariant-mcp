# Examples

These files are small enough to run, and they match the real Holds, Vigil, and Maul contracts.

| Path | What it is |
| --- | --- |
| `holds/holds.yaml` | Holds suite version 1. One task, `exact_field` grader. |
| `holds/agent.py` | Agent process. Reads `HOLDS_INPUT_PATH`, writes `HOLDS_RESULT_PATH`. |
| `vigil/vigil.yaml` | Vigil policy version 1: budget, allow-list, circuit breaker, tool-content guard. |
| `vigil/events.jsonl` | Three recorded requests (no prompt text) for `check_policy`. |
| `maul/maul.yaml` | Maul config. `force_500` at probability 1.0, so upstream is not called. |
| `maul/probe_agent.py` | Agent for `maul test`. Talks to `MAUL_BASE_URL`. |
| `clients/` | Claude Desktop and Cursor MCP config snippets. |

Replace the `/ABSOLUTE/PATH/...` placeholders in the client configs.

Holds runs `agent.command` with `sh -c`. That string lives in the suite file. The MCP `run_eval` tool takes the suite path, not a shell command.

`run_failure_probe` takes an argv list. For the sample probe, from a workspace that contains this folder:

```text
agent: ["python3", "maul/probe_agent.py"]
config: "maul/maul.yaml"
```

That probe needs the `maul` binary on `PATH` (or `INVARIANT_MAUL_BIN`). Without it the tool returns `status: unavailable` and does not write a report.
