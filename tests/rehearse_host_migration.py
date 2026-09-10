"""Run manually on isolated SQLite backups; never takes production paths."""
import argparse
import json
import sqlite3
from pathlib import Path

from api.admin_config.store import AdminConfigStore
from trading_engine_store import TradingEngineStore


def rehearse(root: Path):
    root = root.resolve()
    if root.parent != Path('/tmp') or not root.name.startswith('nifty-v1-rehearsal.'):
        raise ValueError('isolated_rehearsal_directory_required')
    trading, admin_path = root/'trading.db', root/'admin.db'
    if not trading.is_file() or not admin_path.is_file() or trading.is_symlink() or admin_path.is_symlink():
        raise ValueError('regular_backup_files_required')
    def rows():
        with sqlite3.connect(f'file:{trading}?mode=ro',uri=True) as db:
            return db.execute('SELECT trade_id,symbol,qty,status,session_date FROM trades ORDER BY trade_id').fetchall()
    before = rows()
    with sqlite3.connect(f'file:{admin_path}?mode=ro',uri=True) as db:
        saved = db.execute('SELECT v.payload_json FROM admin_config_state s JOIN admin_config_versions v ON s.active_version_id=v.version_id WHERE s.id=1').fetchone()
        prior = json.loads(saved[0])
    store = TradingEngineStore(trading)
    migrated = store.list_trades(None)
    store.close()
    assert rows() == before, 'historical_identity_quantity_status_changed'
    admin = AdminConfigStore(admin_path)
    effective, saved = admin.load_effective_payload(), admin.load_active_payload()
    for key,value in prior.items():
        if key in saved:
            assert saved[key] == value, f'saved_setting_changed:{key}'
    assert saved['daily_loss_cap_inr'] == effective['daily_loss_cap_inr'] == prior['daily_loss_cap_inr']
    admin.close()
    with sqlite3.connect(trading) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    with sqlite3.connect(admin_path) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    return {'status':'passed','historical_rows_preserved':len(before),
            'saved_daily_loss_cap':saved['daily_loss_cap_inr'],
            'open_records':[{'symbol':t.symbol,'qty':t.qty,'status':t.status,
                            'protected_qty':t.protected_qty,'mode_provenance':t.entry_live_orders_enabled}
                            for t in migrated if t.status not in {'closed','skipped','rejected'}],
            'production_databases_modified':False,'broker_calls':0}


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('root',type=Path)
    print(json.dumps(rehearse(parser.parse_args().root),indent=2))
