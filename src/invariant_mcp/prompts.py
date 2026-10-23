"""User-invoked prompt templates. The host, not the model, selects these."""

from __future__ import annotations

from mcp.server import MCPServer


def register_prompts(mcp: MCPServer) -> None:
    """Register prompts that sequence the Invariant tools."""

    @mcp.prompt(title="Gate a change with Holds")
    def gate_a_change(
        suite: str,
        baseline: str,
        report: str = "artifacts/holds_report.json",
    ) -> str:
        """Ask for a Holds run and a baseline comparison before calling a change good."""
        return (
            "Use the Invariant tools to gate this change.\n"
            f"1. Call run_eval with suite {suite!r} and report {report!r}.\n"
            "2. Read the report resource when you need per-attempt detail.\n"
            f"3. Call compare_baseline with candidate {report!r} and baseline {baseline!r}.\n"
            "4. Report whether the candidate passed, quoting pass_rate and the deltas.\n"
            "Leave baseline promotion to a person."
        )

    @mcp.prompt(title="Investigate a Maul failure")
    def investigate_failure(config: str, script: str) -> str:
        """Ask for the Maul scenario list and a failure probe of one workspace script."""
        return (
            "Investigate how this agent behaves when its model endpoint fails.\n"
            f"1. Call list_scenarios with config {config!r} and summarize the enabled faults.\n"
            "2. Call run_failure_probe with that config and "
            f"agent ['python3', {script!r}] when the maul binary is installed.\n"
            "3. When status is unavailable, say Maul is not installed. "
            "Do not invent a reliability report.\n"
            "4. When report_uri is present, read that resource and describe faults_injected, "
            "unrecovered sessions, and budget rejections.\n"
            "HTTP recovery is not task success. Task success is a Holds run_eval question."
        )
