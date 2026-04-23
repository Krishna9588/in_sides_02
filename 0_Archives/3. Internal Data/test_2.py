# As module (from another script)
from agent1_ingestion_2 import agent1_ingestion
import os

result = agent1_ingestion(
    "input/Call with Shashank Agarwal_Version2.md",
    entity_name="Vishal Agarwal",
    hf_token="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"
)

print(f"Total signals: {result['metadata']['total_signals']}")
for entry in result['entries'][:3]:
    print(f"- {entry['signal_type']}: {entry['content'][:80]}")