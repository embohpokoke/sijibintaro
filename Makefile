ROOT_DIR := /var/www/sijibintaro
SERVICE := sijibintaro-api

.PHONY: up down restart status logs smoke deploy migrate-accounting

up:
	@systemctl start $(SERVICE)
	@echo "PASS $(SERVICE) started"

down:
	@systemctl stop $(SERVICE)
	@echo "PASS $(SERVICE) stopped"

restart:
	@systemctl restart $(SERVICE)
	@echo "PASS $(SERVICE) restarted"

status:
	@systemctl status $(SERVICE) --no-pager

logs:
	@journalctl -u $(SERVICE) -f

smoke:
	@/root/scripts/smoke-test.sh sijibintaro

deploy:
	@git -C $(ROOT_DIR) pull --ff-only
	@systemctl restart $(SERVICE)
	@sleep 3
	@$(MAKE) smoke

migrate-accounting:
	@bash -lc 'set -a; [ -f /root/.wallet/sijibintaro-api.env ] && . /root/.wallet/sijibintaro-api.env; [ -f /root/.wallet/livininbintaro.env ] && . /root/.wallet/livininbintaro.env; set +a; cd $(ROOT_DIR) && if [ -n "$$DATABASE_URL" ]; then psql "$$DATABASE_URL" -f migrations/002_accounting.sql; else psql -h "$${PGHOST:-localhost}" -U "$${PGUSER:-siji}" -d "$${PGDATABASE:-livininbintaro}" -f migrations/002_accounting.sql; fi'
