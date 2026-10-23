#!/bin/zsh
# Refresh canonical main checkouts without discarding local work.
set -u

refresh_checkout() {
  local repo="$1" changes
  if [ -L "$repo" ] || [ ! -d "$repo/.git" ] || [ -L "$repo/.git" ] \
      || [ "$(/usr/bin/git -C "$repo" rev-parse --show-toplevel 2>/dev/null)" != "${repo:A}" ]; then
    print -ru2 -- "checkout-pull: $repo is not a canonical checkout"
    return 1
  fi
  if [ "$(/usr/bin/git -C "$repo" symbolic-ref --quiet --short HEAD)" != main ]; then
    print -ru2 -- "checkout-pull: $repo must select main"
    return 1
  fi
  changes="$(/usr/bin/git -C "$repo" status --porcelain --untracked-files=normal)" || return 1
  if [ -n "$changes" ]; then
    print -ru2 -- "checkout-pull: $repo has local changes"
    return 1
  fi
  /usr/bin/git -C "$repo" fetch --quiet origin refs/heads/main:refs/remotes/origin/main || return 1
  /usr/bin/git -C "$repo" merge-base --is-ancestor HEAD origin/main || {
    print -ru2 -- "checkout-pull: $repo cannot fast-forward to origin/main"
    return 1
  }
  /usr/bin/git -C "$repo" merge --ff-only --quiet origin/main
}

rc=0
for repo in "$HOME/carr-system" "$HOME/doctorcre-app"; do
  refresh_checkout "$repo" || rc=1
done
exit "$rc"
