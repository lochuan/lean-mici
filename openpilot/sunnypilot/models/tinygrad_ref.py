import os

from openpilot.common.basedir import BASEDIR

# Flat-tree fallback: published release trees strip tinygrad_repo/.git, so the
# release process stamps the build tree's revision here (device_release.sh).
TINYGRAD_PIN_FILE = "TINYGRAD_PIN"


def _head_ref(git_dir):
  with open(os.path.join(git_dir, "HEAD")) as f:
    ref = f.read().strip()
  if ref.startswith("ref:"):
    with open(os.path.join(git_dir, ref.split(" ", 1)[1])) as f:
      return f.read().strip()
  return ref


def _git_ref():
  repo_path = os.path.join(BASEDIR, "tinygrad_repo")
  git_path = os.path.join(repo_path, ".git")
  if os.path.isdir(git_path):
    return _head_ref(git_path)
  with open(git_path) as f:
    line = f.read().strip()
  return _head_ref(os.path.join(repo_path, line[8:]))


def _pin_file_ref():
  with open(os.path.join(BASEDIR, "tinygrad_repo", TINYGRAD_PIN_FILE)) as f:
    return f.read().strip() or None


def get_tinygrad_ref():
  try:
    ref = _git_ref()
    if ref:
      return ref
  except Exception as e:
    print(f"Error getting tinygrad_repo ref: {e}")
  try:
    return _pin_file_ref()
  except Exception as e:
    print(f"Error getting tinygrad_repo ref: {e}")
    return None


def main():
  current_ref = get_tinygrad_ref()
  if current_ref:
    print(current_ref)
  else:
    print("")


if __name__ == "__main__":
  main()
