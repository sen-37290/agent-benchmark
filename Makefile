.PHONY: lint format

lint:
	cd eval_engine && uv run ruff format --check .
	cd eval_engine && uv run ruff check .

format:
	cd eval_engine && uv run ruff format .
	cd eval_engine && uv run ruff check --fix .
