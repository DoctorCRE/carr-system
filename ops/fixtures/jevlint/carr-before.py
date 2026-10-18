def _exact(value: Any, fields: set[str], name: str) -> dict:
    return contract._expect_exact(value, fields, name)
