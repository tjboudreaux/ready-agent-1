.PHONY: check lint test vendor-check release-check

check: lint test vendor-check release-check

lint:
	python3 -m ruff check --no-cache engine scripts evals tests

test:
	python3 -m unittest discover -s tests -t .

vendor-check:
	python3 scripts/vendor.py --check

release-check:
	python3 scripts/check_release_versions.py
