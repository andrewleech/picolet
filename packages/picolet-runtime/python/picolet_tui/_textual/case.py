import re
from typing import Match, Pattern


def camel_to_snake(
    name: str, _re_snake: Pattern[str] = re.compile("[a-z][A-Z]")
) -> str:
    """Convert name from CamelCase to snake_case.

    Args:
        name: A symbol name, such as a class name.

    Returns:
        Name in snake case.
    """

    def repl(match: Match[str]) -> str:
        lower: str
        upper: str
        # group(0), not group(): MicroPython's re requires the
        # explicit group index.
        lower, upper = match.group(0)  # type: ignore
        return f"{lower}_{upper.lower()}"

    return _re_snake.sub(repl, name).lower()
