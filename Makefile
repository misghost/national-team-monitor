.PHONY: help install test check serve refresh backtest deploy push clean

PYTHONPATH := src
export PYTHONPATH

help:
	@echo "install   安装依赖"
	@echo "test      运行全部测试"
	@echo "check     自检（实体/PIT/信号不变式）"
	@echo "serve     启动本地服务 http://localhost:8000"
	@echo "refresh   刷新数据并重算信号"
	@echo "backtest  运行回测"
	@echo "deploy    一键部署到云服务器（需 SERVER_HOST/SERVER_USER）"
	@echo "push      推送到 GitHub（需 GITHUB_TOKEN）"
	@echo "clean     清理运行期数据"

install:
	pip install -r requirements.txt

test:
	python3 -m pytest tests/ -q

check:
	python3 -m ntm check

serve:
	python3 -m ntm serve

refresh:
	python3 -m ntm refresh

backtest:
	python3 -m ntm backtest --horizon 20

deploy:
	bash deploy/deploy.sh

push:
	bash scripts/push_github.sh

clean:
	rm -f data/*.db data/*.db-shm data/*.db-wal
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache
