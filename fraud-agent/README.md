# Fraud triage agent (LangGraph + HoneyHive)

A **CLI-only** demo of a banking-style fraud triage workflow built with **LangGraph** and instrumented using HoneyHive’s **OpenTelemetry-native** Python SDK (`HoneyHiveTracer`, OpenInference **`LangChainInstrumentor`** for LangGraph/Runnables, `@trace`, and explicit tool spans). It is meant for a short screencast: traces that look like a real agent, **online evaluator** story for **tool misuse**, and a path to **dataset curation** from bad traces.

First-time setup (keys, project, HoneyHive UI) is in **[SETUP.md](SETUP.md)**.

## Prerequisites (summary)

- Python 3.11+
- OpenAI API key ([platform.openai.com/api-keys](https://platform.openai.com/api-keys))
- HoneyHive account and project `fraud-triage-demo` (see SETUP.md)

## Run a single transaction

From the `fraud-agent` directory (with venv activated and `.env` filled in):

```bash
python -m src.main --transactions data/sample_transactions.json --id txn_001
```

Verbose node logging:

```bash
python -m src.main --transactions data/sample_transactions.json --id txn_005 --verbose
```

**Demo mode (deterministic evaluator signal):** set `FRAUD_AGENT_SIMULATE_TOOL_MISUSE=1` (or `freeze_account` / `contact_customer`) to emit a **scripted** forbidden mock tool span after the risk-scoring LLM completes. Default transaction filter is `txn_005`; override with `FRAUD_AGENT_SIMULATE_TOOL_MISUSE_TXN` or use `*` for all rows. Synthetic tool reasons are labeled **`[demo only]`** so traces are honest about scripted vs organic misuse.

The command prints the JSON **verdict**, then a **HoneyHive session id** and a **best-effort trace URL** (if the path differs in your workspace, open **Traces** in the app and search by session id or name).

## Run all samples (batch)

```bash
python scripts/run_batch.py
```

This runs all six rows in `data/sample_transactions.json` sequentially (six separate sessions in HoneyHive).

## What this demonstrates

- **Tracing** — One HoneyHive **session** per transaction; LangGraph/LangChain auto-capture (graph + node + Runnable LLM/tool) plus explicit `@trace` stage names and **tool** spans with tool names matching mock tools (`fetch_customer_history`, `fetch_device_context`, and forbidden action tools if the model calls them). Optional scripted forbidden-tool spans (`FRAUD_AGENT_SIMULATE_TOOL_MISUSE`) for reliable demo traces.
- **Online tool-misuse evaluation** — `src/evaluators.py` encodes allowlist and looping rules; you wire the same logic in the HoneyHive console as a Python evaluator and enable **online evaluation** so new production traces are scored (see SETUP.md §5).
- **Dataset curation** — Traces that fail policy checks (e.g. trap case calling `freeze_account`) can be turned into dataset rows for regression tests; see HoneyHive’s [curate from traces](https://docs.honeyhive.ai/datasets/dataset-curation) documentation.

## Cross-framework note

This demo runs on **LangGraph**, but the same **OpenTelemetry-native** pattern applies elsewhere: initialize `HoneyHiveTracer`, attach the right OpenInference instrumentor for your stack (e.g. [LangChain for LangGraph](https://docs.honeyhive.ai/v2/integrations/langgraph)), optionally add `@trace` for crisp business-stage names, and export spans in one session per logical run. Other stacks differ mainly in **which auto-instrumentation** you attach; sessions, span shape, and evaluator ideas stay the same.

## What’s mocked vs. real

| Piece | Mocked | Real |
| --- | --- | --- |
| Bank / processor APIs | All (`tools.py`) | — |
| Transactions | `data/sample_transactions.json` | — |
| LLM reasoning | — | OpenAI **gpt-4o** |
| Tracing / export | — | HoneyHive OTLP pipeline |
| Online evaluator | Logic in repo; **scoring runs in HoneyHive** after UI setup | — |

The evaluator logic is intentionally small and demo-oriented, not a production policy engine.

## References

- [Tracing quickstart](https://docs.honeyhive.ai/v2/introduction/tracing-quickstart) (`honeyhive>=1.0.0rc0`)
- [LangGraph integration](https://docs.honeyhive.ai/v2/integrations/langgraph) (`LangChainInstrumentor().instrument(tracer_provider=tracer.provider)`)
- [Tracing introduction](https://docs.honeyhive.ai/v2/tracing/introduction)
- [Evaluation introduction](https://docs.honeyhive.ai/v2/evaluation/introduction)
- [Python evaluators](https://docs.honeyhive.ai/evaluators/python)
