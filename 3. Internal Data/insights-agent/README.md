pip install -r requirements.txt

# Download SpaCy model
python -m spacy download en_core_web_sm

pip install -r requirements.txt
python -m spacy download en_core_web_sm

# Create sample transcript
cat > sample_call.md << 'EOF'
Jinay: The issue is that retail traders lose money in F&O.
Akshay: How do we solve this?
Jinay: We need education integrated with the platform.
Shashank: Agreed. That should be the approach.
EOF

# Run pipeline
python agent1_research_ingestion.py

ls -la outputs/
# See JSON and Markdown files generated

ls -la cache/
# See cache files

### Project result output ==> 
from agent1_research_ingestion import Agent1Pipeline

agent = Agent1Pipeline()
result = agent.process_file('Call with Jinay Sawla_Version2.md', output_format='both')

result = agent.process_file('meeting_recording.mp3', output_format='both')

# Script automatically:
# 1. Transcribes audio to text (using Whisper AI)
# 2. Identifies speakers from transcription
# 3. Segments and classifies
# 4. Outputs JSON + Markdown

result = agent.process_file('call_notes.docx', output_format='both')

# Script automatically:
# 1. Extracts text from DOCX
# 2. Parses speakers and content
# 3. Segments and classifies
# 4. Outputs results

result = agent.process_file('call_data.xlsx', output_format='both')

# Script automatically:
# 1. Reads Excel sheet
# 2. Converts to text format
# 3. Segments and classifies
# 4. Outputs results


### Access the results

from agent1_research_ingestion import Agent1Pipeline

agent = Agent1Pipeline()
result = agent.process_file('call.md', output_format='both')

# Extract specific information
print(f"Total segments: {result['stage2']['total_segments']}")
print(f"Processing time: {result['total_processing_time']:.2f}s")

# Get all problems
problems = [
    seg for seg in result['stage2']['segments']
    if seg['classification']['primary_type'] == 'problem_statement'
]

print(f"\nProblems found ({len(problems)}):")
for p in problems:
    print(f"  - {p['raw_text'][:100]}...")

# Get all decisions
decisions = [
    seg for seg in result['stage2']['segments']
    if seg['classification']['primary_type'] == 'decision'
]

print(f"\nDecisions found ({len(decisions)}):")
for d in decisions:
    print(f"  - {d['raw_text'][:100]}...")

# Get sentiment distribution
sentiments = {}
for seg in result['stage2']['segments']:
    sentiment = seg['sentiment']['sentiment']
    sentiments[sentiment] = sentiments.get(sentiment, 0) + 1

print(f"\nSentiment distribution:")
for sentiment, count in sentiments.items():
    print(f"  {sentiment}: {count}")

# Access file paths
print(f"\nOutputs saved to:")
print(f"  JSON: {result['output_paths']['json']}")
print(f"  Markdown: {result['output_paths']['markdown']}")
