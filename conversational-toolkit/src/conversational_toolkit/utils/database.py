import uuid


def generate_uid() -> str:
    return str(uuid.uuid4())
