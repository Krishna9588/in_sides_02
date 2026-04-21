from agent1_production_ingestion_3 import agent1_production_ingestion

result = agent1_production_ingestion(
    input_file='input/Catchup_with_Sunil Daga.txt',
    entity_name='Weekly Standup',
    source_type='Internal'
)

print(f"Processed {result.total_segments} entries")
for entry in result.entries:
    print(f"- {entry.signal_type}: {entry.content[:100]}")