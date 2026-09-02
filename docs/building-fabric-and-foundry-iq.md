# Building Fabric IQ and Foundry IQ

`deploy_solution.ps1` builds Fabric IQ (sample data, lakehouse, ontology, data agent) and Foundry IQ
(search index, knowledge agent) as steps 1-4 of the default flow, automatically skipping any of them
whose artifact already exists (see the "Quick start / deployment" section in the top-level README for
the exact skip conditions). This doc describes what each step does if you want to run them individually,
or reuse pre-existing Fabric IQ / Foundry IQ assets instead of building them here.

## Sequence

1. `python datagen/generate_fnol_data.py`
2. `python datagen/load_to_lakehouse.py`
3. `python fabric/create_ontology.py`
4. `python fabric/configure_data_agent.py`
5. Optionally, after Fabric auto-generates a graph model and you know `FABRIC_GRAPH_MODEL_ID`, run `python fabric/configure_data_agent_ontology.py`
6. `python foundry/build_search_index.py`
7. `python foundry/create_foundry_agent.py`
8. `python foundry/test_foundry_agent.py`
9. Continue with `.\deploy_solution.ps1` (steps 5-13 pick up from there)

To reuse pre-existing Fabric IQ / Foundry IQ assets instead of building them, set `FABRIC_DATA_AGENT_ID`
(and/or `FABRIC_ONTOLOGY_ID`) and `FOUNDRY_KNOWLEDGE_AGENT_ID` in `.env` before running
`deploy_solution.ps1` — steps 3 and 4 will detect those values and skip the build automatically.

## Notes

- Reuse the same environment variable names already consumed by the orchestrator: `FABRIC_WORKSPACE_ID`, `FABRIC_DATA_AGENT_ID`, and `FOUNDRY_KNOWLEDGE_AGENT_ID`.
- `foundry/create_foundry_agent.py` writes `foundry/foundry_knowledge_agent_id.txt`, which `foundry/create_orchestrator_agent.py` can already consume.
- `fabric/create_ontology.py` writes `fabric/ontology_id.txt`; copy that value into `.env` as `FABRIC_ONTOLOGY_ID` if you want to persist it for later reruns.
