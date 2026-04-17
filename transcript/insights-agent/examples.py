# Example 1: Process Jinay's call
from agent1_research_ingestion import *

result = process_founder_call('Call with Jinay Sawla_Version2.md')

# Example 2: Process multiple calls at once
results = process_batch([
    'Call with Jinay Sawla_Version2.md',
    'Call with Shashank Agarwal_Version1.md'
])

# Example 3: Extract specific insights
problems = extract_problems_from_result(result)
decisions = extract_decisions_from_result(result)
competitors = extract_competitive_intelligence(result)

# Example 4: Custom configuration
custom_config = load_config('config.yaml')
custom_config['stage2']['classification_model'] = 'microsoft/deberta-large-mnli'
result = process_with_config('call.md', custom_config)