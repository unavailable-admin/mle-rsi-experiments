"""Single authority for external API dollars and allocated execution/GPU seconds."""
import contextlib
import math
import sqlite3
import time
from pathlib import Path
from .io import digest
import json


class BudgetExhausted(RuntimeError):
    pass


class EvidenceConflict(RuntimeError):
    pass


class Ledger:
    dimensions = ("api_usd", "execution_seconds", "gpu_seconds")

    def __init__(self, root, limits):
        self.path = Path(root)/"ledger.sqlite"
        self.limits = limits
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, task TEXT, input_hash TEXT, state TEXT, amounts TEXT, evidence TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS allowances (id TEXT PRIMARY KEY, task TEXT, dimension TEXT, amount REAL, reason TEXT)")
        self.db.commit()

    def close(self):
        self.db.close()

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def get(self, ident):
        row = self.db.execute("SELECT task,input_hash,state,amounts,evidence FROM entries WHERE id=?", (ident,)).fetchone()
        return None if row is None else dict(id=ident, task=row[0], input_hash=row[1], state=row[2],
                                             amounts=json.loads(row[3]), evidence=json.loads(row[4]))

    def rows(self):
        return [self.get(row[0]) for row in self.db.execute("SELECT id FROM entries ORDER BY id").fetchall()]

    def _amounts(self, amounts):
        if set(amounts)-set(self.dimensions) or any(type(v) not in (float,int) or not math.isfinite(v) or v<0 for v in amounts.values()):
            raise ValueError("Invalid accounting amount")
        return {key:float(amounts.get(key, 0)) for key in self.dimensions}

    def check(self, task, amounts, exclude=None):
        amounts = self._amounts(amounts)
        rows = [r for r in self.rows() if r['id'] != exclude]
        for key,value in amounts.items():
            total = sum(r['amounts'][key] for r in rows)
            own = sum(r['amounts'][key] for r in rows if r['task']==task)
            cap = self.limits.get('total', {}).get(key)
            task_cap = self.limits.get('tasks', {}).get(task, {}).get(key)
            extra=dict(self.db.execute('SELECT task,SUM(amount) FROM allowances WHERE dimension=? GROUP BY task',(key,)).fetchall())
            if cap is not None:cap+=extra.get('',0)
            if task_cap is not None:task_cap+=extra.get(task,0)
            if cap is not None and total+value>cap or task_cap is not None and own+value>task_cap:
                raise BudgetExhausted(f"{key} ceiling reached, including unknown reservations")

    def reserve(self, ident, task, input_hash, amounts):
        amounts = self._amounts(amounts)
        with self.transaction():
            old = self.get(ident)
            if old is not None:
                if old['task'] != task or old['input_hash'] != input_hash:
                    raise EvidenceConflict("Accounting ID reused for another input")
                if old['state'] != 'not_sent':
                    return old
            self.check(task, amounts, exclude=ident)
            self.db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state, amounts=excluded.amounts",
                (ident,task,input_hash,'reserved',json.dumps(amounts),'{}'))
        return self.get(ident)

    def record(self, ident, state, evidence=None, amounts=None):
        if state not in ('reserved','not_sent','running','unknown','completed','skipped'):
            raise ValueError("Invalid ledger state")
        with self.transaction():
            old = self.get(ident)
            if old is None:
                raise EvidenceConflict("Unreserved operation")
            actual = self._amounts(amounts) if amounts is not None else old['amounts']
            if old['state'] == 'completed' and (state != 'completed' or actual != old['amounts']):
                raise EvidenceConflict("Completed accounting changed")
            if state == 'not_sent':
                actual = self._amounts({})
            self.db.execute("UPDATE entries SET state=?,amounts=?,evidence=? WHERE id=?",
                (state,json.dumps(actual),json.dumps({**old['evidence'],**(evidence or {})}),ident))
        return self.get(ident)

    def add_allowance(self, ident, dimension, amount, reason, task=''):
        if dimension not in self.dimensions or type(amount) not in (int,float) or not math.isfinite(amount) or amount<=0 or not reason:
            raise ValueError('Positive allowance and audit reason required')
        if dimension not in (self.limits.get('tasks',{}).get(task,{}) if task else self.limits.get('total',{})):
            raise ValueError('Cannot extend an unspecified ceiling')
        with self.transaction():
            previous=self.db.execute('SELECT task,dimension,amount,reason FROM allowances WHERE id=?',(ident,)).fetchone()
            wanted=(task,dimension,float(amount),reason)
            if previous is not None and previous!=wanted:raise EvidenceConflict('Allowance ID changed')
            self.db.execute('INSERT OR IGNORE INTO allowances VALUES (?,?,?,?,?)',(ident,*wanted))

    def summary(self):
        rows = self.rows()
        return {'totals':{k:sum(r['amounts'][k] for r in rows) for k in self.dimensions},
                'unsettled':[r['id'] for r in rows if r['state'] in ('reserved','running','unknown')],
                'entries':len(rows)}
