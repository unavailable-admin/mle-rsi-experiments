import pytest
from mle_rsi.ledger import Ledger,BudgetExhausted,EvidenceConflict
from mle_rsi.io import write
from mle_rsi.recovery import execution,OutcomeUnknown


def test_reservations_unknown_and_idempotent_settlement(tmp_path):
    ledger=Ledger(tmp_path,{'total':{'api_usd':1}})
    try:
        ledger.reserve('a','task','input',{'api_usd':.8})
        ledger.record('a','unknown')
        with pytest.raises(BudgetExhausted):ledger.reserve('b','task','other',{'api_usd':.3})
        with pytest.raises(EvidenceConflict):ledger.reserve('a','task','changed',{'api_usd':.8})
        ledger.record('a','completed',amounts={'api_usd':.2})
        ledger.record('a','completed',amounts={'api_usd':.2})
        assert ledger.summary()['totals']['api_usd']==.2
        with pytest.raises(EvidenceConflict):ledger.record('a','completed',amounts={'api_usd':.1})
    finally:ledger.close()


def test_execution_evidence_reconciles_before_repeat(tmp_path):
    ledger=Ledger(tmp_path,{})
    try:
        ledger.reserve('e','task','hash',{'execution_seconds':100,'gpu_seconds':100})
        ledger.record('e','running')
        with pytest.raises(OutcomeUnknown):execution(tmp_path,'e','hash',ledger)
        assert ledger.get('e')['amounts']['gpu_seconds']==100
        write(tmp_path/'execution/resources.json',{'wall_seconds':9,'gpu_allocated_seconds':9,'exit_code':0,'timed_out':False})
        assert execution(tmp_path,'e','hash',ledger)['wall_seconds']==9
        assert ledger.get('e')['amounts']['gpu_seconds']==9
        assert execution(tmp_path,'e','hash',ledger)['wall_seconds']==9
    finally:ledger.close()


def test_allowance_addition_does_not_reset_spending(tmp_path):
    ledger=Ledger(tmp_path,{'total':{'api_usd':1}})
    try:
        ledger.reserve('old','task','h',{'api_usd':1})
        ledger.record('old','unknown')
        ledger.add_allowance('grant','api_usd',2,'explicit test grant')
        ledger.add_allowance('grant','api_usd',2,'explicit test grant')
        assert ledger.get('old')['amounts']['api_usd']==1
        ledger.reserve('new','task','h2',{'api_usd':2})
        with pytest.raises(BudgetExhausted):ledger.reserve('extra','task','h3',{'api_usd':.1})
    finally:ledger.close()
