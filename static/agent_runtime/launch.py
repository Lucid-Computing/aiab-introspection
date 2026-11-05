"""Apply OS limits before Node startup; evidence remains with supervisor until ready."""

import os
import resource
import sys

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3,) * 2)
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
os.umask(0o077)
os.execv(sys.argv[1], [sys.argv[1], "--jitless", "--max-old-space-size=128", sys.argv[2]])
