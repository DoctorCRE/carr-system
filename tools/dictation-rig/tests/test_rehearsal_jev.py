"""The offline rehearsal accepts the Jev SDK request options used by callers."""
import importlib.util
from pathlib import Path

import pytest
from typesafe_sdk import RetryPolicy


def test_rehearsal_jev_checks_the_deal_with_sdk_request_options():
    path = Path(__file__).parent / 'fixtures' / 'rehearsal_jev.py'
    spec = importlib.util.spec_from_file_location('rehearsal_jev', path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    response = fixture.client().system_one(
        state={'item': {'evidence_excerpt': 'Bayside moves to negotiation'},
               'candidate_deals': [{'id': 'bayside', 'name': 'Synthetic Bayside Dental Relocation'}]},
        questions={'deal_match': object(), 'speaker': object()},
        model='jev-1.14.0', retry=RetryPolicy(max_retries=0),
    )
    assert response.answers['deal_match'].choice == 'bayside'
    assert response.answers['deal_match'].confidence == 0.9
    assert response.answers['speaker'].noul == 0.9


@pytest.mark.parametrize('model,retries', [('jev-latest', 0), ('jev-1.14.0', 1)])
def test_rehearsal_rejects_unbudgeted_model_or_hidden_retries(model, retries):
    path = Path(__file__).parent / 'fixtures' / 'rehearsal_jev.py'
    spec = importlib.util.spec_from_file_location('rehearsal_jev_options', path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    with pytest.raises(AssertionError):
        fixture.client().system_one(state={}, questions={}, model=model,
                                    retry=RetryPolicy(max_retries=retries))
