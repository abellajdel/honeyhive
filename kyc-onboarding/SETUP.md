# KYC onboarding demo — setup (folder-specific)

Assume you already followed **[../fraud-agent/SETUP.md](../fraud-agent/SETUP.md)** (Python env and HoneyHive API key).

The onboarding CLI reads the **mono-repo root** `.env`: the **`honeyhive/`** directory that contains both `fraud-agent/` and `kyc_onboarding/` (same pattern as fraud-agent).

If you set **`HONEYHIVE_PROJECT=kyc-onboarding`** in that file but traces still land on **`fraud-triage-demo`**, you almost certainly have **`export HONEYHIVE_PROJECT=...`** (or an IDE run config) still forcing the old value — standard dotenv does not override pre-set env vars. This agent reloads the root `.env` with **`override=True`** so the file wins; if it still misroutes, run **`unset HONEYHIVE_PROJECT`** in the shell and try again.

## 1. Prerequisites

- Same machine / venv conventions as fraud-agent.
- In that root `.env`, set **`HONEYHIVE_PROJECT`** to the HoneyHive **KYC** project slug (often `kyc-onboarding`) **when running this agent**. Fraud-agent uses **the same variable**; swap the value in `.env` when you switch demos (or use separate shells/copies if you prefer).

## 2. HoneyHive project

Create a HoneyHive project for KYC traces (e.g. **`kyc-onboarding`**) when you’re ready for this demo, and set **`HONEYHIVE_PROJECT=kyc-onboarding`** (or matching slug) before KYC runs.

Links (product docs):

- [Projects overview](https://docs.honeyhive.ai/v2/concepts)

## 3. Install dependencies

```bash
cd kyc_onboarding
pip install -r requirements.txt
```

Use either the fraud-agent virtualenv or an isolated env; both approaches are fine.

References: [Tracing quickstart](https://docs.honeyhive.ai/v2/introduction/tracing-quickstart).

## 4. First run

Set **`HONEYHIVE_PROJECT`** to your KYC project, then:

```bash
python -m src.main --applications data/sample_applications.json --id kyc_001
```

**Terminal:** progress lines prefixed `[kyc-onboarding]`, then JSON **onboarding packet**, a blank line, **`HoneyHive session id`**, and **`HoneyHive trace URL`**.

**HoneyHive UI:** open [Traces / sessions](https://app.honeyhive.ai/traces/sessions); select the project matching **`HONEYHIVE_PROJECT`**; confirm a session named `kyc-onboarding-kyc_001`.

> **TODO:** If your workspace nests sessions under another tab name, match the labels in-console and rely on searching by **`session id`** printed in your terminal ([Tracing introduction](https://docs.honeyhive.ai/v2/tracing/introduction)).

## 5. Online evaluator

Follow the fraud-agent evaluator section conceptually (**[../fraud-agent/SETUP.md §5](../fraud-agent/SETUP.md#5-configuring-the-online-evaluator-honeyhive-ui)**) but create/configure the evaluator in the HoneyHive UI under **the same project you used for `HONEYHIVE_PROJECT`** on KYC runs (e.g. `kyc-onboarding`).

- Port logic parallel to **`src/evaluators.py`** (`kyc_onboarding/src/evaluators.py`).
- **Allowlist**: `verify_identity_document`, `screen_sanctions_lists`, `verify_address` only.
- Toggle **online evaluation** per [Python evaluators — online evaluation](https://docs.honeyhive.ai/evaluators/python).

> **TODO:** Exact “Evaluators” / “Metrics” entry points evolve; mirror the evaluator IDE path linked from [evaluation introduction](https://docs.honeyhive.ai/v2/evaluation/introduction).

## 6. Batch runner

Ensure **`HONEYHIVE_PROJECT`** is your KYC project, then:

```bash
python scripts/run_batch.py
```

Six sessions are created from `data/sample_applications.json`.

### Trap-case (`kyc_005`)

Notes may entice forbidden tools — misuse is non-deterministic unless you simulate it:

```bash
KYC_AGENT_SIMULATE_TOOL_MISUSE=1 python -m src.main --applications data/sample_applications.json --id kyc_005
```

Or **`KYC_AGENT_SIMULATE_TOOL_MISUSE_APPLICANT=*`** for every row in batch.

### PII leak demo (`kyc_002`)

Forcing a **synthetic** PII regurgitation in the **`risk_assessment`** model span (the canonical place an LLM would leak account data):

```bash
PII_LEAK=1 python -m src.main --applications data/sample_applications.json --id kyc_002
```

Synthetic numbers (`8412-5567-2199` / `021-000-021`) and a **`[demo only — synthetic PII]`** prefix make the leak unambiguous in HoneyHive — perfect for showing a PII evaluator firing on the **`risk_assessment`** event. Default filter is **`kyc_002`**; widen with **`PII_LEAK_APPLICANT=*`**.

## 7. Optional OTLP (dual export)

```bash
HONEYHIVE_ENABLE_OTLP=1 python -m src.main --applications data/sample_applications.json --id kyc_001
```

`HONEYHIVE_PROJECT` selects the OTLP destination; REST remains default unless **`HONEYHIVE_DISABLE_REST=1`**.

Same OTLP tuning env vars as fraud-agent (**`HH_OTLP_HTTP_TIMEOUT`**, **`HH_OTLP_EXPORT_ATTEMPTS`**, **`HONEYHIVE_OTLP_BATCH`**, **`HONEYHIVE_SKIP_FLUSH`**).
