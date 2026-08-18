install:
	pip3 install -r requirements.txt

run:
	python3 -m gio_trading_bot

clean:
	rm -rf __pycache__ gio_trading_bot/__pycache__ .venv gio.db
