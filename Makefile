.PHONY: install test check

install:
	./install.sh

test:
	python -m unittest discover -s tests -v

check:
	bash -n install.sh contactanalyzer scripts/launch-chrome.sh
	for id in fedora debian ubuntu linuxmint kali; do \
		CONTACT_ANALYZER_DISTRO_ID="$$id" ./install.sh --system-requirements >/dev/null; \
	done
	python -m compileall -q contactanalyzer_app tests
	python -m unittest discover -s tests -v
