from reconcile.atomic_write import replace_snapshot
from reconcile.company_profiles import safe_company_path
from .company_transfer import restore_companies


def safe_target(root, name):
    return safe_company_path(root, root / name)


def restore_data(root, archive):
    return restore_companies(root, archive, publisher=replace_snapshot)
