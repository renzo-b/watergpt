"""
Entroply — minimal agent loop.

Deliberately small and readable: this file IS the product's core, so you should
be able to hold all of it in your head. Everything here is hand-written on
purpose. Tools live in tools.py.

Usage:
    python agent.py "Contact tank is 150 m3, flow 45 L/s, residual 0.8 mg/L..."
"""

import os
import sys
import base64
import mimetypes

from dotenv import load_dotenv
from anthropic import Anthropic
from system_prompt import SYSTEM_PROMPT
from tools import TOOL_SCHEMAS, dispatch

load_dotenv()  # reads .env from cwd or walks up parent dirs

API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not API_KEY:
    raise SystemExit(
        "ANTHROPIC_API_KEY is not set. Put it in a .env file next to this "
        "script, or export it in your shell."
    )

# Set to whichever model you're using. Swap freely — the loop doesn't care.
MODEL = "claude-sonnet-4-5"
MAX_TURNS = 10  # hard stop so a confused agent can't loop forever

def _image_block(path: str) -> dict:
    """Read a local image into an Anthropic image content block."""
    media_type = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode()
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def run_agent(question, attachment=None, plant_id="demo", verbose=True,
              truncate=300):
    """Run the tool-use loop until the model stops asking for tools.

    Returns (final_text, tool_calls) where tool_calls is a list of
    (tool_name, tool_input) in the order they were invoked. The tool_calls
    list is what the eval harness grades on — wrong tool choice is the most
    common early failure, and it's invisible if you only look at the answer.
    """
    client = Anthropic(api_key=API_KEY)

    content = []
    if attachment:
        content.append(_image_block(attachment))
    content.append({"type": "text", "text": question})

    messages = [{"role": "user", "content": content}]
    tool_calls = []

    for _ in range(MAX_TURNS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        if verbose:
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    print(f"\n  [assistant] {block.text.strip()[:truncate]}")
                elif block.type == "tool_use":
                    print(f"  [tool call] {block.name}({block.input})")

        if response.stop_reason != "tool_use":
            text = "\n".join(b.text for b in response.content if b.type == "text")
            return text, tool_calls

        # Append the assistant turn verbatim, then answer every tool_use block.
        messages.append({"role": "assistant", "content": response.content})

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_calls.append((block.name, block.input))
            try:
                output = dispatch(block.name, block.input, plant_id=plant_id)
                is_error = False
            except Exception as e:
                # Hand errors back to the model rather than crashing — it can
                # often recover by fixing its arguments.
                output, is_error = f"Tool error: {e}", True
            if verbose:
                print(f"  [tool result] {str(output)[:truncate]}")
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(output),
                    "is_error": is_error,
                }
            )

        messages.append({"role": "user", "content": results})

    return "[MAX_TURNS exceeded without a final answer]", tool_calls


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "What does CT mean and how do I calculate it?"
    answer, calls = run_agent(q)
    print("\n" + "=" * 70)
    print(answer)
    print("=" * 70)
    print(f"tools used: {[c[0] for c in calls] or 'none'}")
