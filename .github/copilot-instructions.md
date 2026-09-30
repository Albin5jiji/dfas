# Copilot instructions for CloudPilot

## Commands

- Set up local dependencies: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
- Run the app locally: `uvicorn app.main:app --reload`
- Run the full test suite: `python -m pytest -q`
- Run one test: `python -m pytest tests/test_workflow.py::test_v3_compares_prediction_to_post_deployment_outcome_and_updates_confidence -q`
- No dedicated lint or build command is defined in the repo.

## Architecture

- CloudPilot is a FastAPI app in `app/main.py` that wires request/response endpoints, startup database initialization, static dashboard assets, and optional CORS.
- The core review flow is: Terraform `show -json` input -> `app/services.py` -> pure engines in `app/engines.py` -> business context resolution in `app/business_context.py` -> persisted analysis and audit records.
- Live AWS topology is read-only and stored in the database; `app/live_topology.py` discovers and persists nodes/edges, while `app/graph.py` runs BFS/DFS blast-radius traversal over the active graph.
- V3 verification compares a previously stored analysis with observed deployment outcomes in `app/verification.py`, including cost error, dependency precision/recall/F1, health status, and historical confidence.
- The local default database is SQLite, but the schema and models are SQLAlchemy-based and can run against PostgreSQL via `DATABASE_URL`.

## Conventions

- Keep the decision engine deterministic and explainable; supported plan normalization only covers managed `aws_instance`, `aws_db_instance`, and `aws_ebs_volume` changes.
- Treat the AWS topology path as read-only only; CloudPilot should never create, update, invoke, stop, or delete AWS resources.
- Prefer persisted live topology over the JSON fixture for API analysis; the fixture in `topology/default.json` is for tests and deterministic examples.
- Preserve Terraform redaction behavior when touching analysis persistence or reports; sensitive values must stay masked in stored plans and responses.
- Maintain the graph direction convention: an edge `A -> B` means a change to `A` may affect `B`.
- Demo business context is seeded automatically in non-production unless `SEED_DEMO_DATA=false`; keep that behavior aligned with `app/config.py` and `app/business_context.py`.
- Analysis and verification endpoints return capped history (typically 50 rows); keep pagination/limits consistent when adding similar list endpoints.
