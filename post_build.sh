#!/bin/bash
# Diagnostic first: print environment to understand Oryx context
set -e

echo "=== post_build: environment ==="
env | sort
echo "=== PWD ==="
pwd
echo "=== ls . ==="
ls .
echo "=== post_build complete ==="
