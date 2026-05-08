.PHONY: help install dev test clean run docker-build docker-run

help:
	@echo "Batocera Overmind - Available commands:"
	@echo ""
	@echo "  make install      - Install dependencies"
	@echo "  make dev          - Install development dependencies"
	@echo "  make run          - Run the development server"
	@echo "  make test         - Run tests"
	@echo "  make lint         - Run linters"
	@echo "  make format       - Format code with black"
	@echo "  make clean        - Clean cache files"
	@echo "  make docker-build - Build Docker image"
	@echo "  make docker-run   - Run with Docker Compose"

install:
	python3 -m pip install --user -r requirements.txt

dev:
	python3 -m pip install --user -r requirements.txt
	python3 -m pip install --user pytest pytest-asyncio httpx ruff black mypy

run:
	python3 -m uvicorn src.overmind.main:app --reload --host 0.0.0.0 --port 8000

test:
	python3 -m pytest tests/ -v

lint:
	python3 -m ruff check src/
	python3 -m mypy src/

format:
	python3 -m black src/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name ".DS_Store" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache

docker-build:
	docker build -t batocera-overmind .

docker-run:
	docker-compose up
