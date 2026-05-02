.PHONY: setup dev build up down restart logs migrate nginx-install ssl-cert smoke deploy

setup:
	@if [ ! -f .env ]; then cp .env.example .env && echo "⚠️  Edit .env dulu!"; fi
	@pip install -r requirements.txt
	@echo "✅ Setup done"

dev:
	@uvicorn main:app --host 127.0.0.1 --port 8002 --reload

build:
	@echo "No build step for FastAPI"

up:
	@systemctl start sijibintaro-api
	@echo "✅ SIJI API started"

down:
	@systemctl stop sijibintaro-api
	@echo "✅ SIJI API stopped"

restart:
	@systemctl restart sijibintaro-api
	@echo "✅ SIJI API restarted"

logs:
	@journalctl -u sijibintaro-api -f

migrate:
	@psql $$DATABASE_URL -f migrations/001_init.sql
	@echo "✅ Migration done"

nginx-install:
	@cp nginx/sijibintaro.conf /etc/nginx/conf.d/sijibintaro-id.conf
	@nginx -t && systemctl reload nginx
	@echo "✅ Nginx config installed"

ssl-cert:
	@certbot --nginx -d sijibintaro.id
	@echo "✅ SSL cert issued"

smoke:
	@/root/scripts/smoke-test.sh sijibintaro

deploy:
	@git pull
	@systemctl restart sijibintaro-api
	@sleep 3
	@$(MAKE) smoke
