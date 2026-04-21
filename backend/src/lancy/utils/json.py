import re

from partial_json_parser import loads as partial_json_loads

# Strip ```json ... ``` or ``` ... ``` code fences that some models add despite json_mode
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def _escape_literal_newlines(text: str) -> str:
    """Escape bare newlines/carriage-returns inside JSON string values.

    Models sometimes emit literal line breaks in string values, which is
    invalid JSON and breaks both json.loads and partial_json_loads.
    """
    result: list[str] = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            result.append(ch)
            escape_next = False
        elif ch == "\\" and in_string:
            result.append(ch)
            escape_next = True
        elif ch == '"':
            in_string = not in_string
            result.append(ch)
        elif ch == "\n" and in_string:
            result.append("\\n")
        elif ch == "\r" and in_string:
            result.append("\\r")
        else:
            result.append(ch)
    return "".join(result)


def parse_llm_json_stream(input_str: str) -> dict[str, str] | None:
    # Remove markdown code fences before attempting JSON parse
    cleaned = _CODE_FENCE_RE.sub("", input_str).strip()
    try:
        opening_bracket_index = cleaned.index("{")
        json_part = _escape_literal_newlines(cleaned[opening_bracket_index:])
        json_object = partial_json_loads(json_part)
        return json_object

    except ValueError as e:
        # No "{" found — plain-text answer
        if len(input_str) > 10 and "substring not found" in str(e):
            return {"answer": input_str}
        return {}
    except Exception:
        # partial_json_loads raised something unexpected
        return {}
