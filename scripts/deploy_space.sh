#!/bin/sh
# Assemble the Hugging Face Space bundle and push it to the Space's git repo.
#
#   HF_SPACE=<user>/<space-name> scripts/deploy_space.sh
#
# Needs: a Space created with the Docker SDK, and git credentials for huggingface.co (an HF
# access token with write permission; `git` will prompt, or use a credential helper).
# The bundle holds only runtime code: no .env, data, tests, evals or results.
set -eu
: "${HF_SPACE:?set HF_SPACE=<user>/<space-name>}"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

git clone --depth 1 "https://huggingface.co/spaces/$HF_SPACE" "$WORK/space"
cd "$WORK/space"
# Replace everything except git metadata with a fresh bundle.
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +

cp "$ROOT/docker/space.Dockerfile" Dockerfile
cp "$ROOT/docker/space_README.md" README.md
cp "$ROOT/pyproject.toml" "$ROOT/uv.lock" "$ROOT/.python-version" "$ROOT/.dockerignore" .
mkdir -p docs docker
cp -R "$ROOT/app" "$ROOT/api" "$ROOT/ui" "$ROOT/.streamlit" .
cp "$ROOT/docs/schema.yaml" docs/
cp "$ROOT/docker/space_start.sh" docker/
find . -name __pycache__ -type d -prune -exec rm -rf {} +

git add -A
if git diff --cached --quiet; then
  echo "Space already up to date."
else
  git commit -q -m "Deploy $(git -C "$ROOT" rev-parse --short HEAD)"
  git push
  echo "Pushed. Build logs: https://huggingface.co/spaces/$HF_SPACE?logs=build"
fi
