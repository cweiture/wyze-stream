#!/usr/bin/env python3
"""Compatibility entry point; use wyze_stream.py for new deployments."""
import sys
from wyze_stream import main

if __name__ == '__main__':
    sys.argv = [sys.argv[0], 'bridge', *[a for a in sys.argv[1:] if a != '--resume']]
    main()
