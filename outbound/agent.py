"""Core agent loop using Anthropic tool-calling API.

This is the only file that talks to the LLM. Everything else is tools and prompts.
The loop is simple: send messages → get response → execute any tool calls → repeat.
"""

import os
from typing import Callable, Optional

import anthropic


DEFAULT_MODEL = "claude-sonnet-4-20250514"


def run_agent(
    system_prompt: str,
    user_message: str,
    tools: list[dict],
    execute_tool: Callable[[str, dict], str],
    model: Optional[str] = None,
    max_turns: int = 30,
) -> list[dict]:
    """
    Run an agent loop with tool calling.

    Args:
        system_prompt: System instructions for the agent.
        user_message: The user's task/prompt.
        tools: List of Anthropic tool definitions (name, description, input_schema).
        execute_tool: Callback to execute a tool: (name, input_dict) -> result_string.
        model: Anthropic model ID. Defaults to OUTBOUND_MODEL env var or claude-sonnet.
        max_turns: Maximum number of tool-calling rounds before stopping.

    Returns:
        Full message history (for logging/debugging).
    """
    client = anthropic.Anthropic()
    model = model or os.environ.get("OUTBOUND_MODEL", DEFAULT_MODEL)
    messages = [{"role": "user", "content": user_message}]

    for turn in range(max_turns):
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )

        # Parse response into serializable content + collect tool calls
        assistant_content = []
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
                print(f"\n  Agent: {block.text}\n")
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
                tool_calls.append(block)

        messages.append({"role": "assistant", "content": assistant_content})

        # No tool calls means the agent is done
        if not tool_calls:
            break

        # Execute each tool call and collect results
        tool_results = []
        for tc in tool_calls:
            print(f"  -> {tc.name}({_summarize_input(tc.input)})")
            try:
                result = execute_tool(tc.name, tc.input)
            except Exception as e:
                result = f"Error: {type(e).__name__}: {e}"
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": str(result),
            })

        messages.append({"role": "user", "content": tool_results})

    else:
        print(f"\n  [Agent stopped after {max_turns} turns]")

    return messages


def _summarize_input(input_dict: dict) -> str:
    """One-line summary of tool input for console output."""
    parts = []
    for k, v in input_dict.items():
        val = str(v)
        if len(val) > 80:
            val = val[:77] + "..."
        parts.append(f"{k}={val}")
    return ", ".join(parts)
