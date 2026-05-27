.PHONY: install run test clean

install:
	python -m venv .venv
	.venv\Scripts\python.exe -m pip install --upgrade pip
	.venv\Scripts\python.exe -m pip install -r requirements.txt
	@echo ""
	@echo "Done. Next steps in PowerShell:"
	@echo "1. .venv\Scripts\activate"
	@echo "2. cp .env.example .env"
	@echo "3. make run"

run:
	.venv\Scripts\python.exe -m uvicorn src.app:app --reload --host 0.0.0.0 --port 8000

test:
	.venv\Scripts\python.exe -m pytest -v tests/

clean:
	powershell -Command "Remove-Item -Recurse -Force _data, __pycache__, .pytest_cache, src\__pycache__, src\adapters\__pycache__, tests\__pycache__ -ErrorAction SilentlyContinue"