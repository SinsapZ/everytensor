"""Local inference policy. The explicitly invoked installer is separate."""
import os


def configure_offline():
    # Set before importing model libraries, including when Embedder is used by a tool.
    for name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY',
                 'DO_NOT_TRACK', 'HF_HUB_DISABLE_IMPLICIT_TOKEN', 'HF_HUB_DISABLE_UPDATE_CHECK'):
        os.environ[name] = '1'
