"""Claude API desk policy. Changes require source review."""
from decimal import Decimal
from pathlib import Path

MODEL = 'claude-sonnet-5-5'
SDK_VERSION = '0.2.168'
MONTHLY_BUDGET_USD = Decimal('200.00')
RUN_ESTIMATE_USD = Decimal('10.00')
MAX_CONCURRENT = 2
# https://platform.claude.com/docs/en/about-claude/pricing
# Sonnet $2/$10 and Opus $4/$20 per MTok. Charge every input at the
# 2x cache-write rate plus 25% token-count margin. Either run bounds $9.31072.
MODEL_REQUESTS = {'claude-sonnet-5-5': 16, 'claude-opus-5-5': 8}
MODEL_PRICES = {'claude-sonnet-5-5': (Decimal('2'), Decimal('10')),
                'claude-opus-5-5': (Decimal('4'), Decimal('20'))}
MAX_INPUT_TOKENS = 100000
MAX_OUTPUT_TOKENS = 8192
SOFT_BUDGET_USD = Decimal('7.00')


def run_bound_usd(model):
    input_price, output_price = MODEL_PRICES[model]
    return MODEL_REQUESTS[model] * (MAX_INPUT_TOKENS * Decimal('1.25') * 2 * input_price
                                    + MAX_OUTPUT_TOKENS * output_price) / 1000000


KEYCHAIN_SERVICE = 'carr-model-room-claude-api'
KEYCHAIN_ACCOUNT = 'model-room'
LEDGER_PATH = Path.home() / 'Library/Application Support/CARR/claude-api-ledger.json'
KEY_SETUP = ('security add-generic-password -U -a model-room '
             '-s carr-model-room-claude-api -w')
