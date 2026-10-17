# ==============================================================================
# Tactical Edge Gateway - Developer & Automation Makefile
# ==============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV ?= .venv
PYTHON ?= $(VENV)/bin/python3
PIP ?= $(VENV)/bin/pip

.PHONY: help venv install dev-install lint format test security pre-commit pre-commit-install clean keys docker-build zarf-package lula-lint lula-validate demo

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
	@if command -v pre-commit >/dev/null 2>&1; then \
		pre-commit install; \
	fi

lint: venv ## Run flake8 and code formatting checks
	$(VENV)/bin/flake8 src tests
	$(VENV)/bin/black --check src tests
	$(VENV)/bin/isort --check-only src tests

format: venv ## Automatically format code using black and isort
	$(VENV)/bin/black src tests
	$(VENV)/bin/isort src tests

test: venv ## Run unit and integration tests with coverage
	$(VENV)/bin/pytest tests/

pre-commit: ## Run all pre-commit hooks against the codebase
	pre-commit run --all-files

pre-commit-install: ## Install pre-commit git hooks
	pre-commit install

security: ## Run local SAST, secret, and vulnerability security checks
	@echo "==> Running Bandit SAST scan..."
	$(VENV)/bin/bandit -c pyproject.toml -r src
	@echo "==> Running Pip-Audit dependency vulnerability check..."
	$(VENV)/bin/pip-audit
	@echo "==> Running Trivy filesystem and secret scan..."
	@if command -v trivy >/dev/null 2>&1; then \
		trivy fs --scanners vuln,secret,misconfig .; \
	else \
		echo "Trivy CLI not found in PATH. Skipping Trivy scan."; \
	fi

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

lula-lint: ## Lint OSCAL Component Definition against upstream schema
	@if command -v lula >/dev/null 2>&1; then \
		lula tools lint -f compliance/lula/oscal-component.yaml; \
	else \
		echo "Lula CLI not found in PATH. Please install lula to lint OSCAL definitions."; \
	fi

lula-validate: ## Run automated Lula OSCAL compliance assessment
	@./scripts/validate-lula-compliance.sh

demo: venv ## Run interactive end-to-end mission simulation with simulated RF blackout
	$(PYTHON) scripts/run-e2e-simulation.py $(ARGS)

clean: ## Clean build, test, and temporary cache artifacts
	rm -rf build/ dist/ *.egg-info .pytest_cache/ .coverage htmlcov/
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
