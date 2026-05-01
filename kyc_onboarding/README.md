# KYC onboarding agent (LangGraph + HoneyHive)

A **CLI-only** companion demo to [`fraud-agent/`](../fraud-agent/README.md): Know Your Customer (KYC) **onboarding intake** modeled as a linear LangGraph workflow. Each applicant run emits one HoneyHive **session** with explicit **tool** and **model** spans. Use it next to fraud triage to show the **same instrumentation and evaluator playbook** reused for a different product surface.

Detailed setup steps for **this folder** → **[SETUP.md](SETUP.md)**. First-time HoneyHive keys, projects UI, and the broader take-home framing → **[../fraud-agent/README.md](../fraud-agent/README.md)**.

## Prerequisites (summary)

- Python 3.11+
- OpenAI API key and HoneyHive API key on the **repo-root** `.env` (same file as fraud-agent)
- **`HONEYHIVE_PROJECT`** set to your **KYC** HoneyHive project (e.g. `kyc-onboarding`) — same env var fraud-agent uses; **swap the value in `.env`** when changing demos ([SETUP.md](SETUP.md))
- Fraud-agent setup effectively complete ([fraud-agent/SETUP.md](../fraud-agent/SETUP.md))

## Run one application

From `kyc_onboarding/`:

```bash
pip install -r requirements.txt

python -m src.main --applications data/sample_applications.json --id kyc_001
```

Verbose logging:

```bash
python -m src.main --applications data/sample_applications.json --id kyc_005 --verbose
```

The CLI prints the structured **onboarding packet** JSON, then a **HoneyHive session id** and a **best-effort trace URL**.

## Batch (all six demos)

```bash
python scripts/run_batch.py
```

## What this demonstrates alongside the fraud agent

Both agents share the **same OpenTelemetry-native HoneyHive tracer pattern**, **REST `/session/start` + `/events`** tracing by default (optional OTLP via `HONEYHIVE_ENABLE_OTLP=1`), **`LangChainInstrumentor`** wiring for LangGraph, and **tool misuse** logic in `evaluators.py` you mirror as online evaluators. The substantive differences across “many agents” are **tool allowlists**, domain graph/mocks, and **which HoneyHive project you aim at** — here by setting **`HONEYHIVE_PROJECT`** before a run.

References: [Tracing quickstart](https://docs.honeyhive.ai/v2/introduction/tracing-quickstart), [Tracing introduction](https://docs.honeyhive.ai/v2/tracing/introduction), [Evaluation introduction](https://docs.honeyhive.ai/v2/evaluation/introduction), [LangGraph integration](https://docs.honeyhive.ai/v2/integrations/langgraph).

## Deterministic trap-case misuse for recordings

Organic LLM misuse on **`kyc_005`** may not fire every run. Mirror the fraud-agent story with:

```bash
KYC_AGENT_SIMULATE_TOOL_MISUSE=1 python -m src.main --applications data/sample_applications.json --id kyc_005
```

Default filter is **`kyc_005`**; set **`KYC_AGENT_SIMULATE_TOOL_MISUSE_APPLICANT=*`** to script every applicant in a batch demo.

## Deterministic PII leak for recordings

Show HoneyHive surfacing a **PII regurgitation** in the **`risk_assessment`** model span:

```bash
PII_LEAK=1 python -m src.main --applications data/sample_applications.json --id kyc_002
```

Appends a clearly-labeled `[demo only — synthetic PII]` sentence containing a fake bank account / routing number to the risk-assessment narrative output. Default filter is **`kyc_002`**; widen to all applicants with **`PII_LEAK_APPLICANT=*`**. Numbers are synthetic — do not use real account data.

## What’s mocked vs. real

| Piece | Mocked | Real |
| --- | --- | --- |
| Verification APIs | All (`tools.py`) | — |
| Applications | `data/sample_applications.json` | — |
| LLM reasoning | — | OpenAI **gpt-4o** |
| Telemetry | — | HoneyHive REST (default) ± OTLP |

The **online evaluator** is real once configured in the HoneyHive console, but tuned for demonstration (allowlist-centric misuse), not jurisdictional compliance sign-off.
