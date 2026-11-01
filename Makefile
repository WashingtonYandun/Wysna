PREFIX ?= $(HOME)/.local
BINDIR ?= $(PREFIX)/bin

.PHONY: all test check selfhost install uninstall clean

all: selfhost test

test:
	./wysna test

check:
	./wysna check selfhost/compiler.wysna

selfhost:
	@mkdir -p .wysna_cache
	./wysna build --engine=compiler selfhost/compiler.wysna -o .wysna_cache/wysna_selfhost
	@echo "[OK] Native self-hosted compiler binary ready at .wysna_cache/wysna_selfhost"

install:
	./install.sh "$(BINDIR)"

uninstall:
	rm -f "$(BINDIR)/wysna"
	@echo "[OK] Removed $(BINDIR)/wysna"

clean:
	rm -rf .wysna_cache build dist *.egg-info
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	@echo "[OK] Cleaned build caches"
