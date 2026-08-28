#!/usr/bin/env python3
"""Run Background Assistant from a source checkout.

The real entry point is ``bgassist.cli``; this file exists so ``python main.py``
keeps working for anyone used to it.
"""
import sys

from bgassist.cli import main

if __name__ == "__main__":
    sys.exit(main())
