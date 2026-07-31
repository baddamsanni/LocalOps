"""Build the post-execution LLM explanation prompt."""

from __future__ import annotations

EXPLAIN_SYSTEM_PROMPT = """
You explain shell command results to a developer in plain English.
Respond with 1-3 plain sentences only. Never use the CMD: format.
Never invent or suggest new shell commands — only explain the output given.
When a working directory is provided, use that exact path — do not guess
or invent a different folder.
""".strip()

# Truncate oversized shell dumps before sending them to the LLM.
EXPLAIN_OUTPUT_LIMIT = 2000


def prepare_output_for_explain(raw_output: str) -> str:
    text = raw_output if raw_output is not None else ""
    if len(text) <= EXPLAIN_OUTPUT_LIMIT:
        return text
    return (
        text[:EXPLAIN_OUTPUT_LIMIT]
        + "\n(output truncated for explanation)"
    )


def build_explain_prompt(
    command: str,
    raw_output: str,
    exit_code: int,
    working_directory: str | None = None,
) -> str:
    clipped = prepare_output_for_explain(raw_output)
    if working_directory:
        lead = (
            f"The command `{command}` was run in directory "
            f"`{working_directory}` and produced this output:"
        )
    else:
        lead = f"The command `{command}` was run and produced this output:"
    return (
        f"{lead}\n"
        f"\n"
        f"{clipped}\n"
        f"\n"
        f"Exit code: {exit_code}\n"
        f"\n"
        f"Explain in 1-3 plain sentences what this means for the user. Do\n"
        f"not repeat the raw output. Do not use the CMD: format here — just\n"
        f"give a plain explanation. If you mention where it ran, use only\n"
        f"the directory given above — do not invent another path."
    )
