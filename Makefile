.PHONY: help install run test lint fmt docker-up docker-down

help:
	@echo "install     — установить зависимости"
	@echo "run         — запустить бота локально"
	@echo "test        — прогнать тесты"
	@echo "lint        — проверить код линтером"
	@echo "fmt         — отформатировать код"
	@echo "docker-up   — запустить в Docker"
	@echo "docker-down — остановить Docker"

install:
	python3 -m pip install -r requirements-dev.txt

run:
	python3 -m bot

test:
	python3 -m pytest tests -q

lint:
	python3 -m ruff check bot tests

fmt:
	python3 -m ruff format bot tests
	python3 -m ruff check --fix bot tests

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down
