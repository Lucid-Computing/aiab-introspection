# Build in the measured image. No compiler or npm invocation occurs at run time.
NODE_INCLUDE ?= /usr/include/node
isolate.node: isolate.c
	$(CC) -O2 -Wall -Wextra -Wno-unused-parameter -shared -fPIC -I$(NODE_INCLUDE) $< -o $@
