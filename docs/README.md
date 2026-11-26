# Documentation

Start with the document that matches your role.

## Overview

| Document | Read it to learn | Audience |
|---|---|---|
| [Architecture](architecture.md) | Components, data flow, remediation flow, data model, API, deployment | Engineers, security reviewers |
| [Results](results.md) | What has been measured and verified, what is only a target, and what is still unmeasured | Everyone |
| [Business decisions](business-decisions.md) | The problem, who the platform is for, product decisions and their rationale, economics, KPIs, risks | DPO, leadership, product |
| [Engineering decisions](engineering-decisions.md) | Technical decisions from the 2026 audit (context → decision → consequences) | Engineers |

## Operating the platform

| Document | Purpose |
|---|---|
| [Getting started](getting-started.md) | Local setup, step by step |
| [Security review](security-review.md) | OWASP Top 10 assessment and production checklist |
| [Data retention policy](data-retention-policy.md) | Quarantine and deletion rules |
| [Runbooks](runbooks/) | Patrol DAG failure, inference down, blue/green rollout, quarantine bucket full, Kafka consumer lag |

## For DPOs

| Document | Purpose |
|---|---|
| [Quick-start](dpo-quickstart.md) | First session in the dashboard |
| [User guide](dpo-user-guide.md) | Every screen and workflow |
| [Onboarding checklist](dpo-onboarding-checklist.md) | What to set up and verify |

## Planning (original design records)

The `planning/` folder holds the long-form documents written before implementation. Read them for background. The documents above describe the current state.

- [planning/BUSINESS.md](../planning/BUSINESS.md): full business case, market, regulation, personas
- [planning/DECISIONS.md](../planning/DECISIONS.md): original engineering decisions
- [planning/ROADMAP.md](../planning/ROADMAP.md): sprint plan and backlog
- [planning/BRAND.md](../planning/BRAND.md): brand and design system
