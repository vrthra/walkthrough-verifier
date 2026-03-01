.PHONY: run run-hospital run-airport use-hospital use-airport train seed-hospital seed-airport backup clean

# ── Domain profile paths ────────────────────────────────────────────────────
HOSPITAL_CONFIG = configs/hospital.json
AIRPORT_CONFIG  = configs/airport.json

# ── Run ─────────────────────────────────────────────────────────────────────
run:
	PYTHONPATH=. python app.py

# Switch to a domain profile then start the app
run-hospital: use-hospital
	PYTHONPATH=. python app.py

run-airport: use-airport
	PYTHONPATH=. python app.py

# ── Switch domain (config only — restart required if app is already running) ─
use-hospital:
	cp $(HOSPITAL_CONFIG) config.json
	@echo "Switched to hospital domain. Run 'make run' (or 'make run-hospital') to start."

use-airport:
	cp $(AIRPORT_CONFIG) config.json
	@echo "Switched to airport domain. Run 'make run' (or 'make run-airport') to start."

# ── Seeding ──────────────────────────────────────────────────────────────────
# Populate a fresh database for each domain (idempotent by default).
# Pass RESET=1 to wipe and re-seed:  make seed-airport RESET=1
_RESET_FLAG = $(if $(RESET),--reset,)

seed-hospital:
	PYTHONPATH=. python seed_hospital.py \
	  --db-dir "$$(python -c "import json; print(json.load(open('$(HOSPITAL_CONFIG)'))['db_dir'])")" \
	  $(_RESET_FLAG)

seed-airport:
	PYTHONPATH=. python seed_airport.py \
	  --db-dir "$$(python -c "import json; print(json.load(open('$(AIRPORT_CONFIG)'))['db_dir'])")" \
	  $(_RESET_FLAG)

# ── Backup / Clean ───────────────────────────────────────────────────────────
# Backup dir: backups/<domain>/<timestamp>/
# Backs up the active domain's db_dir (triage.db + scorer pkl) and config.json.
# To back up a specific domain regardless of active config:
#   make backup DOMAIN=hospital   or   make backup DOMAIN=airport

_ACTIVE_DB_DIR  := $(shell python -c "import json; print(json.load(open('config.json')).get('db_dir','data'))" 2>/dev/null)
_ACTIVE_DOMAIN  := $(shell python -c "import json; print(json.load(open('config.json')).get('app_name','unknown').lower().split()[0])" 2>/dev/null)
_DOMAIN          = $(if $(DOMAIN),$(DOMAIN),$(_ACTIVE_DOMAIN))
_DOMAIN_DB_DIR   = $(if $(DOMAIN),$(shell python -c "import json; print(json.load(open('configs/$(DOMAIN).json'))['db_dir'])" 2>/dev/null),$(_ACTIVE_DB_DIR))
_TIMESTAMP      := $(shell date +%Y%m%d_%H%M%S)
_BACKUP_DIR      = backups/$(_DOMAIN)/$(_TIMESTAMP)

backup:
	@echo "Backing up domain '$(_DOMAIN)' → $(_BACKUP_DIR)/"
	@mkdir -p $(_BACKUP_DIR)
	@[ -d "$(_DOMAIN_DB_DIR)" ] || (echo "ERROR: db_dir '$(_DOMAIN_DB_DIR)' not found" && exit 1)
	@cp -r $(_DOMAIN_DB_DIR)/. $(_BACKUP_DIR)/
	@cp configs/$(_DOMAIN).json $(_BACKUP_DIR)/config_profile.json
	@echo "Backed up: $$(ls $(_BACKUP_DIR) | tr '\n' '  ')"
	@echo "Done → $(_BACKUP_DIR)"

# Remove all __pycache__, *.pyc, and the scorer pickle for the active domain.
# FORCE=1 also deletes the active domain's triage.db — back up first!
clean:
	@echo "Cleaning build artefacts…"
	find . -name '__pycache__' -not -path './py/*' -exec rm -rf {} + 2>/dev/null; true
	find . -name '*.pyc'       -not -path './py/*' -delete 2>/dev/null; true
	@[ -f "$(_ACTIVE_DB_DIR)/triage_scorer.pkl" ] && \
	  rm "$(_ACTIVE_DB_DIR)/triage_scorer.pkl" && \
	  echo "Removed scorer: $(_ACTIVE_DB_DIR)/triage_scorer.pkl" || true
	@if [ "$(FORCE)" = "1" ]; then \
	  if [ -f "$(_ACTIVE_DB_DIR)/triage.db" ]; then \
	    echo "FORCE=1: removing database $(_ACTIVE_DB_DIR)/triage.db"; \
	    rm "$(_ACTIVE_DB_DIR)/triage.db"; \
	  else \
	    echo "FORCE=1: no database found at $(_ACTIVE_DB_DIR)/triage.db"; \
	  fi; \
	else \
	  echo "Database untouched (pass FORCE=1 to also remove triage.db)."; \
	fi
	@echo "Clean done."

# ── Module 2 ─────────────────────────────────────────────────────────────────
train:
	PYTHONPATH=. python module2_scorer.py train triage_walkthroughs.json
