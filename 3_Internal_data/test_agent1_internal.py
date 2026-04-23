from agent1_internal import agent1_internal, agent1_internal_batch

## Single file (any supported format)
result = agent1_internal("3_Internal_data/Catchup_with_Sunil Daga.txt")
# results = agent1_internal(["raw/file1.md", "notes/call.docx"])  # list
# results = agent1_internal("raw/")  # folder

## Explicit batch
# results = agent1_internal_batch("raw/")

## Chained from transcript_cleaner
# from transcript_cleaner import transcript_cleaner

## clean = transcript_cleaner("raw/Vishal_Agarwal.md")
# result = agent1_internal(clean.json_path)

# result (single)  -> InternalResult
# result.signals_path    -> path to output JSON
# result.total_signals   -> int
# result.classifier_used -> "hf_api" | "rule_based" | "hf_api+rule_based"
# result.metadata        -> dict (the metadata block)
# result.signals         -> List[SignalRecord]