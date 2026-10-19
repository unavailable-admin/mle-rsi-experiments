"""One application entrypoint for new experiments."""
import argparse
import asyncio
import json
from . import config,controller,reporting


def main():
    parser=argparse.ArgumentParser(prog='mle-rsi')
    sub=parser.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('--config',required=True);init.add_argument('--host')
    host=sub.add_parser('prepare-host');host.add_argument('--host',required=True);host.add_argument('--output-dir',required=True)
    init.add_argument('--run-dir',required=True)
    for name in ('run','status','stop','pause'):
        p=sub.add_parser(name);p.add_argument('--run-dir',required=True)
    web=sub.add_parser('serve');web.add_argument('--run-dir', required=True)
    web.add_argument('--bind', default='127.0.0.1');web.add_argument('--port', type=int, default=8080)
    workflow=sub.add_parser('workflow');workflow.add_argument('--config', required=True);workflow.add_argument('--run-dir', required=True)
    export=sub.add_parser('export');export.add_argument('--run-dir', required=True);export.add_argument('--task', required=True)
    export.add_argument('--output', required=True);export.add_argument('--through-round', type=int)
    export.add_argument('--campaign');export.add_argument('--public-dir')
    imported=sub.add_parser('import');imported.add_argument('--run-dir', required=True)
    imported.add_argument('--task', required=True);imported.add_argument('--bundle', required=True)
    refresh=sub.add_parser('refresh');refresh.add_argument('--run-dir', required=True)
    refresh.add_argument('--id', required=True);refresh.add_argument('--task')
    budget=sub.add_parser('budget-add');budget.add_argument('--run-dir',required=True)
    budget.add_argument('--id',required=True);budget.add_argument('--dimension',required=True)
    budget.add_argument('--amount',required=True,type=float);budget.add_argument('--reason',required=True)
    budget.add_argument('--task',default='')
    args=parser.parse_args()
    if args.command=='prepare-host':
        from .inventory import prepare_host
        from .io import read
        result=prepare_host(read(args.host),args.output_dir)
    elif args.command=='init':
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
    elif args.command=='serve':
        from .web import serve
        serve(args.run_dir,args.bind,args.port);return
    elif args.command=='workflow':
        from .workflow import execute
        result=asyncio.run(execute(args.config,args.run_dir))
    elif args.command=='export':
        from .migration import export_run
        result=export_run(args.run_dir,args.task,args.output,args.through_round,args.campaign,args.public_dir)
    elif args.command=='import':
        from .migration import import_run
        result=asyncio.run(import_run(args.run_dir,args.task,args.bundle))
    elif args.command=='refresh':
        asyncio.run(controller.refresh(args.run_dir,args.id,args.task));result=reporting.status(args.run_dir)
    elif args.command=='run':
        asyncio.run(controller.run(args.run_dir));result=reporting.status(args.run_dir)
    elif args.command in ('stop','pause'):
        controller.control(args.run_dir,args.command);result={'action':args.command}
    else:result=reporting.status(args.run_dir)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
