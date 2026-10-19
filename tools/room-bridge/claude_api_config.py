"""Claude API desk policy. Changes require source review."""
from decimal import Decimal
from pathlib import Path

MODEL = 'claude-sonnet-5-5'
SDK_VERSION = '0.2.168'
MONTHLY_BUDGET_USD = Decimal('200.00')
RUN_ESTIMATE_USD = Decimal('10.00')
MAX_CONCURRENT = 2
KEYCHAIN_SERVICE = 'carr-model-room-claude-api'
KEYCHAIN_ACCOUNT = 'model-room'
LEDGER_PATH = Path.home() / 'Library/Application Support/CARR/claude-api-ledger.json'
KEY_SETUP = ('security add-generic-password -U -a model-room '
             '-s carr-model-room-claude-api -w')
