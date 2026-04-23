from transcript_cleaner_claude import transcript_cleaner

result = transcript_cleaner("input/Catchup_with_Sunil Daga.txt")

# result.clean_txt_path  → path to the clean .txt file
# result.json_path       → path to the structured turns JSON
# result.turns           → List[Turn] dataclass
# result.stats           → cleaning report dict

# Chain directly into agent1:
from agent1_ingestion import agent1_ingestion
ingestion_result = agent1_ingestion(result.clean_txt_path)