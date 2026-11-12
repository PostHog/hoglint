import gc
import sys

from hoglint.cli import main

code = main()
# The collection the interpreter runs at exit walks every live object and frees nothing an exiting process needs back.
gc.freeze()
sys.exit(code)
