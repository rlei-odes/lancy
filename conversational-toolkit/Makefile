ifneq (,$(wildcard .env.local))
    include .env.local
    export $(shell sed 's/=.*//' .env.local)
endif

dev:
	uvicorn examples.simple_rag:app --reload --port 8080

# Make command for bumping major version
major:
	bump2version major --allow-dirty

# Make command for bumping minor version
minor:
	bump2version minor --allow-dirty

# Make command for bumping patch version
patch:
	bump2version patch --allow-dirty
