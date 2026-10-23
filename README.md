# invariant-mcp

MCP server for [Invariant](https://getinvariant.sh/). A host such as Claude Desktop or Cursor can run a [Holds](https://github.com/invariant-sh/holds) eval, compare it to a baseline, check a [Vigil](https://github.com/invariant-sh/vigil) policy, and probe an agent through [Maul](https://github.com/invariant-sh/maul).

```text
run_eval            Holds RunService            holds run
compare_baseline    Holds CompareService        holds compare
check_policy        Vigil validate / dry-run    vigil policy validate | dry-run
list_scenarios      Maul fault catalog          maul --validate when the binary exists
run_failure_probe   Maul CLI                    maul test
```

Holds and Vigil are called through their Python APIs (pinned git commits, not a reimplementation). Maul is a Rust binary with no Python API. The server shells out to `maul` and, when the binary is absent, says so. It does not invent a reliability report.

The official Python SDK renamed `FastMCP` to `MCPServer` in v2. This project uses `MCPServer` from the `mcp` package.

New to MCP? Read [docs/CONCEPTS.md](docs/CONCEPTS.md). It walks the primitives, the `initialize` handshake, and how a client discovers and calls a tool, using this server as the example.

## Install

Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
uv run invariant-mcp --help
```

Holds and Vigil are installed from GitHub as library dependencies. Maul is not. Install the `maul` binary yourself when you want failure probes (GitHub release, or `cargo install --git https://github.com/invariant-sh/maul.git`) and point at it with `INVARIANT_MAUL_BIN` if it is not on `PATH`.

## Run

```bash
# stdio, for a host that launches the process. This is the default.
uv run invariant-mcp --workspace /path/to/agent-repo

# streamable HTTP on loopback. Clients use http://127.0.0.1:8000/mcp
uv run invariant-mcp --transport streamable-http --workspace /path/to/agent-repo --port 8000
```

`--json-response` returns one JSON body per POST instead of an SSE stream. `--stateless` skips session tracking. SSE is not offered.

Every file path a tool or resource accepts is resolved inside `--workspace` (or `INVARIANT_WORKSPACE`, or the current directory). The model cannot widen that root.

## Claude Desktop

Merge `examples/clients/claude_desktop.json` into Claude Desktop's config (`claude_desktop_config.json`; on macOS that file lives under `~/Library/Application Support/Claude/`). Replace both absolute paths.

```json
{
  "mcpServers": {
    "invariant": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/ABSOLUTE/PATH/invariant-mcp",
        "invariant-mcp",
        "--workspace",
        "/ABSOLUTE/PATH/agent-repo"
      ]
    }
  }
}
```

Restart Claude Desktop. The process speaks stdio. Logs are on stderr, so they show up in Claude's MCP log rather than on the protocol stream.

## Cursor

Project config is `.cursor/mcp.json`. The same stdio entry as above is in `examples/clients/cursor.mcp.json`.

For streamable HTTP, start the server yourself and use `examples/clients/cursor.http.mcp.json`:

```json
{
  "mcpServers": {
    "invariant": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

The HTTP listener has no authentication. Keep it on `127.0.0.1`.

## MCP Inspector

The SDK ships an Inspector wrapper. It imports the module-level server, so set the workspace before launch:

```bash
INVARIANT_WORKSPACE=/path/to/agent-repo uv run mcp dev src/invariant_mcp/server.py
```

Or point the Inspector at the CLI, which is what Claude and Cursor spawn:

```bash
npx @modelcontextprotocol/inspector \
  uv run --directory /path/to/invariant-mcp invariant-mcp --workspace /path/to/agent-repo
```

In the Inspector, open `initialize`, then `tools/list`, then call `list_scenarios`. That is enough to see capability negotiation and a tool call. Set `INVARIANT_LOG_LEVEL=DEBUG` for stderr logs. Do not write to stdout while a stdio session is up.

## Tools

| Tool | Calls | Writes |
| --- | --- | --- |
| `run_eval` | Holds `RunService` | A versioned Holds report and a markdown summary |
| `compare_baseline` | Holds `CompareService` | Nothing. Baseline promotion stays `holds baseline` |
| `check_policy` | Vigil validate, plus dry-run when `events` is set | `decisions.jsonl` (overwrite) and `audit.jsonl` (append) on dry-run |
| `list_scenarios` | Compiled Maul v0.1 catalog; `maul --validate` if the binary exists | Nothing |
| `run_failure_probe` | `maul test` | `reliability_report.json` when Maul actually runs |

`run_eval` returns the summary even when thresholds fail. The report file is still written. Raw agent outputs stay out of the tool result. `include_outputs` only affects the file, and it defaults off.

`check_policy` with no events validates `vigil.yaml`. With an events file it dry-runs recorded requests. A block is a completed check, not a failed call. Nothing is deployed. `vigil policy from-maul` is intentionally not a tool: Vigil treats those YAML files as suggestions a person reviews.

`list_scenarios` knows `force_500`, `force_429`, and `malformed_tool_call_json`, which is the set in Maul's fault engine at commit `0d18832`. `force_500` and `force_429` do not call upstream. `malformed_tool_call_json` does. The `/__maul` control plane is marked planned upstream and is not queried.

`run_failure_probe` takes an argv list (`["python3", "agent.py"]`), not a shell string. If `maul` is missing, `status` is `unavailable` and `summary` is null.

Sample inputs live in [`examples/`](examples/).

## Resources and prompts

| URI | What you get |
| --- | --- |
| `invariant://workspace/index` | Discovered reports, baselines, policies, events, decisions, audits |
| `invariant://holds/report/{+path}` | Holds report JSON |
| `invariant://holds/baseline/{+path}` | Holds baseline JSON |
| `invariant://vigil/policy/{+path}` | Policy YAML |
| `invariant://vigil/events/{+path}` | Request JSONL |
| `invariant://vigil/decisions/{+path}` | Dry-run decisions |
| `invariant://vigil/audit/{+path}` | Redacted audit JSONL |
| `invariant://maul/report/{+path}` | Reliability report |
| `invariant://maul/scenarios` | Fault catalog |

`{+path}` is workspace-relative and may contain slashes.

Prompts, for a person to invoke from the host:

- `gate_a_change` — `run_eval`, then `compare_baseline`
- `investigate_failure` — `list_scenarios`, then `run_failure_probe`

## Configuration

| Variable | Default | Role |
| --- | --- | --- |
| `INVARIANT_WORKSPACE` | current directory | Sandbox root. Overridden by `--workspace` |
| `INVARIANT_MAUL_BIN` | `maul` | Maul executable name or absolute path |
| `INVARIANT_MAX_EVAL_SECONDS` | 300 | Cap on the sum of Holds per-attempt timeouts |
| `INVARIANT_SUBPROCESS_TIMEOUT_SECONDS` | 120 | Cap for `maul test` and `maul --validate` |
| `INVARIANT_MAX_OUTPUT_BYTES` | 16384 | Stdout/stderr kept from those processes |
| `INVARIANT_MAX_RESOURCE_BYTES` | 1048576 | Largest resource or input file that will be read |
| `INVARIANT_LOG_LEVEL` | `INFO` | Stderr log level |

Ceilings are enforced (15 minutes for timeouts, 8 MiB for a resource) so a typo cannot pin the process or dump a huge file into the model.

## Security model

The trust boundary is the workspace, chosen by the operator who starts the process. It is not an MCP root advertised by the client.

- Paths are resolved with the SDK's `safe_join`. `..`, absolute paths outside the root, NUL bytes, and symlinks that leave the root are rejected.
- Subprocesses are argv lists with `shell=False`, a timeout, and a capped capture. The pipe is drained after the cap so a noisy child cannot deadlock the server.
- `run_failure_probe` accepts an allowlisted interpreter (`python`, `python3`, `python3.12`–`3.14`) plus a script inside the workspace, or an executable that itself lives in the workspace. Arguments that start with `-` are rejected, which blocks `python -c`. The resulting argv is `shlex.quote`d into one argument because **Maul's `maul test` runs the agent with `sh -c`**. Quoting keeps metacharacters as data.
- Holds' own runner also uses `sh -c` on `agent.command` from the suite file. `run_eval` does not take a command. The suite is trusted operator config, the same way Holds treats a Python grader as trusted local code. There is no extra sandbox around that command.
- Tool results omit raw model outputs and cap lists of attempts and decisions. Full documents are resources, and those reads are size-capped.
- The environment of the server is passed through to Holds agents and to Maul, because that is how those tools receive `OPENAI_API_KEY` and `MAUL_*`. The model cannot supply an env map.
- Streamable HTTP does not authenticate clients. The default bind is `127.0.0.1`. A non-loopback bind logs a warning.
- Vigil audit events are the redacted records Vigil already writes (no prompts, no `Authorization`).

Point the workspace at a repo you trust. A suite or a probe script in that repo can run code. That is the same power as running `holds run` or `maul test` yourself.

## Development

```bash
uv sync --extra dev
uv run ruff format .
uv run ruff check .
uv run pytest
```

CI runs those three on Python 3.12.

The tool-selection eval has two modes. `pytest` always runs a deterministic ranker over the real `tools/list` text, so a vague description fails offline. A live model run is skipped unless you set `OPENAI_API_KEY`, or `INVARIANT_MCP_EVAL_BASE_URL` for an OpenAI-compatible server (`INVARIANT_MCP_EVAL_MODEL`, `INVARIANT_MCP_EVAL_API_KEY`).

```bash
OPENAI_API_KEY=... uv run pytest tests/evals/test_tool_selection.py -m llm
```

## Known gaps

Checked against the sibling repos, not against the one-line descriptions of them.

- **Maul has no Python API.** The README lists a control plane (`/__maul/*`) and a Python CLI as planned. `list_scenarios` is a catalog compiled from `fault/mod.rs` at `0d18832a160b926a82f7503dbe1f42bf35a1e185`. When `maul` is installed, `maul --validate` is the authority for a config file. Otherwise a local mirror of v0.1 `RawConfig` is used and labeled `validation_source: local_mirror`. A newer Maul that adds a scenario will disagree with the catalog until this repo is updated.
- **`maul test` uses `sh -c`.** See `src/main.rs` in Maul. This server does not set `shell=True`. It quotes an argv and refuses flag arguments. That reduces the injection, and it does not remove Maul's shell.
- **Holds launches agents with `sh -c`** (`SubprocessAgentRunner`). Same upstream fact. The MCP boundary is the suite path.
- **`vigil policy from-maul` is not exposed.** Vigil's own contract is that suggestions are not auto-deployed (`suggested` → `approved` → `deployed`). A tool that wrote suggestion YAML and looked like a deploy would be the wrong shape.
- **Baseline promotion is not a tool.** `holds baseline` is how a person accepts a run. `compare_baseline` only reads.
- **`invariant-sh/vigil-platform` is not a public repository.** Vigil's README describes a commercial control plane and a future Rust gateway. This server talks to the open-source SDK only. The site repo (`invariant-sh/www`) is marketing copy plus demo JSON; it is not an API.
- **No HTTP authentication, no OAuth.** Loopback stdio is the intended setup.
- **Holds and Vigil are pinned by git commit** in `pyproject.toml` (`holds` `ded2539`, `vigil` `f07762b`) because they are not on PyPI. Bump the pin when you want newer behavior.

## License

Apache-2.0. See [LICENSE](LICENSE).
