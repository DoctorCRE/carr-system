"""Validate migration tooling output paths."""
class OwnershipError(ValueError):
    pass

def validate_outputs(repo, paths):
    """Refuse nonregular sinks and symlink ancestors before any write."""
    import stat
    root = repo.resolve(strict=True)
    for name in paths:
        target = repo / name
        if not target.is_relative_to(repo) or '..' in target.relative_to(repo).parts:
            raise OwnershipError(f"{name}: output escapes staging")
        for part in [target, *target.parents]:
            if part == repo:
                break
            if part.is_symlink():
                raise OwnershipError(f"{name}: symlink output or ancestor")
        if not target.resolve().is_relative_to(root):
            raise OwnershipError(f"{name}: output escapes staging")
        if target.exists() and not stat.S_ISREG(target.stat().st_mode):
            raise OwnershipError(f"{name}: output is not a regular file")
