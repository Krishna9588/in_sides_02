from agent1_research_ingestion import Agent1Pipeline

# Initialize
agent = Agent1Pipeline()

# Process your transcript
result = agent.process_file(
    input_file='Call with Jinay Sawla_Version2.md',
    human_in_loop=False,
    output_format='both'  # JSON + Markdown
)

result2 = agent.process_file(
    input_file='meeting_recording.mp3',
    human_in_loop=False,
    output_format='both'
)

result3 = agent.process_file(
    input_file='call_notes.docx',
    human_in_loop=False,
    output_format='both'
)


# Access results
print(f"✓ Processed {result['metadata']['total_segments']} segments")
print(f"✓ Output saved to: {result['output_paths']}\n")
print(f"✓ Processed {result2['metadata']['total_segments']} segments")
print(f"✓ Output saved to: {result2['output_paths']}\n")
print(f"✓ Processed {result3['metadata']['total_segments']} segments")
print(f"✓ Output saved to: {result3['output_paths']}\n")
