# ==============================================================================
# Tactical Edge Gateway - Developer & Automation Makefile
# ==============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV ?= .venv
PYTHON ?= $(VENV)/bin/python3
PIP ?= $(VENV)/bin/pip

.PHONY: help venv install dev-install lint format test clean keys docker-build zarf-package

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip setuptools wheel

venv: $(VENV)/bin/activate ## Create Python virtual environment

install: venv ## Install project dependencies
	$(PIP) install -e .

dev-install: venv ## Install project dependencies in editable mode with development tools
	$(PIP) install -e ".[dev]"

lint: venv ## Run flake8 and code formatting checks
	$(VENV)/bin/flake8 src tests
	$(VENV)/bin/black --check src tests
	$(VENV)/bin/isort --check-only src tests

format: venv ## Automatically format code using black and isort
	$(VENV)/bin/black src tests
	$(VENV)/bin/isort src tests

test: venv ## Run unit and integration tests with coverage
	$(VENV)/bin/pytest tests/

keys: ## Generate deterministic cryptographic host and service keys for edge testing
	@python3 scripts/generate-deterministic-keys.py --seed "tactical-edge-gateway-lab-seed"

docker-build: ## Build lightweight OCI container image
	docker build -t tactical-edge-gateway:latest .

zarf-package: ## Build declarative air-gapped Zarf package
	@if command -v zarf >/dev/null 2>&1; then \
		zarf package create packages/zarf --confirm --output packages/zarf; \
	else \
		echo "Zarf CLI not found in PATH. Please install zarf to package for air-gap."; \
	fi

clean: ## Clean build, test, and temporary cache artifacts
	rm -rf build/ dist/ *.egg-info .pytest_cache/ .coverage htmlcov/
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
