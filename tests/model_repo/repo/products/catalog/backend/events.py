from posthog.kafka_client.client import produce

ENTRY_CREATED = "catalog_entry_created"


def emit_created(entry_id: int) -> bool:
    return produce(ENTRY_CREATED, str(entry_id).encode())
