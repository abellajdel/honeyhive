# Assumptions

## 1. Tech stack

- **Cloud:** AWS -primary. Azure as a secondary footprint for Microsoft 365 and some compliance workloads.
- **LLM gateway:** Internally built on AWS Bedrock as the model backbone, with a thin proxy layer the platform team owns for routing, key management, logging, and rate limits. Anthropic and Amazon Nova as primary models; OpenAI via Azure for specific workloads.
- **Agent frameworks (the 5 in production):** Heterogeneous. LangGraph (2 agents), AWS Bedrock Agents (2 agents), CrewAI (1 agent).
- **Guardrails:** AWS Bedrock Guardrails for input/output filtering, plus a homegrown PII redaction service in front of the gateway.
- **Observability:** Datadog for infrastructure and APM (Application Performance Monitoring). Splunk for security/audit logs. No agent-native observability today.
- **Data:** Snowflake as the analytics warehouse. Customer data in a mix of mainframe (core banking), Salesforce (CRM, Customer Relationship Management), and various line-of-business systems. Vector store is OpenSearch.
- **CI/CD (Continuous Integration / Continuous Deployment):** GitHub Enterprise + GitHub Actions. Internal Backstage portal for service catalog.
- **Identity:** Okta for workforce SSO (Single Sign-On), Entra ID for M365.

## 2. Team structure (aka Stakeholders)

- **AI Platform team:** ~15 people, under the CTO org. Owns the LLM gateway, guardrails service, shared eval tooling, and developer experience for AI use cases.
- **Agent-building teams:** Embedded in business lines — fraud, retail banking ops, commercial lending, customer service, compliance ops. Each has 2–6 engineers. They consume the platform but make their own framework choices today.
- **Responsible AI:** ~8 people, under the Chief Risk Officer. Reports through MRM (Model Risk Management), which already governs traditional ML models under SR 11-7 (Federal Reserve Supervisory Letter 11-7, the regulatory guidance on model risk management).
- **Procurement & Legal:** Centralized. Procurement runs on Coupa. Legal has a dedicated Technology Contracts team.
- **CISO (Chief Information Security Officer) org:** Separate from RAI. Owns data security, third-party risk reviews, and the security questionnaire process.
- **Decision-makers for this deal:** Economic buyer is the CTO. Technical champion is the AI Platform Director. Required approvers are RAI, CISO (third-party risk), and Legal.

## 3. Responsible AI concerns

The three failure modes RAI has cited as blockers for production deployment:

- **Hallucinations** — agents fabricating account details, policy interpretations, regulatory citations, or product information. RAI wants statistical evidence of hallucination rates per agent per release, not spot-check QA (Quality Assurance).
- **PII leakage** — customer data appearing in places it shouldn't: model context windows shared across sessions, log streams, prompts sent to third-party models, downstream tool calls, or generated outputs returned to unauthorized users. RAI wants both prevention evidence and detection evidence.
- **Unauthorized actions / tool misuse** — agents calling tools they shouldn't, with parameters they shouldn't, in sequences they shouldn't. RAI wants trace-level evidence that every tool call in production was within policy, and a way to detect drift over time.

Out of scope for now but on the horizon: prompt injection, jailbreaks, model drift across versions, and bias in customer-impact decisions.

## 4. Procurement process

- **Standard path:** 6–9 months for new vendors. Sequence: security questionnaire (CAIQ, Consensus Assessments Initiative Questionnaire + SIG-Lite, Standardized Information Gathering Lite + custom bank addendum) → third-party risk assessment by CISO org → MRM model risk review for AI vendors → Legal redlines on MSA (Master Services Agreement) + DPA (Data Processing Agreement) → Procurement negotiation → final sign-off.
- **AI vendors get extra scrutiny:** AI-specific addendum covering training data usage, model provenance, evaluation methodology, and incident disclosure. Adds 4–6 weeks.
- **Data residency:** US-only. Self-hosted deployment required for any vendor touching production traces.
- **Spend thresholds:**
  - Under $50K annual: department budget with director sign-off, light procurement review (~2 weeks), short-form contract.
  - $50K–$250K: standard procurement, expedited path possible, ~3 months.
  - Over $250K: full enterprise process, 6–9 months.
