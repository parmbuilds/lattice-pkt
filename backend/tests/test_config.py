import pytest

from app.config import DEV_PEPPER, Settings
from app.main import create_app


def test_the_public_default_pepper_is_refused_outside_local_development():
    with pytest.raises(RuntimeError, match="LATTICE_SECRET_PEPPER"):
        create_app(Settings(secret_pepper=DEV_PEPPER, insecure_dev_cookies=False))
    create_app(Settings(secret_pepper=DEV_PEPPER, insecure_dev_cookies=True))
