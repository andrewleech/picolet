import json


def decode_untrusted_payload(payload: str) -> object:
    return json.loads(payload)
