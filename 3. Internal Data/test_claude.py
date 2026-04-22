# Standalone — or as Stage 0 before agent1
from transcript_cleaner_claude import transcript_cleaner
from agent1_ingestion import agent1_ingestion

clean  = transcript_cleaner("/input/Catchup with Sunil Daga.md", output_dir="cleaned")
# result = agent1_ingestion(clean.clean_txt_path)