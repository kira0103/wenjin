"""Model credentials are resolved by the execution service, never by the client."""
import os

import keyring


def model_key():
    configured = os.environ.get('SHIXUE_MODEL_API_KEY')
    if configured is not None:
        return configured
    try:
        return keyring.get_password('shixue-workbench', 'model-api-key')
    except Exception:
        return None
