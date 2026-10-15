"""Secure credential storage using the `keyring` library.

Stores the OpenRouter API key in the desktop's native keyring.
The `keyring` library abstracts the backend: it uses
- GNOME Keyring (Secret Service API) on GNOME / Cinnamon
- KWallet on KDE Plasma
"""

from typing import Optional

import keyring

SERVICE_NAME = "ortracker"
KEYRING_ACCOUNT = "default"


def store_api_key(api_key: str) -> None:
    """Store the OpenRouter Management API key in the system keyring.

    If a previous key exists, it will be replaced.
    """
    api_key = api_key.strip()
    keyring.set_password(SERVICE_NAME, KEYRING_ACCOUNT, api_key)


def retrieve_api_key() -> Optional[str]:
    """Retrieve the stored OpenRouter Management API key.

    Returns ``None`` if no key is stored or if retrieval fails.
    """
    try:
        password = keyring.get_password(SERVICE_NAME, KEYRING_ACCOUNT)
        if password:
            return password.strip()
        return None
    except Exception:
        return None


def clear_api_key() -> None:
    """Remove the stored API key from the keyring."""
    try:
        keyring.delete_password(SERVICE_NAME, KEYRING_ACCOUNT)
    except KeyError:
        pass
    except Exception:
        pass


def has_api_key() -> bool:
    """Check whether an API key is stored."""
    return retrieve_api_key() is not None
