
"""Quick test for the Foundry IQ knowledge agent."""
import os
import sys

from azure.ai.agents import AgentsClient
from azure.ai.agents.models import ListSortOrder
from azure.identity import AzureCliCredential

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

PROJECT_ENDPOINT = config.FOUNDRY_PROJECT_ENDPOINT
AGENT_ID_FILE = os.path.join(os.path.dirname(__file__), "foundry_knowledge_agent_id.txt")

QUESTIONS = [
    "What is the total loss threshold in East States vs South States?",
    "What fraud score threshold requires mandatory SIU referral?",
    "What SLA applies for first adjuster contact on a Tier 3 complex claim?",
]


def main():
    agent_id = config.FOUNDRY_KNOWLEDGE_AGENT_ID
    if not agent_id and os.path.exists(AGENT_ID_FILE):
        with open(AGENT_ID_FILE, encoding="utf-8") as f:
            agent_id = f.read().strip()
    if not agent_id:
        raise RuntimeError("Set FOUNDRY_KNOWLEDGE_AGENT_ID or create the agent first.")
    client = AgentsClient(endpoint=PROJECT_ENDPOINT, credential=AzureCliCredential())
    thread = client.threads.create()
    print("Thread:", thread.id)
    for question in QUESTIONS:
        client.messages.create(thread_id=thread.id, role="user", content=question)
        run = client.runs.create_and_process(thread_id=thread.id, agent_id=agent_id)
        print(f"\nQ: {question}")
        print("Run status:", run.status)
        if run.status == "failed":
            print("Error:", run.last_error)
            continue
        for message in client.messages.list(thread_id=thread.id, order=ListSortOrder.DESCENDING, limit=1):
            for content in message.content:
                if hasattr(content, "text"):
                    print("A:", content.text.value[:600].encode("ascii", "ignore").decode())


if __name__ == "__main__":
    main()
