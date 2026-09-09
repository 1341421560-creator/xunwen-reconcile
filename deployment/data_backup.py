from .company_transfer import backup_companies


def backup_data(source_root, destination):
    return backup_companies(source_root, destination)
