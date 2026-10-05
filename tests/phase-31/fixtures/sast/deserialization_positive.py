import pickle


def decode_untrusted_payload(payload: bytes) -> object:
    return pickle.loads(payload)
