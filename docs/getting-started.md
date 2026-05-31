# Getting Started — PII Ghost-Hunter

> Local development setup. Time to first `docker compose up`: ~10 minutes.

---

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Docker Desktop | ≥ 4.28 | [docs.docker.com](https://docs.docker.com/get-docker/) |
| Docker Compose | ≥ 2.24 | bundled with Docker Desktop |
| Python | 3.11+ | [python.org](https://www.python.org/downloads/) |
| Node.js | 20 LTS | [nodejs.org](https://nodejs.org/) |
| Make | any | pre-installed on macOS/Linux; [GnuWin32](http://gnuwin32.sourceforge.net/packages/make.htm) on Windows |

---

## 1. Clone & configure environment

```bash
git clone https://github.com/your-org/pii-ghost-hunter.git
cd pii-ghost-hunter

# Copy the example env file — edit values as needed
cp .env.example .env
```

> **Note:** For local development the defaults in `.env.example` work as-is.
> You only need real AWS credentials if you want to test S3 integration.

---

## 2. Install local dependencies

```bash
make install
```

This runs:
- `pip install -r requirements-dev.txt` (Python packages + dev tools)
- `npm install` inside `dashboard/` (React + Vite + tooling)

---

## 3. Install pre-commit hooks

```bash
make pre-commit-install
```

Hooks run on every commit: ruff, black, mypy, trailing-whitespace, detect-private-key.

---

## 4. Start all services

```bash
make dev
```

This runs `docker compose up --build -d` and prints service URLs when ready.

| Service | URL | Credentials |
|---|---|---|
| Dashboard (React) | http://localhost:5173 | — |
| API (FastAPI) | http://localhost:8000 | — |
| API docs (Swagger) | http://localhost:8000/docs | — |
| Airflow | http://localhost:8080 | admin / admin |
| Schema Registry | http://localhost:8081 | — |
| PostgreSQL | localhost:5432 | airflow / airflow |
| Redis | localhost:6379 | — |
| Kafka | localhost:9092 | — |

> First start takes longer — Docker pulls images (~3 GB total).
> Airflow init runs migrations before the webserver starts; allow ~60 seconds.

---

## 5. Verify health

```bash
# API health
curl http://localhost:8000/health

# Kafka topics (after Kafka is healthy)
docker exec gh-kafka kafka-topics --bootstrap-server localhost:9092 --list

# Airflow (should show scheduler as healthy)
curl -u admin:admin http://localhost:8080/api/v1/health
```

---

## 6. Run tests

```bash
make test        # all tests (Python + TypeScript)
make test-py     # Python only
make test-ts     # Frontend only
```

---

## 7. Lint & format

```bash
make lint        # check only (CI mode)
make format      # auto-fix
```

---

## 8. Stop everything

```bash
make stop        # docker compose down (preserves volumes)
docker compose down -v   # also removes volumes (wipes DB)
```

---

## Project layout recap

```
.
├── api/                FastAPI backend (port 8000)
├── dashboard/          React + Vite frontend (port 5173)
├── scanner/            Kafka consumer/producer stubs
├── orchestration/      Airflow DAGs and plugins
├── ml/                 PII classification model
├── etl/                Anonymization and quarantine jobs
├── infra/              Terraform + Helm charts
├── tests/              Pytest suite
├── docs/               This and other documentation
├── docker-compose.yml
├── Makefile
└── .env.example
```

---

## Troubleshooting

**Airflow webserver fails to start**
: Wait for `airflow-init` to complete first (`docker compose logs airflow-init`). If it keeps failing, run `docker compose down -v` and restart — the DB state may be corrupted.

**Kafka healthcheck times out**
: Kafka takes 30–60 s to elect a controller. Run `docker compose ps` and wait for the `(healthy)` state before testing consumers.

**Port already in use**
: Another process is using one of 5173, 8000, 8080, 8081, 9092, 5432, or 6379. Stop the conflicting process or change the port mapping in `docker-compose.yml`.

**`make dev` on Windows without Make**
: Run the `docker compose up --build -d` command directly, or install Make via Chocolatey: `choco install make`.
