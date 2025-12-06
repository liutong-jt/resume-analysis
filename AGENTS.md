# Repository Guidelines

## Project Structure & Module Organization
- `main.py` hosts the FastAPI backend, PDF parsing helpers, and OpenAI call pipeline.
- `static/` contains the Vue + Tailwind single-page UI (`index.html`, `app.js`, `styles.css`).
- `requirements.txt` lists Python dependencies (FastAPI, pdfium, OpenAI SDK, etc.).
- `package.json` is currently placeholder; front-end assets are served statically and do not require a build step.
- `test_evaluation.py` bundles the regression tests guarding `process_evaluation_result`.

## Build, Test, and Development Commands
- `python3 -m venv .venv && source .venv/bin/activate` — recommended virtualenv before installing deps.
- `pip install -r requirements.txt` — install backend/runtime tooling.
- `uvicorn main:app --reload` — launch the local server with hot reload on `http://127.0.0.1:8000`.
- `python test_evaluation.py` — run the deterministic regression tests for score/name handling.

## Coding Style & Naming Conventions
- Follow PEP 8 with 4-space indentation; keep functions short and pure when possible.
- Favor explicit names such as `candidate_images` over single letters; shared helpers belong near the top of `main.py`.
- Front-end scripts should remain Vanilla JS modules; keep Vue composition API hooks grouped (`refs`, lifecycle, handlers`).
- Add docstrings to new Python helpers and inline comments only when logic is non-trivial.

## Testing Guidelines
- Extend `test_evaluation.py` with new scenarios when touching scoring, parsing, or filename fallbacks.
- Name new tests `test_<behavior>` and keep fixtures inline for readability.
- For backend changes outside `process_evaluation_result`, prefer lightweight FastAPI route tests via `TestClient` in new files under a `tests/` folder.
- Document manual QA steps (e.g., multi-page resume upload) in your PR description.

## Commit & Pull Request Guidelines
- Write imperative, scoped commit messages (e.g., `Add scrolling to resume preview pane`).
- Squash exploratory commits before opening a PR; keep history tidy and reviewable.
- PRs should describe high-level changes, include before/after screenshots for UI tweaks, and reference issue numbers.
- Highlight any config changes (.env keys, API quotas) so reviewers can replicate your setup.

## Security & Configuration Tips
- Secrets live in `.env` (e.g., `OPENAI_API_KEY`, `OPENAI_BASE_URL`); never commit them.
- Validate PDF size/type before processing; never trust client-provided filenames when storing files.
- Limit outbound requests to the configured OpenAI endpoint and document any new external services in the PR.
