from agent1_ingestion import agent1_ingestion

result = agent1_ingestion(
    "input/Call with Jinay Sawla_Version2.md",
    entity_name="Jinay Sawla",
    source_type="Internal"
)
print(f"Processed {result.total_segments} entries")
for entry in result.entries:
    print(f"- {entry.signal_type}: {entry.content[:50]}")