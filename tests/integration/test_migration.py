import pytest
from mle_rsi.controller import initialize, run, refresh
from mle_rsi.migration import export_run, import_run
from mle_rsi.ledger import Ledger
from mle_rsi.io import read


@pytest.mark.asyncio
async def test_fork_retains_history_costs_and_new_namespace(tmp_path, spec):
    source = initialize(tmp_path / 'source', spec)
    await run(source)
    ledger = Ledger(source, spec['budget'])
    ledger.reserve('unknown-1', 'fixture', 'input-1', {'api_usd': .02})
    ledger.record('unknown-1', 'unknown')
    ledger.close()
    bundle = tmp_path / 'bundle'
    export_run(source, 'fixture', bundle)
    target_spec = {**spec, 'rounds': 2}
    target = initialize(tmp_path / 'target', target_spec)
    receipt = await import_run(target, 'fixture', bundle)
    assert receipt['namespace'] != receipt['source_namespace']
    assert receipt['completed_rounds'] == 1
    assert receipt == await import_run(target, 'fixture', bundle)
    ledger = Ledger(target, spec['budget'])
    assert len(ledger.rows()) == 1 and ledger.rows()[0]['state'] == 'unknown'
    assert ledger.rows()[0]['amounts']['api_usd'] == .02
    ledger.close()
    await refresh(target, 'after-import')
    await run(target)
    status = read(target / 'status/fixture.json')
    assert status['state'] == 'completed'
    assert status['summary']['completed_rounds'] == 2
    assert status['summary']['valid_candidates'] == 8
    rollback = tmp_path / 'rollback'
    export_run(target, 'fixture', rollback, through_round=1)
    # Source's history was not rolled back or modified.
    assert read(target / 'status/fixture.json')['summary']['completed_rounds'] == 2


@pytest.mark.asyncio
async def test_tampered_bundle_is_rejected_before_import(tmp_path, spec):
    source = initialize(tmp_path / 'source', spec)
    await run(source)
    bundle = tmp_path / 'bundle'
    export_run(source, 'fixture', bundle)
    (bundle / 'platform.json').write_text('{}')
    target = initialize(tmp_path / 'target', spec)
    with pytest.raises(Exception, match='content changed'):
        await import_run(target, 'fixture', bundle)


@pytest.mark.asyncio
async def test_occupied_target_rejects_before_inheriting_costs(tmp_path, spec):
    source = initialize(tmp_path / 'source', spec)
    await run(source)
    ledger = Ledger(source, spec['budget'])
    ledger.reserve('source-charge', 'fixture', 'source-input', {'api_usd': .02})
    ledger.close()
    bundle = tmp_path / 'bundle'
    export_run(source, 'fixture', bundle)
    target = initialize(tmp_path / 'target', spec)
    await run(target)
    with pytest.raises(Exception, match='fresh campaign'):
        await import_run(target, 'fixture', bundle)
    ledger = Ledger(target, spec['budget'])
    assert ledger.rows() == []
    ledger.close()


def test_failed_export_does_not_publish_bundle(tmp_path, spec):
    source = initialize(tmp_path / 'source', spec)
    bundle = tmp_path / 'bundle'
    with pytest.raises(Exception):
        export_run(source, 'fixture', bundle)
    assert not bundle.exists()
