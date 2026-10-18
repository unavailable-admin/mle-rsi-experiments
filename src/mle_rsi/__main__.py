"""One application entrypoint for new experiments."""
import argparse
import asyncio
import json
from . import config,controller,reporting


def main():
    parser=argparse.ArgumentParser(prog='mle-rsi')
    sub=parser.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('--config',required=True);init.add_argument('--host')
    init.add_argument('--run-dir',required=True)
    for name in ('run','status','stop','pause'):
        p=sub.add_parser(name);p.add_argument('--run-dir',required=True)
    budget=sub.add_parser('budget-add');budget.add_argument('--run-dir',required=True)
    budget.add_argument('--id',required=True);budget.add_argument('--dimension',required=True)
    budget.add_argument('--amount',required=True,type=float);budget.add_argument('--reason',required=True)
    budget.add_argument('--task',default='')
    args=parser.parse_args()
    if args.command=='init':
        result={'run':str(controller.initialize(args.run_dir,config.load(args.config,args.host))),'state':'initialized'}
    elif args.command=='budget-add':
        from .ledger import Ledger
        from .io import read
        from pathlib import Path
        spec=read(Path(args.run_dir)/'config.json')
        ledger=Ledger(args.run_dir,spec.get('budget',{}))
        try:ledger.add_allowance(args.id,args.dimension,args.amount,args.reason,args.task)
        finally:ledger.close()
        result=reporting.status(args.run_dir)
    elif args.command=='run':
        asyncio.run(controller.run(args.run_dir));result=reporting.status(args.run_dir)
    elif args.command in ('stop','pause'):
        controller.control(args.run_dir,args.command);result={'action':args.command}
    else:result=reporting.status(args.run_dir)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
