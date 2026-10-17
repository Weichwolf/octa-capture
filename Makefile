# SPDX-License-Identifier: GPL-2.0-only
PRESET ?= strict

.PHONY: all configure test lint format release
all: configure
	cmake --build --preset $(PRESET)

configure:
	cmake --preset $(PRESET)

test: all
	ctest --preset $(PRESET)

lint: configure
	cmake --build build/$(PRESET) --target lint

format:
	cmake --preset $(PRESET)
	cmake --build build/$(PRESET) --target format

release:
	$(MAKE) PRESET=release test