- **Existing vendor leverage:** Anthropic is already approved via Bedrock. [HoneyHive's self-hosted deployment in customer VPC](https://docs.honeyhive.ai/v2/setup/self-hosted) avoids most of the data-handling questions that slow AI vendors down.
- **Champion's authority:** AI Platform Director can approve up to $50K out of their own budget for tooling without a formal business case. Above that requires CTO sign-off and a full procurement cycle.



# Part I

## Q1 — How would you communicate the ROI of the platform?

Companies buy tools for one of three reasons: to make money, to save on cost, or to reduce risk. HoneyHive sits primarily in risk reduction — and for this customer, it's exactly the right bucket.

But for this account, risk reduction is the *unlock* for the other two. The bank has 5 agents stuck in pre-production and 45 more queued. RAI is the gate. Until agents pass that gate, the business value of every agent is zero. HoneyHive is the fastest, most defensible path to opening it.

### The primary story: risk reduction

RAI has blocked deployments because they don't have the evidence they need on hallucinations, PII leakage, and unauthorized tool use. HoneyHive produces that evidence continuously and at trace level — not as a one-time pre-deployment checklist, but as ongoing monitoring that survives audit. This maps directly onto the bank's existing MRM (Model Risk Management) framework under SR 11-7, which RAI already operates within. We're not asking RAI to invent a new control regime; we're giving them the tooling to extend the one they already have.

The cost of *not* having this is asymmetric. One public AI incident at a major bank — a hallucinated policy interpretation, a customer's PII appearing in another customer's session, an agent moving money it shouldn't — is a board-level event. The industry has had enough close calls in the past 18 months that RAI's caution is well-founded. HoneyHive's annual cost is rounding error against that downside.

### The secondary story: unlock value already committed

Every month an agent doesn't ship is a month its business case isn't realized. The bank has already paid the cost of building these 5 agents — engineering time, infrastructure, integration work. That spend produces zero return until the agents are live.

HoneyHive shortens the time from "built" to "deployed" by giving platform a credible answer to RAI's questions on day one rather than on month six. Even a modest acceleration — getting the first agent live one quarter sooner — covers the platform's annual contract many times over for any agent with material business value.

The same logic compounds across the 45 agents to come. Today, every new agent restarts the RAI conversation from scratch. With HoneyHive in place, the conversation becomes "here's the evidence, in the same format as the last one we approved." That's the difference between RAI being a per-agent bottleneck and a per-agent rubber stamp.

### The build-vs-buy framing

The platform team could build this in-house. Realistically, it would take 2–3 platform engineers 9–12 months to build the trace ingestion, evaluator library, dataset curation, and dashboards — and that's before ongoing maintenance, before keeping up with new frameworks, and before convincing RAI that an internally-built tool is rigorous enough to trust. HoneyHive's ACV (Annual Contract Value) is a fraction of that engineering cost, and the team is freed to work on the gateway, the guardrails service, and the developer experience layer that's actually proprietary to this bank.

### How ROI lands with each stakeholder

- **AI Platform team:** "You ship agents. Today RAI is your bottleneck. We remove it."
- **Responsible AI:** "You get continuous, trace-level evidence in a format that maps to your existing MRM controls. You're not approving a black box; you're approving a system you can audit at any time."
- **CTO:** "Your AI program scales from 5 agents to 50 without a linear increase in RAI review cost. The unit economics of each new agent improve, not degrade."
- **CFO (when this conversation comes):** "The business cases that justified the AI investment realize on a faster timeline. Each month of acceleration on agents already built is captured value."

### What we won't claim

We're not going to put a single percentage on this — "X% faster time to production" or "Y% reduction in incidents" — because we don't have the bank's baseline data and any number we make up will get torn apart in front of the platform team. The framework is here; the inputs are theirs. That's a stronger position than a fabricated headline number.

## Q2 — How would you explain HoneyHive's workflow?

HoneyHive sits across three connected surfaces — observability, evaluation, and prompt management — that together form a closed loop between production and development.

For the platform team, the simplest framing is: **HoneyHive is the agent-native layer that lives alongside your existing Datadog and Splunk footprint, but operates on the things those tools can't see — what the agent reasoned, which tools it called, and whether the output was correct.**

### The loop

1. **Instrument once, in the framework you already use.** Drop the OpenTelemetry-native SDK into each agent. Auto-instrumentation covers the major frameworks (LangChain/LangGraph, CrewAI, OpenAI Agents) and major model providers (Anthropic, OpenAI, Bedrock). You get full traces — every model call, every tool call, every retrieval — as OTLP (OpenTelemetry Protocol) spans, with no vendor lock-in. Same SDK pattern across all 5 agents regardless of framework.

2. **Observe in production.** Traces stream into HoneyHive as agent graphs (DAGs, Directed Acyclic Graphs) showing the full execution path. The platform team sees cost, latency, and quality side-by-side. On-call gets alerts on failure modes — guardrail violations, latency spikes, accuracy drops — and can drill from alert to trace to root cause without leaving the tool.

3. **Run online evaluators on live traffic.** This is the piece that matters most for RAI. HoneyHive runs evaluators continuously against production traces — faithfulness checks for hallucinations, PII detection on inputs and outputs, tool-misuse and looping detection, custom assertions for business rules. Evaluators can be code, LLM-as-judge, or third-party. Results land in dashboards RAI can review.

4. **Curate datasets from production failures.** When something fails in production — a hallucination caught by an online eval, a user thumbs-down, a guardrail violation — that trace can be promoted into a dataset with one click. The dataset becomes a regression suite for the next iteration of the agent.

5. **Run offline experiments before you ship.** Before deploying a prompt change, model swap, or new tool, the agent team runs the curated dataset through HoneyHive's experiment framework (programmatically or via GitHub Actions in CI). Side-by-side comparison against the prior version. Regressions get flagged before they hit production.

6. **Manage prompts in Studio.** Versioned prompts in a shared workspace, with history. Domain experts (compliance, fraud SMEs, product) can review and edit alongside engineers. Deployments are decoupled from code releases via the optional proxy endpoint, so rollback is one click rather than a redeploy.

The output of step 5 feeds the next deployment, which gets observed in step 2, and the loop continues. Each iteration tightens.

### What adoption looks like

For a platform team that already has agents in production: instrument one agent first (3 lines of code), get traces flowing within an hour, set up the first online evaluator the same day. The first week is about proving the trace data is useful for the on-call engineers. The second week is about wiring evaluators that map to RAI's three concerns. By week four, the loop is closed — production failures are feeding a curated dataset, and the next agent release is gated by an offline experiment in CI.

The pattern extends identically to the other 4 agents and to the 45 to come, because the SDK and the data model are framework-agnostic.

## Q3 — Which features do you think are critical to highlight early?

Three features, ranked by how directly they move RAI sign-off and the path to 50 agents. The narrative arc: get traces from every agent regardless of framework → measure what RAI cares about continuously → make sure failures don't repeat.

### 1. Online evaluators on production traces

Continuous evaluators running against live traffic — faithfulness checks for hallucinations, PII detection on inputs/outputs/intermediate steps, tool-misuse and looping detection, custom assertions for business rules. Code, LLM-as-judge, or third-party. This is the single feature that produces the evidence RAI needs to unblock deployment, and it produces it continuously rather than at a point in time.

### 2. OpenTelemetry-native SDK with framework-agnostic instrumentation

Same SDK pattern across LangGraph, Bedrock Agents, CrewAI, and whatever the next 45 agents land on. OTLP spans, no vendor lock-in, auto-instrumentation for the major frameworks and model providers. Without this, feature #1 doesn't scale past the first agent. This is the platform team's biggest win — one integration pattern they can standardize on and offer to every use-case team.

### 3. Dataset curation from production failures + offline experiments in CI

Production failures get promoted to a curated dataset with ease. The dataset becomes a regression suite that gates the next deploy via GitHub Actions. This is what makes the RAI evidence durable — not "we measured it once," but "we have a system that prevents recurrence" and can show it at every release.

### What we're intentionally not leading with

Prompt management (Studio), agent graph visualizations, and alerting are all genuinely useful, but they don't move RAI and they're not the platform team's current bottleneck. Self-hosted deployment matters enormously for the procurement conversation but is a security/legal story, not a technical-deep-dive story. We mention these in passing and lead with the three above.

## Q4 — Discovery questions

Before the deep-dive, here are the questions I'd want answered. They're organized in two layers: questions for the platform team about their own world, and questions for the platform team as proxy for the Responsible AI team — since RAI is the deployment blocker even though they're not in the room.

I considered a third layer on procurement (thresholds, prior AI vendor experience, what slowed past deals down), but intentionally left it out of this session. Those questions aren't technical, would derail a deep-dive with engineers, and are better surfaced in a one-on-one follow-up with the AI Platform Director.

### Layer 1 — Platform team's world

#### The 5 agents in production

- For each of the 5 agents: what does it do, who built it, what framework, what tools does it call, and what's the blast radius if it fails?
- Which of the 5 has the highest stakes if it goes wrong? Which has the lowest? (This is where we'd want to start instrumenting.)
- Are any of the 5 already in front of customers, or are they all internal-facing today?
- What does "in production" actually mean for each — handling live traffic, shadow mode, internal beta?
- What incidents have you had so far? Walk me through the most recent one — how did you find out, how did you debug it, how long did it take to resolve?

#### Stack and gateway

- The LLM gateway — what does it log today, where do those logs go, and who looks at them?
- Are you generating any trace-level data across agent steps today, or is it request-level only at the gateway?
- What's the relationship between the gateway, your guardrails service, and the agent frameworks? Does every model call go through the gateway, including tool-driven sub-calls inside an agent?
- Where does Datadog end and where does the gap start? Specifically — what can your on-call see when an agent misbehaves at 2am?

#### Scaling to 50 agents

- What's the bottleneck today — RAI sign-off, platform capacity, agent team velocity, or something else?
- When a new use case onboards, what does the first 30 days look like? What does month two through six look like?
- Are the next 45 agents going to land on the same three frameworks (LangGraph, Bedrock Agents, CrewAI), or do you expect more fragmentation?
- Is there any appetite to consolidate frameworks, or is "framework choice belongs to the use-case team" a fixed principle?

#### What's been tried

- Has the platform team built any internal eval or observability tooling already? What worked, what didn't?
- Have you evaluated other vendors in this space? What were the disqualifiers?
- Is there an internal "platform agent" or reference implementation that the use-case teams are supposed to copy from?

### Layer 2 — Platform team as RAI proxy

The goal here is to surface where the platform team has crisp answers from RAI and where they're operating on inference. Both are useful — the gaps are where we can help platform get sharper before the next RAI conversation.

I'm walking in with a hypothesis: based on what sales heard on the first call and what we typically see at banks, RAI's three blockers are hallucinations, PII leakage, and unauthorized tool use. The first question tests that hypothesis. The rest probe deeper, conditional on the hypothesis being right.

#### Hypothesis check

- Our read going in is that RAI's three blockers are hallucinations, PII leakage, and unauthorized tool use. Two questions: (a) did we get that right, and (b) which of the three is RAI most exercised about right now? We've seen banks where the order is very different, and it changes where we'd start.
- What's *not* on the list yet that you expect RAI to add over the next 6–12 months? (Prompt injection, jailbreaks, model drift, bias — any of those on the radar?)

#### Evidence requirements

- What specific evidence has RAI told you they need to see before they'll unblock a deployment? Have they given you an artifact, a checklist, a memo — or is it verbal so far?
- Assuming hallucinations stay on the list: have they told you a target rate, a measurement methodology, or just "show us you're measuring it"?
- For PII: are they asking for prevention evidence, detection evidence, or both? At what layer — input, output, intermediate tool calls, logs?
- For tool misuse: are they asking for trace-level review of every production tool call, or sampling, or a baseline-and-drift approach?
- Is RAI thinking about this as a one-time pre-deployment gate, or as continuous evidence post-deployment?

#### Review cadence

- Is RAI's involvement a one-time gate before deployment, or ongoing? If ongoing, at what cadence — per release, monthly, quarterly?
- How does RAI want to consume this evidence — a dashboard they log into, a report you generate, a Jira ticket, something else?
- Who at RAI is the actual reviewer? Is it the same person each time, or does it route through a committee?

#### Failure response

- If an agent in production produces a hallucination or leaks PII tomorrow, what's the expected response — from platform, from RAI, from the business?
- Is there a defined incident process for AI-specific failures, or does it route through the existing security/ops incident process?
- Has RAI defined what "good enough" looks like, or is the bar implicitly "no incidents"?

---

The pattern across both layers: I want to find out *what platform actually knows vs. infers*, because the gaps are where HoneyHive becomes the shared tool that lets platform have a sharper conversation with RAI — not just better instrumentation for the engineers.

# Part II

## The demo
The demo was recorded using Loom and can be found [here](https://www.loom.com/share/825bcbe779c24362a2a043a02a4cb1cc). The link is public.

## The code
Two agents have been built using LangGraph: Fraud Agent and KYC Onboarding.  
To run the agents, you can follow the instructions in the README of each folder. The demo also walks through the execution of the agents and a review of the logged traces.

## The shareable artifact for one other stakeholder
I chose to create a memo for RAI. You can find the PDF in the root folder named `RAI_Memo_HoneyHive.pdf`.

# Part III
The POC plan can be found in the PDF `HoneyHive_POC_Plan.pdf` in the root of the repository.

# AI use
Claude was used for brainstorming and the generation of artifacts under my supervision and guidance.  
Claude Code and Cursor were used for code generation, instrumentation, and debugging.