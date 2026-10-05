def evaluate_fixed_expression() -> object:
    return eval("2 + 2", {"__builtins__": {}}, {})
