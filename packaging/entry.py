"""Entry point for the frozen binary.

Imports from the installed distribution. `src/main.py`'s sys.path trick is a development
convenience a shipped binary should not depend on, and PyInstaller should analyse the
package as installed rather than as a directory that happens to sit beside a script.
"""

import sys

from cli.app import main

if __name__ == "__main__":
    sys.exit(main())
