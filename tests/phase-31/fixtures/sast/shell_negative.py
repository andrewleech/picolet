import json


def parse_payload(payload: str) -> object:
    return json.loads(payload)
