# 1. Import from another script (Agent 2 will call this)
from agent_1.agent1_orchestrator import agent1_orchestrator

result = agent1_orchestrator(
    project_name   = "Angel One",
    domain         = "https://www.angelone.in/",
    run            = ["all"],
    internal_path  ="agent_1/transcripts/angelone",
)

# Feed directly into Agent 2
signals = result.signals   # unified list, all 6 agents